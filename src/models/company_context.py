"""公司统一上下文模型 — Phase 4 路线B

CompanyContext 作为爬虫数据与 LLM Prompt 之间的结构化中间层，
承载官网、招聘、新闻的完整信息及其溯源 URL，直接注入 Prompt，
不再依赖 ChromaDB metadata 传递 URL。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CompanyContext:
    """公司外部数据的统一结构化上下文

    爬虫完成后的所有外部信息聚合在此对象中，
    由 generator.py 直接消费、注入 Prompt 各节。
    URL 存活在 Python 内存中，不受 ChromaDB 序列化/反序列化影响。
    """

    company: str

    # ── 官网数据 ────────────────────────────────────────────────
    website_url: str = ""
    about_url: str = ""
    website_summary: str = ""  # 首页摘要（2000 字内）
    about_full_text: str = ""  # About 页完整文本（5000 字内）
    products: str = ""         # 产品/服务描述

    # ── 招聘数据 ────────────────────────────────────────────────
    job_count: int = 0
    job_summary: str = ""      # LLM 增强后的招聘摘要
    job_urls: list[str] = field(default_factory=list)  # 职位来源 URL 列表

    # ── 新闻数据 ────────────────────────────────────────────────
    news_count: int = 0
    news_summary: str = ""
    news_urls: list[str] = field(default_factory=list)

    # ── 聚合属性 ────────────────────────────────────────────────

    @property
    def has_any_data(self) -> bool:
        return bool(
            self.website_summary
            or self.job_summary
            or self.news_summary
        )

    @property
    def source_urls(self) -> list[tuple[int, str, str]]:
        """返回 (cite_id, label, url) 列表，用于 prompt 中生成来源索引

        编号从 201 开始（避开内部 Bitable 的 1-200），避免与 RAG 来源编号冲突。
        """
        entries: list[tuple[int, str, str]] = []
        idx = 201

        if self.website_url:
            entries.append((idx, f"{self.company} 官网", self.website_url))
            idx += 1

        for i, url in enumerate(self.job_urls):
            entries.append((idx, f"{self.company} 招聘来源{i + 1}", url))
            idx += 1

        for i, url in enumerate(self.news_urls):
            entries.append((idx, f"{self.company} 新闻来源{i + 1}", url))
            idx += 1

        return entries

    # ── Prompt 格式化 ───────────────────────────────────────────

    def format_for_prompt(self) -> str:
        """将公司上下文格式化为可直接注入 Prompt 的结构化文本块。

        包含：公司介绍、招聘摘要、新闻摘要、来源链接索引。
        每个数据块自带可点击的 URL，LLM 生成报告时可直接引用。
        """
        if not self.has_any_data:
            return ""

        sections: list[str] = [
            "═══════════════════════════════════════════════",
            "【公司外部数据摘要 — 以下信息来自互联网公开数据，已在报告生成前完成爬取】",
            "═══════════════════════════════════════════════",
        ]

        # 1) 官网信息
        if self.website_summary:
            sections.append("")
            sections.append("── 企业官网信息 ──────────────────────────────")
            sections.append(f"来源 URL：{self.website_url}")
            sections.append("")
            sections.append("**企业介绍**：")
            # 优先用 About 页面完整文本，否则用首页摘要
            about = self.about_full_text or self.website_summary
            sections.append(about[:2000])
            if self.products:
                sections.append("")
                sections.append("**产品/服务**：")
                sections.append(self.products[:500])
            sections.append("")
            sections.append(f"[来源201] {self.website_url}")

        # 2) 招聘信息
        if self.job_summary:
            sections.append("")
            sections.append("── 近期招聘信息 ──────────────────────────────")
            sections.append(f"在招职位数：{self.job_count}")
            sections.append("")
            sections.append(self.job_summary[:1500])
            sections.append("")
            for i, url in enumerate(self.job_urls):
                cid = 202 + i
                sections.append(f"[来源{cid}] {url}")

        # 3) 新闻
        if self.news_summary:
            sections.append("")
            sections.append("── 行业/技术新闻 ──────────────────────────────")
            sections.append(self.news_summary[:1000])
            sections.append("")
            base_cid = 202 + len(self.job_urls)
            for i, url in enumerate(self.news_urls):
                cid = base_cid + i
                sections.append(f"[来源{cid}] {url}")

        sections.append("")
        sections.append("═══════════════════════════════════════════════")
        sections.append("请在你的分析中使用以上信息，并在引用处标注来源编号。")
        sections.append("═══════════════════════════════════════════════")

        return "\n".join(sections)

    def get_website_source_url(self) -> str:
        """快捷方法：返回官网 URL（用于 bottom sources 列表）"""
        return self.website_url

    def get_job_source_urls(self) -> list[str]:
        """快捷方法：返回招聘来源 URL 列表"""
        return self.job_urls

    def get_news_source_urls(self) -> list[str]:
        """快捷方法：返回新闻来源 URL 列表"""
        return self.news_urls


def build_company_context(
    company: str,
    web_info: dict[str, Any],
    jobs: list[dict[str, Any]],
    news_items: list[dict[str, Any]],
) -> CompanyContext:
    """从爬虫原始结果构建 CompanyContext

    此函数在爬虫调度器中调用，将三种爬虫的原始返回值聚合成统一结构。

    Args:
        company: 公司名称
        web_info: WebCrawler.crawl_company_website() 返回的 dict
        jobs: JobCrawler.crawl_jobs() 返回的职位列表
        news_items: NewsCrawler.crawl_tech_news() 返回的新闻列表

    Returns:
        填充好的 CompanyContext 实例
    """
    ctx = CompanyContext(company=company)

    # 官网数据
    if web_info:
        ctx.website_url = web_info.get("website_url", "")
        ctx.about_url = web_info.get("about_url", "")
        ctx.website_summary = web_info.get("summary", "")
        ctx.about_full_text = web_info.get("about_full_text", "")
        ctx.products = web_info.get("products", "")

    # 招聘数据
    if jobs:
        ctx.job_count = len(jobs)
        ctx.job_summary = _summarize_jobs(jobs)
        ctx.job_urls = _collect_job_urls(jobs)

    # 新闻数据
    if news_items:
        ctx.news_count = len(news_items)
        ctx.news_summary = _summarize_news(news_items)
        ctx.news_urls = _collect_news_urls(news_items)

    return ctx


def _summarize_jobs(jobs: list[dict[str, Any]]) -> str:
    """从职位列表生成招聘摘要文本

    格式：每行一个职位，包含标题、技术关键词
    """
    lines: list[str] = []
    for i, job in enumerate(jobs[:20], 1):
        title = job.get("title", "未知职位")
        kws = job.get("keywords", [])
        kw_str = "、".join(kws[:5]) if kws else ""
        salary = job.get("salary", "")
        line = f"  {i}. {title}"
        if salary:
            line += f" [{salary}]"
        if kw_str:
            line += f" — 技术栈：{kw_str}"
        lines.append(line)

    if not lines:
        return ""

    return "\n".join(lines)


def _summarize_news(news_items: list[dict[str, Any]]) -> str:
    """从新闻列表生成摘要文本"""
    lines: list[str] = []
    for i, item in enumerate(news_items[:10], 1):
        title = item.get("title", "")
        summary = item.get("summary", "")
        topic = item.get("topic", "")
        line = f"  {i}. {title}"
        if topic:
            line += f" [{topic}]"
        if summary:
            line += f"\n     {summary[:150]}"
        lines.append(line)

    return "\n".join(lines) if lines else ""


def _collect_job_urls(jobs: list[dict[str, Any]]) -> list[str]:
    """从职位列表收集去重 URL"""
    seen: set[str] = set()
    urls: list[str] = []
    for job in jobs:
        url = job.get("source_url", "") or job.get("url", "")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls[:10]


def _collect_news_urls(news_items: list[dict[str, Any]]) -> list[str]:
    """从新闻列表收集去重 URL"""
    seen: set[str] = set()
    urls: list[str] = []
    for item in news_items:
        url = item.get("url", "")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls[:10]
