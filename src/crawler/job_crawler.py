"""招聘数据爬虫 — Phase 3

通过 BOSS直聘搜索指定公司的在招职位，提取职位名称、技术栈、薪资、部门等信息。
通过招聘信息可以反推：
  - 公司正在布局哪些技术方向（AWS/Azure/AI/安全等）
  - 技术团队规模和结构
  - 培训需求切入点（在招岗位对应的认证培训）

注意：招聘平台有反爬机制，这里使用多层降级策略：
  1. 尝试 Playwright（仅本地开发）
  2. 降级为 httpx + 搜索引擎结果解析
  3. 如均失败，返回空列表（不影响主流程）
"""

from __future__ import annotations

import asyncio
import hashlib
import urllib.parse
from typing import Any

from src.crawler.base_crawler import BaseCrawler, clean_text, deduplicate_texts
from src.utils.config import settings
from src.utils.logger import logger


# 技术关键词 → 培训方向映射（用于报告中的推荐语）
TECH_KEYWORD_MAP: dict[str, list[str]] = {
    # AWS
    "aws": ["AWS 认证培训", "云架构师认证", "AWS Solutions Architect"],
    "amazon web services": ["AWS 认证培训"],
    "ec2": ["AWS 云实践培训"],
    "s3": ["AWS 云实践培训"],
    "lambda": ["AWS 云实践培训", "无服务器架构培训"],
    # Azure / 微软
    "azure": ["微软 Azure 认证培训", "AZ-900/AZ-104 认证"],
    "microsoft": ["微软技术培训"],
    "power bi": ["Power BI 数据分析培训"],
    "office 365": ["Microsoft 365 办公自动化培训"],
    "copilot": ["Microsoft Copilot AI 培训"],
    # AI/ML
    "ai": ["AI 应用开发培训", "大模型应用培训"],
    "人工智能": ["AI 应用开发培训"],
    "大模型": ["大模型应用培训", "Prompt Engineering 培训"],
    "llm": ["大模型应用培训"],
    "python": ["Python 数据分析培训", "AI 开发入门培训"],
    "机器学习": ["机器学习培训", "AI 应用开发培训"],
    # 安全
    "网络安全": ["网络安全培训", "等保合规培训"],
    "安全": ["安全意识培训", "等保合规培训"],
    "cisp": ["CISP 认证培训"],
    "cissp": ["CISSP 认证培训"],
    # DevOps / 云原生
    "kubernetes": ["云原生培训", "Kubernetes 认证（CKA）"],
    "k8s": ["云原生培训", "Kubernetes 认证（CKA）"],
    "docker": ["容器技术培训"],
    "devops": ["DevOps 实践培训"],
    "ci/cd": ["DevOps 实践培训"],
    # 数据
    "数据分析": ["数据分析培训"],
    "数仓": ["数据仓库培训"],
    "etl": ["数据工程培训"],
    # 管理
    "pmp": ["PMP 项目管理培训"],
    "项目管理": ["PMP 项目管理培训"],
}


class JobCrawler(BaseCrawler):
    """招聘数据爬虫

    主要通过搜索引擎（Bing/百度）搜索「公司名 site:zhipin.com」，
    解析摘要文本，提取职位信息。
    这样不直接请求招聘平台，稳定性更高。
    """

    async def crawl_jobs(self, company: str) -> list[dict[str, Any]]:
        """爬取指定公司的招聘信息

        Args:
            company: 公司名称（如「九号公司」「联想集团」）

        Returns:
            职位信息列表，每条包含 title/keywords/department/salary 等字段
        """
        logger.info(f"[招聘爬虫] 开始爬取: {company}")
        jobs: list[dict[str, Any]] = []

        # 策略 1：通过 Bing 搜索 BOSS直聘 结果页摘要
        bing_jobs = await self._search_bing_jobs(company)
        jobs.extend(bing_jobs)

        # 策略 2：通过百度搜索（作为备用）
        if len(jobs) < 3:
            baidu_jobs = await self._search_baidu_jobs(company)
            jobs.extend(baidu_jobs)

        if not jobs:
            logger.info(f"[招聘爬虫] {company} 未获取到招聘数据")
            return []

        # 去重
        seen_titles: set[str] = set()
        unique_jobs: list[dict] = []
        for job in jobs:
            title = job.get("title", "").strip()
            if title and title not in seen_titles:
                seen_titles.add(title)
                unique_jobs.append(job)

        logger.info(f"[招聘爬虫] {company} 获取到 {len(unique_jobs)} 条职位信息")
        return unique_jobs[:30]  # 最多 30 条

    async def _search_bing_jobs(self, company: str) -> list[dict[str, Any]]:
        """通过 Bing 搜索获取招聘信息"""
        # 如果有 Bing Search API Key，用 API 搜索（质量更高）
        if settings.bing_search_api_key:
            return await self._bing_api_search(company)

        # 否则通过 httpx 搜索 Bing（不加引号！引号会破坏 Bing 中文分词）
        query = f"{company} 招聘 工程师 技术 (site:zhipin.com OR site:lagou.com OR site:liepin.com)"
        url = "https://cn.bing.com/search"
        params = {"q": query, "count": 20, "mkt": "zh-CN"}
        headers = {
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://cn.bing.com/",
        }
        html = await self.fetch_html(url, headers=headers, params=params)
        if not html:
            return []
        return self._parse_search_results(html, company, source="bing")

    async def _bing_api_search(self, company: str) -> list[dict[str, Any]]:
        """使用 Bing Search API（质量更高，有 key 时优先）"""
        import httpx

        query = f"{company} 招聘技术岗位 工程师"
        headers = {"Ocp-Apim-Subscription-Key": settings.bing_search_api_key}
        params = {
            "q": query,
            "count": 20,
            "mkt": "zh-CN",
            "freshness": "Month",  # 最近一个月
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    settings.bing_search_endpoint,
                    headers=headers,
                    params=params
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning(f"Bing API 搜索失败: {e}")
            return []

        jobs: list[dict[str, Any]] = []
        for item in data.get("webPages", {}).get("value", []):
            title = item.get("name", "")
            snippet = item.get("snippet", "")
            url = item.get("url", "")

            # 从标题和摘要中提取职位信息
            job = self._extract_job_from_text(title + " " + snippet, company)
            if job:
                job["source_url"] = url
                jobs.append(job)

        return jobs

    async def _search_baidu_jobs(self, company: str) -> list[dict[str, Any]]:
        """通过百度搜索招聘信息"""
        query = f"{company} 招聘 工程师 技术"
        url = "https://www.baidu.com/s"
        params = {"wd": query, "rn": 20}
        headers = {
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.baidu.com/",
        }
        html = await self.fetch_html(url, headers=headers, params=params)
        if not html:
            return []
        return self._parse_search_results(html, company, source="baidu")

    def _parse_search_results(
        self, html: str, company: str, source: str = "bing"
    ) -> list[dict[str, Any]]:
        """解析搜索引擎结果页，提取职位相关信息"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        jobs: list[dict[str, Any]] = []

        if source == "bing":
            # Bing 搜索结果：li.b_algo
            results = soup.select("li.b_algo")
        else:
            # 百度搜索结果：div.result
            results = soup.select("div.result, div.c-container")

        for result in results[:15]:
            text = result.get_text(separator=" ", strip=True)
            job = self._extract_job_from_text(text, company)
            if job:
                jobs.append(job)

        return jobs

    def _extract_job_from_text(
        self, text: str, company: str
    ) -> dict[str, Any] | None:
        """从文本中提取职位信息（简单规则提取）"""
        import re

        if not text or len(text) < 10:
            return None

        # 尝试从文本中提取职位名称（常见模式）
        title_patterns = [
            r"(?:招聘|求职|岗位)[：:]\s*(.{3,30}?)(?:\s|$|，|,|\|)",
            r"(.{3,25}?(?:工程师|开发|架构师|专家|经理|总监|主任|运营|分析师|顾问))",
            r"(.{3,20}?(?:Engineer|Developer|Architect|Manager|Analyst))",
        ]

        title = ""
        for pattern in title_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                title = match.group(1).strip()
                # 过滤明显不是职位名的结果
                if len(title) >= 3 and company not in title:
                    break
                title = ""

        # 如果提取不到职位名，但文本中有技术关键词，也记录
        keywords = self._extract_tech_keywords(text)
        if not title and not keywords:
            return None

        # 提取薪资
        salary_match = re.search(
            r"(\d+[\-~至]\d+[kKwW万元/月年]|\d+[kKwW万元/月年])", text
        )
        salary = salary_match.group(0) if salary_match else ""

        return {
            "title": title or "未知职位",
            "keywords": keywords,
            "salary": salary,
            "raw_text": text[:300],
            "training_hints": self._get_training_hints(keywords),
        }

    def _extract_tech_keywords(self, text: str) -> list[str]:
        """从文本中提取技术关键词"""
        text_lower = text.lower()
        found: list[str] = []
        for keyword in TECH_KEYWORD_MAP:
            if keyword.lower() in text_lower:
                found.append(keyword)
        return found[:10]  # 最多 10 个

    def _get_training_hints(self, keywords: list[str]) -> list[str]:
        """根据技术关键词推导培训方向建议"""
        hints: set[str] = set()
        for kw in keywords:
            for training in TECH_KEYWORD_MAP.get(kw.lower(), []):
                hints.add(training)
        return list(hints)[:5]


def jobs_to_rag_document(company: str, jobs: list[dict[str, Any]]) -> str:
    """将职位信息转换为 RAG 文档文本

    Args:
        company: 公司名
        jobs: JobCrawler.crawl_jobs() 的返回值

    Returns:
        适合写入 ChromaDB 的纯文本文档
    """
    if not jobs:
        return ""

    lines: list[str] = [
        f"【{company}】招聘信息分析（外部数据，来源：公开招聘平台）",
        "",
    ]

    # 统计技术关键词频次
    all_keywords: dict[str, int] = {}
    all_hints: set[str] = set()
    for job in jobs:
        for kw in job.get("keywords", []):
            all_keywords[kw] = all_keywords.get(kw, 0) + 1
        for hint in job.get("training_hints", []):
            all_hints.add(hint)

    # 高频关键词
    top_keywords = sorted(all_keywords.items(), key=lambda x: -x[1])[:10]
    if top_keywords:
        lines.append(
            "核心技术方向：" + "、".join([f"{k}（{v}个职位）" for k, v in top_keywords])
        )
        lines.append("")

    # 培训机会提示
    if all_hints:
        lines.append("潜在培训需求（基于招聘技术关键词推导）：")
        for hint in sorted(all_hints):
            lines.append(f"  - {hint}")
        lines.append("")

    # 职位列表摘要
    lines.append(f"在招职位（共 {len(jobs)} 条）：")
    for i, job in enumerate(jobs[:15], 1):  # 最多 15 条
        title = job.get("title", "未知职位")
        salary = job.get("salary", "")
        kws = "、".join(job.get("keywords", [])[:5])
        line = f"  {i}. {title}"
        if salary:
            line += f"（{salary}）"
        if kws:
            line += f" | 技术：{kws}"
        lines.append(line)

    return "\n".join(lines)


def jobs_to_chunks(
    company: str,
    jobs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """将职位数据转换为 ChromaDB chunk 格式

    Args:
        company: 公司名
        jobs: 职位信息列表

    Returns:
        适合 add_chunks() 的 chunk 列表
    """
    if not jobs:
        return []

    doc_text = jobs_to_rag_document(company, jobs)
    if not doc_text:
        return []

    chunk_id = hashlib.md5(
        f"jobs_{company}_{doc_text[:100]}".encode()
    ).hexdigest()[:16]

    return [
        {
            "chunk_id": f"ext_jobs_{chunk_id}",
            "content": doc_text,
            "metadata": {
                "source": "external_jobs",
                "company": company,
                "job_count": str(len(jobs)),
                "data_type": "recruitment",
            },
        }
    ]
