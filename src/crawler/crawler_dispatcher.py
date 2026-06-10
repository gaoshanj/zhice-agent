"""外部数据爬取调度器 — Phase 3

统一入口：给定公司名，同时触发招聘爬虫+官网爬虫，
结果写入 ChromaDB external_docs 集合，供 RAG 检索使用。

设计原则：
- 爬取结果有时效（默认 7 天），过期才重新爬（避免频繁请求）
- 整个爬取过程有超时上限（默认 40 秒），超时不影响报告生成
- 所有错误静默处理，爬取失败不影响内部知识库 RAG
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime, timezone
from typing import Any

from src.crawler.job_crawler import JobCrawler, jobs_to_chunks
from src.crawler.web_crawler import WebCrawler, website_info_to_chunks
from src.crawler.bitable_writer import write_crawl_result
from src.rag.vector_store import add_chunks, similarity_search, collection_count
from src.utils.config import settings
from src.utils.logger import logger


# 内存缓存：记录最近已爬取的公司和时间（避免同一会话内重复爬取）
_crawl_cache: dict[str, float] = {}
CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 天


def _is_recently_crawled(company: str) -> bool:
    """检查该公司是否最近已爬取过（内存缓存）"""
    key = hashlib.md5(company.encode()).hexdigest()[:8]
    last_crawl = _crawl_cache.get(key, 0)
    return (time.monotonic() - last_crawl) < 300  # 同一进程内 5 分钟内不重复爬


def _mark_crawled(company: str) -> None:
    """标记公司已爬取"""
    key = hashlib.md5(company.encode()).hexdigest()[:8]
    _crawl_cache[key] = time.monotonic()


def _has_external_data(company: str) -> bool:
    """检查 ChromaDB 中是否已有该公司的外部数据"""
    try:
        results = similarity_search(
            query=company,
            top_k=1,
            collection_name=settings.chroma_collection_external,
            filter_dict={"company": company},
        )
        return bool(results)
    except Exception:
        return False


async def crawl_and_store(
    company: str,
    force: bool = False,
    timeout: float = 40.0,
) -> dict[str, Any]:
    """爬取公司外部数据并写入向量库

    Args:
        company: 公司名称
        force: 强制重新爬取（忽略缓存）
        timeout: 整体超时秒数（超时后返回当前已完成的结果）

    Returns:
        {
            "company": str,
            "jobs_count": int,       # 爬取到的职位数
            "website_found": bool,   # 是否找到官网
            "chunks_stored": int,    # 写入向量库的 chunk 数
            "bitable_written": int,  # 写入飞书 Bitable 的记录数
            "elapsed": float,        # 耗时秒数
            "errors": list[str],     # 错误信息（如有）
            "bitable_errors": list[str],  # Bitable 写入错误
        }
    """
    start = time.monotonic()
    result: dict[str, Any] = {
        "company": company,
        "jobs_count": 0,
        "website_found": False,
        "chunks_stored": 0,
        "bitable_written": 0,
        "elapsed": 0.0,
        "errors": [],
        "bitable_errors": [],
    }

    # 缓存检查（同进程短时间内不重复爬）
    if not force and _is_recently_crawled(company):
        logger.info(f"[爬虫调度] {company} 最近已爬取，跳过（内存缓存）")
        result["elapsed"] = time.monotonic() - start
        return result

    logger.info(f"[爬虫调度] 开始外部数据爬取: {company}（超时 {timeout}s）")
    _mark_crawled(company)

    all_chunks: list[dict[str, Any]] = []

    # 保存原始爬取结果，用于后续写入 Bitable
    raw_jobs: list[dict[str, Any]] = []
    raw_web_info: dict[str, Any] = {}

    # ── 并发运行招聘爬虫 + 官网爬虫 ──────────────────────────────
    async def run_job_crawler() -> None:
        nonlocal raw_jobs
        try:
            crawler = JobCrawler()
            jobs = await crawler.crawl_jobs(company)
            if jobs:
                raw_jobs = jobs
                result["jobs_count"] = len(jobs)
                chunks = jobs_to_chunks(company, jobs)
                all_chunks.extend(chunks)
                logger.info(f"[爬虫调度] 招聘数据: {len(jobs)} 条职位，{len(chunks)} 个 chunk")
        except Exception as e:
            msg = f"招聘爬虫失败: {e}"
            logger.warning(f"[爬虫调度] {msg}")
            result["errors"].append(msg)

    async def run_web_crawler() -> None:
        nonlocal raw_web_info
        try:
            crawler = WebCrawler()
            info = await crawler.crawl_company_website(company)
            raw_web_info = info
            if info.get("website_url"):
                result["website_found"] = True
            chunks = website_info_to_chunks(company, info)
            all_chunks.extend(chunks)
            logger.info(
                f"[爬虫调度] 官网数据: {'找到' if result['website_found'] else '未找到'}，"
                f"{len(chunks)} 个 chunk"
            )
        except Exception as e:
            msg = f"官网爬虫失败: {e}"
            logger.warning(f"[爬虫调度] {msg}")
            result["errors"].append(msg)

    try:
        await asyncio.wait_for(
            asyncio.gather(run_job_crawler(), run_web_crawler()),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(f"[爬虫调度] {company} 爬取超时（{timeout}s）")
        result["errors"].append(f"爬取超时（{timeout}s）")

    # ── 写入向量库 ─────────────────────────────────────────────
    if all_chunks:
        try:
            stored = add_chunks(
                chunks=all_chunks,
                collection_name=settings.chroma_collection_external,
            )
            result["chunks_stored"] = stored
            logger.info(f"[爬虫调度] 写入 external_docs: {stored} 个 chunk")
        except Exception as e:
            msg = f"写入向量库失败: {e}"
            logger.error(f"[爬虫调度] {msg}")
            result["errors"].append(msg)
    else:
        logger.info(f"[爬虫调度] {company} 无数据写入")

    # ── 写入飞书 Bitable ─────────────────────────────────────
    # 以 URL 为唯一键去重，避免重复抓取相同网页
    result["bitable_written"] = 0
    result["bitable_errors"] = []

    # 官网数据写入 Bitable
    if raw_web_info and raw_web_info.get("website_url"):
        try:
            outcome = await write_crawl_result(
                company=company,
                url=raw_web_info["website_url"],
                summary=raw_web_info.get("summary", "")[:5000],
                source_type="官网",
            )
            if outcome["written"]:
                result["bitable_written"] += 1
            elif outcome.get("error"):
                result["bitable_errors"].append(f"官网写入: {outcome['error']}")
        except Exception as e:
            logger.warning(f"[爬虫调度] Bitable 官网写入异常: {e}")

    # 招聘数据逐条写入 Bitable
    for job in raw_jobs:
        job_url = job.get("url", "")
        if not job_url:
            continue
        try:
            summary_parts = []
            if job.get("title"):
                summary_parts.append(f"职位: {job['title']}")
            if job.get("company"):
                summary_parts.append(f"公司: {job['company']}")
            if job.get("description"):
                summary_parts.append(job["description"][:3000])

            outcome = await write_crawl_result(
                company=company,
                url=job_url,
                summary="\n".join(summary_parts),
                source_type="招聘",
            )
            if outcome["written"]:
                result["bitable_written"] += 1
            elif outcome.get("error"):
                result["bitable_errors"].append(f"招聘写入({job_url[:40]}): {outcome['error']}")
        except Exception as e:
            logger.warning(f"[爬虫调度] Bitable 招聘写入异常: {e}")

    if result["bitable_written"]:
        logger.info(f"[爬虫调度] Bitable 写入: {result['bitable_written']} 条")

    result["elapsed"] = round(time.monotonic() - start, 1)
    logger.info(
        f"[爬虫调度] {company} 完成，耗时 {result['elapsed']}s，"
        f"chunks={result['chunks_stored']}，errors={len(result['errors'])}"
    )
    return result
