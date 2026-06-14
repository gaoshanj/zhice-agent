"""企业技术新闻爬虫 — Phase 3 Enhancement

搜索每个公司的 IT/AI/Cloud/Big Data 相关新闻。
目的：让报告中的「市场动态」部分有实时的外部数据支撑。

搜索策略：
  1. Bing News 搜索（优先，`tbm=nws`）
  2. 常规 Bing 搜索 + 新闻关键词降级
  3. 百度备用

输出：
  - 新闻标题、URL、摘要、来源时间
  - 技术关键词标签
"""

from __future__ import annotations

import hashlib
import urllib.parse
from typing import Any

from src.crawler.base_crawler import BaseCrawler, clean_text, extract_core_company_name
from src.utils.config import settings
from src.utils.logger import logger

# IT/AI/Cloud/BigData 搜索主题关键词
TECH_NEWS_TOPICS = [
    "人工智能 AI 技术",
    "云计算 数字化转型",
    "大数据 数据分析",
    "IT技术 信息安全",
]

# 招聘信息的内容关键词（帮助判断是否误抓招聘）
JOB_INDICATORS = [
    "招聘", "薪资", "职位描述", "任职要求", "senior", "junior",
    "五险一金", "年终奖", "股票期权", "职位类别",
]


class NewsCrawler(BaseCrawler):
    """企业技术新闻爬虫"""

    async def crawl_tech_news(self, company: str) -> list[dict[str, Any]]:
        """爬取指定公司的技术相关新闻

        Args:
            company: 公司名称

        Returns:
            新闻列表，每条含 title/url/summary/source/topic
        """
        logger.info(f"[新闻爬虫] 开始爬取: {company}")
        all_news: list[dict[str, Any]] = []

        # 长公司名截断为搜索友好的短名
        search_name = extract_core_company_name(company)
        if search_name != company:
            logger.info(f"[新闻爬虫] 搜索名: {company} → {search_name}")

        for topic in TECH_NEWS_TOPICS:
            # 策略 1：Bing News 搜索
            news = await self._search_bing_news(search_name, topic)
            all_news.extend(news)

            # 策略 2：常规搜索（如果新闻太少）
            if len(all_news) < 3:
                news2 = await self._search_bing_general(search_name, topic)
                all_news.extend(news2)

        if not all_news:
            logger.info(f"[新闻爬虫] {company} 未获取到相关新闻")
            return []

        # 去重（按 URL）
        seen_urls: set[str] = set()
        unique: list[dict[str, Any]] = []
        for n in all_news:
            url = n.get("url", "").strip()
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique.append(n)

        # 过滤掉明显的招聘信息和百度推广 + 非新闻类页面
        filtered = [
            n for n in unique
            if not self._is_job_posting(n) and self._is_likely_news(n)
        ]

        logger.info(
            f"[新闻爬虫] {company} 获取到 {len(filtered)} 条技术新闻"
        )
        return filtered[:20]

    async def _search_bing_news(
        self, company: str, topic: str
    ) -> list[dict[str, Any]]:
        """Bing News 专用搜索"""
        query = f"{company} {topic}"
        # 使用 Bing News 搜索（而非 tbm=nws 参数）
        url = "https://cn.bing.com/news/search"
        params = {"q": query, "qft": "interval=\"7\"", "form": "YFNR"}
        html = await self.fetch_html(url, params=params)
        if not html:
            return []
        return self._parse_bing_news_results(html, company, topic)

    async def _search_bing_general(
        self, company: str, topic: str
    ) -> list[dict[str, Any]]:
        """常规 Bing 搜索作为新闻辅助"""
        # 更聚焦的搜索词
        topic_short = topic.split()[0]  # 取第一个词（如"人工智能"）
        query = f"{company} {topic_short} 最新 动态"
        url = "https://cn.bing.com/search"
        params = {"q": query, "mkt": "zh-CN"}
        html = await self.fetch_html(url, params=params)
        if not html:
            return []
        return self._parse_bing_news_results(html, company, topic)

    def _parse_bing_news_results(
        self, html: str, company: str, topic: str
    ) -> list[dict[str, Any]]:
        """解析 Bing 搜索结果页中的新闻条目"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        news_list: list[dict[str, Any]] = []

        # Bing News 页面专用选择器
        results = soup.select(".news-card, article.newsitem, .nwscrd")
        if not results:
            # Fallback 到通用搜索结果选择器
            results = soup.select("li.b_algo, div.b_caption")

        for item in results[:10]:
            # 提取链接和标题
            a_tag = item.select_one("h2 a, .title a, a.title, a[href]")
            if not a_tag:
                continue
            href = a_tag.get("href", "")
            title = a_tag.get_text(strip=True)

            # 跳过百度推广链接
            if any(d in href.lower() for d in [
                "baidu.com/link", "baidu.com/s?", "/s?wd=",
            ]):
                continue

            # 跳过百科、字典、招聘类链接
            if any(d in href.lower() for d in [
                "baike.baidu.com", "zidian.", "hanyuguoxue",
                "zhipin.com/job", "lagou.com/jobs", "51job.com",
            ]):
                continue

            # 提取摘要
            snippet_tag = item.select_one(
                ".snippet, .b_snippet, .news_snippet, p"
            )
            snippet = snippet_tag.get_text(separator=" ", strip=True) if snippet_tag else ""

            # 提取来源和时间
            source_tag = item.select_one(".source, .news_source, cite")
            source = source_tag.get_text(strip=True) if source_tag else ""

            if title and len(title) >= 5:
                news_list.append({
                    "title": title[:200],
                    "url": href,
                    "summary": snippet[:500],
                    "source": source,
                    "topic": topic,
                })

        return news_list

    def _is_job_posting(self, news: dict[str, Any]) -> bool:
        """判断是否为招聘信息（而非真正的新闻）"""
        title = news.get("title", "").lower()
        summary = news.get("summary", "").lower()
        combined = f"{title} {summary}"
        for indicator in JOB_INDICATORS:
            if indicator.lower() in combined:
                return True
        return False

    @staticmethod
    def _is_likely_news(news: dict[str, Any]) -> bool:
        """判断是否更像新闻而非主页/产品页"""
        title = news.get("title", "")
        url = news.get("url", "").lower()
        summary = news.get("summary", "")

        # URL 以斜杠结尾且没有路径→很可能是首页
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.rstrip("/")
        if not path or path in ["/cn", "/en", "/index", "/index.html", "/index.htm"]:
            return False

        # 纯公司名作为标题 → 可能是首页
        if len(title) < 8 and not any(k in title for k in ["新闻", "发布", "推出", "宣布"]):
            return False

        # 包含新闻特征词 → 更像是新闻
        news_keywords = ["新闻", "发布", "推出", "宣布", "合作", "签约", "上线", "完成",
                         "获", "融资", "投资", "创新", "突破", "增长", "季度"]
        if any(k in title for k in news_keywords):
            return True

        # 有摘要内容且包含描述性文字 → 可能是新闻
        if summary and len(summary) > 30:
            return True

        return False


def news_to_chunks(
    company: str,
    news_list: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """将新闻列表转换为 ChromaDB chunk 格式

    Args:
        company: 公司名
        news_list: NewsCrawler.crawl_tech_news() 的返回值

    Returns:
        适合 add_chunks() 的 chunk 列表
    """
    if not news_list:
        return []

    chunks: list[dict[str, Any]] = []

    # 按主题分组，每个主题一个 chunk
    topics: dict[str, list[dict]] = {}
    for n in news_list:
        topic = n.get("topic", "其他")
        topics.setdefault(topic, []).append(n)

    for topic, items in topics.items():
        lines = [
            f"【{company}】技术新闻（外部数据，主题：{topic}）",
            f"共 {len(items)} 条相关新闻：",
            "",
        ]
        for i, item in enumerate(items[:8], 1):
            title = clean_text(item.get("title", "")[:120])
            summary = clean_text(item.get("summary", "")[:200])
            source = item.get("source", "")
            lines.append(f"  {i}. {title}")
            if summary:
                lines.append(f"     {summary}")
            if source:
                lines.append(f"     来源：{source}")
            lines.append("")

        doc = "\n".join(lines)
        if len(doc) > 50:
            chunk_id = hashlib.md5(
                f"news_{company}_{topic}".encode()
            ).hexdigest()[:16]

            # 收集新闻 URL（用于溯源链接）
            news_urls: list[str] = []
            for item in items:
                url = item.get("url", "")
                if url and url not in news_urls:
                    news_urls.append(url)

            metadata: dict[str, Any] = {
                "source": "external_news",
                "company": company,
                "news_count": str(len(items)),
                "topic": topic,
                "data_type": "tech_news",
            }
            if news_urls:
                metadata["url"] = news_urls[0]

            chunks.append({
                "chunk_id": f"ext_news_{chunk_id}",
                "content": doc,
                "metadata": metadata,
            })

    return chunks
