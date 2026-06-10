"""官网信息爬虫 — Phase 3

抓取公司官网的基本信息：公司简介、产品线、最新动态等。
目的：让 Section 1（客户快照）能展示更丰富的公司背景信息。

爬取流程：
  1. 通过 Bing/百度搜索「公司名 官网」，确定官网 URL
  2. 抓取首页 + /about / /products 等子页面
  3. 提取核心文本，去噪 → 写入 external_docs
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from typing import Any

import httpx
from bs4 import BeautifulSoup

from src.crawler.base_crawler import BaseCrawler, clean_text, truncate_text
from src.utils.config import settings
from src.utils.logger import logger


# 可能包含重要信息的页面路径关键词
USEFUL_PATH_KEYWORDS = [
    "about", "about-us", "about_us", "关于", "简介", "公司介绍",
    "product", "products", "solution", "solutions", "service", "services",
    "产品", "解决方案", "服务",
    "news", "blog", "press", "新闻", "动态", "公告",
]

# 无用的 meta 标签 / 链接
NOISE_PATTERNS = [
    r"^\s*$",
    r"^copyright",
    r"^©",
    r"^all rights reserved",
    r"^联系我们$",
    r"^首页$",
    r"^返回顶部$",
]


class WebCrawler(BaseCrawler):
    """公司官网爬虫"""

    async def crawl_company_website(
        self, company: str
    ) -> dict[str, Any]:
        """爬取公司官网信息

        Args:
            company: 公司名称

        Returns:
            {
                "company": str,
                "website_url": str,
                "summary": str,       # 公司简介（首页提取）
                "products": str,      # 产品/服务描述
                "news": list[str],    # 最新动态（标题列表）
                "raw_pages": list,    # 原始页面数据
            }
        """
        logger.info(f"[官网爬虫] 开始爬取: {company}")

        # Step 1: 找到官网 URL
        website_url = await self._find_website_url(company)
        if not website_url:
            logger.info(f"[官网爬虫] 未找到 {company} 的官网 URL")
            return {"company": company, "website_url": "", "summary": "",
                    "products": "", "news": [], "raw_pages": []}

        logger.info(f"[官网爬虫] 找到官网: {website_url}")

        # Step 2: 抓取首页
        homepage_data = await self._crawl_page(website_url, "homepage")

        # Step 3: 从首页找子页面链接（About/Products/News）
        sub_urls = self._find_useful_links(
            homepage_data.get("html", ""),
            base_url=website_url,
        )

        # Step 4: 抓取子页面（限制 3 个，避免太慢）
        sub_pages: list[dict] = []
        for url, label in sub_urls[:3]:
            page_data = await self._crawl_page(url, label)
            if page_data.get("text"):
                sub_pages.append(page_data)

        # Step 5: 汇总提取内容
        all_pages = [homepage_data] + sub_pages
        result = self._extract_company_info(company, website_url, all_pages)
        logger.info(
            f"[官网爬虫] {company} 爬取完成，摘要 {len(result.get('summary',''))} 字"
        )
        return result

    async def _find_website_url(self, company: str) -> str | None:
        """通过搜索引擎定位公司官网 URL"""
        # 如果有 Bing API 优先使用
        if settings.bing_search_api_key:
            url = await self._bing_api_find_website(company)
            if url:
                return url

        # 否则通过 Bing 搜索页面解析
        url = await self._bing_search_website(company)
        if url:
            return url

        # 百度备用
        return await self._baidu_search_website(company)

    async def _bing_api_find_website(self, company: str) -> str | None:
        """使用 Bing Search API 找官网"""
        query = f"{company} 官网"
        headers = {"Ocp-Apim-Subscription-Key": settings.bing_search_api_key}
        params = {"q": query, "count": 5, "mkt": "zh-CN"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    settings.bing_search_endpoint,
                    headers=headers,
                    params=params
                )
                resp.raise_for_status()
                data = resp.json()
            for item in data.get("webPages", {}).get("value", []):
                url = item.get("url", "")
                if self._looks_like_official_website(url, company):
                    return url
        except Exception as e:
            logger.warning(f"Bing API 找官网失败: {e}")
        return None

    async def _bing_search_website(self, company: str) -> str | None:
        """通过 Bing 搜索页面找官网"""
        query = f"{company} 官方网站"
        url = "https://cn.bing.com/search"
        params = {"q": query, "mkt": "zh-CN"}
        html = await self.fetch_html(url, params=params)
        if not html:
            return None
        return self._extract_first_url_from_search(html, company)

    async def _baidu_search_website(self, company: str) -> str | None:
        """通过百度搜索找官网"""
        query = f"{company} 官网"
        url = "https://www.baidu.com/s"
        params = {"wd": query}
        html = await self.fetch_html(url, params=params)
        if not html:
            return None
        return self._extract_first_url_from_search(html, company, source="baidu")

    def _extract_first_url_from_search(
        self, html: str, company: str, source: str = "bing"
    ) -> str | None:
        """从搜索结果 HTML 中提取第一个官网 URL"""
        soup = BeautifulSoup(html, "lxml")
        if source == "bing":
            results = soup.select("li.b_algo h2 a, li.b_algo .b_title a")
        else:
            results = soup.select("h3.t a, .result a[href]")

        for a in results:
            href = a.get("href", "")
            if href.startswith("http") and self._looks_like_official_website(href, company):
                return href
            # 处理百度跳转链接
            if "baidu.com/link" in href:
                continue
        return None

    def _looks_like_official_website(self, url: str, company: str) -> bool:
        """判断 URL 是否像官网（而非招聘/新闻/政府网站）"""
        if not url or not url.startswith("http"):
            return False
        # 排除明显非官网的域名
        exclude_domains = [
            "baidu.com", "bing.com", "google.com",
            "zhipin.com", "lagou.com", "liepin.com", "51job.com",
            "zhaopin.com", "qichacha.com", "tianyancha.com",
            "weibo.com", "weixin.qq.com", "zhihu.com",
            "news.sina.com", "163.com/dy",
            "wikipedia.org", "gov.cn",
            "linkedin.com", "twitter.com",
        ]
        for d in exclude_domains:
            if d in url:
                return False
        return True

    async def _crawl_page(self, url: str, label: str = "page") -> dict[str, Any]:
        """爬取单个页面，返回标准化数据"""
        html = await self.fetch_html(url)
        if not html:
            return {"url": url, "label": label, "html": "", "text": ""}
        text = self.extract_text(html)
        return {
            "url": url,
            "label": label,
            "html": html,
            "text": truncate_text(text, 3000),  # 单页最多 3000 字
        }

    def _find_useful_links(
        self, html: str, base_url: str
    ) -> list[tuple[str, str]]:
        """从首页 HTML 中找到有价值的子页面链接（About/Products/News等）"""
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        base_domain = urllib.parse.urlparse(base_url).netloc
        found: list[tuple[str, str]] = []
        seen_urls: set[str] = set()

        for a in soup.find_all("a", href=True):
            href = a.get("href", "").strip()
            text = a.get_text(strip=True).lower()

            # 补全相对路径
            if href.startswith("/"):
                href = f"{urllib.parse.urlparse(base_url).scheme}://{base_domain}{href}"
            elif not href.startswith("http"):
                continue

            # 只取同域名的链接
            link_domain = urllib.parse.urlparse(href).netloc
            if link_domain != base_domain:
                continue

            # 判断是否为有价值的页面
            path = urllib.parse.urlparse(href).path.lower()
            label = self._categorize_link(path, text)
            if label and href not in seen_urls:
                seen_urls.add(href)
                found.append((href, label))

        # 按优先级排序：about > products > news
        priority = {"about": 0, "products": 1, "news": 2}
        found.sort(key=lambda x: priority.get(x[1], 99))
        return found[:5]

    def _categorize_link(self, path: str, text: str) -> str | None:
        """判断链接类型"""
        about_kws = ["about", "关于", "简介", "公司"]
        product_kws = ["product", "solution", "service", "产品", "解决方案", "服务"]
        news_kws = ["news", "blog", "press", "新闻", "动态", "公告"]

        combined = path + " " + text
        if any(k in combined for k in about_kws):
            return "about"
        if any(k in combined for k in product_kws):
            return "products"
        if any(k in combined for k in news_kws):
            return "news"
        return None

    def _extract_company_info(
        self, company: str, website_url: str, pages: list[dict]
    ) -> dict[str, Any]:
        """从多个页面数据中提取结构化公司信息"""
        homepage_text = ""
        about_text = ""
        product_text = ""
        news_titles: list[str] = []

        for page in pages:
            label = page.get("label", "")
            text = page.get("text", "")
            if not text:
                continue

            if label == "homepage":
                homepage_text = text[:2000]
            elif label == "about":
                about_text = text[:2000]
            elif label == "products":
                product_text = text[:2000]
            elif label == "news":
                # 提取新闻标题（短句）
                for line in text.splitlines():
                    line = line.strip()
                    if 10 < len(line) < 80 and not any(
                        re.search(p, line, re.I) for p in NOISE_PATTERNS
                    ):
                        news_titles.append(line)

        # 优先用 about 页面作为摘要
        summary_text = about_text or homepage_text
        summary = self._extract_summary(summary_text, company)

        return {
            "company": company,
            "website_url": website_url,
            "summary": summary,
            "products": truncate_text(product_text, 1000),
            "news": news_titles[:5],
            "raw_pages": [
                {"url": p["url"], "label": p["label"]} for p in pages
            ],
        }

    def _extract_summary(self, text: str, company: str) -> str:
        """从文本中提取公司简介（找包含公司名的关键段落）"""
        if not text:
            return ""

        lines = text.splitlines()
        relevant: list[str] = []
        company_short = company[:4]  # 用前4字匹配

        for line in lines:
            line = line.strip()
            if len(line) < 15 or len(line) > 300:
                continue
            # 找包含公司名或关键描述词的行
            if (company_short in line
                    or any(k in line for k in ["成立", "创立", "专注", "致力", "领先",
                                                "提供", "服务", "客户", "产品", "解决方案"])):
                relevant.append(line)

        if not relevant:
            # 退而求其次：取前 200 字
            return truncate_text(text, 200)

        return "\n".join(relevant[:6])


def website_info_to_chunks(
    company: str,
    info: dict[str, Any],
) -> list[dict[str, Any]]:
    """将官网信息转换为 ChromaDB chunk 格式

    Args:
        company: 公司名
        info: WebCrawler.crawl_company_website() 的返回值

    Returns:
        适合 add_chunks() 的 chunk 列表
    """
    chunks: list[dict[str, Any]] = []

    # Chunk 1：公司概况 + 产品摘要
    summary_parts: list[str] = [
        f"【{company}】官网信息（外部数据，来源：公司官网 {info.get('website_url', '')}）",
        "",
    ]
    if info.get("summary"):
        summary_parts.append("公司简介：")
        summary_parts.append(info["summary"])
        summary_parts.append("")
    if info.get("products"):
        summary_parts.append("产品/服务：")
        summary_parts.append(truncate_text(info["products"], 500))
        summary_parts.append("")
    if info.get("news"):
        summary_parts.append("近期动态：")
        for n in info["news"][:3]:
            summary_parts.append(f"  - {n}")

    summary_doc = "\n".join(summary_parts)
    if len(summary_doc) > 50:
        chunk_id = hashlib.md5(
            f"web_{company}_{summary_doc[:80]}".encode()
        ).hexdigest()[:16]
        chunks.append({
            "chunk_id": f"ext_web_{chunk_id}",
            "content": summary_doc,
            "metadata": {
                "source": "external_website",
                "company": company,
                "website_url": info.get("website_url", ""),
                "data_type": "company_profile",
            },
        })

    return chunks
