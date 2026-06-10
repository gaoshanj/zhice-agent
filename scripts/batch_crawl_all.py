"""批量爬取所有公司 — Phase 3 Enhancement

从飞书 Bitable 知识库读取全部公司名，逐公司运行爬虫，
结果写入 ChromaDB + Bitable。适用于本地手动执行或 Azure 定时任务。

用法：
    python scripts/batch_crawl_all.py          # 正常模式（缓存优先）
    python scripts/batch_crawl_all.py --force  # 强制重新爬取
    python scripts/batch_crawl_all.py --dry-run # 仅列出公司不做爬取
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from dotenv import load_dotenv

load_dotenv()

from src.utils.config import settings
from src.utils.logger import logger
from src.crawler.crawler_dispatcher import crawl_and_store


def get_all_companies() -> list[str]:
    """从飞书 Bitable 读取所有公司名"""
    url = "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal"
    r = httpx.post(url, json={
        "app_id": settings.feishu_app_id,
        "app_secret": settings.feishu_app_secret,
    }, timeout=10)
    r.raise_for_status()
    token = r.json().get("app_access_token", "")
    if not token:
        raise RuntimeError("获取飞书 app_access_token 失败")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    companies: list[str] = []
    page_token = None
    base_token = settings.feishu_bitable_base_token
    table_id = settings.feishu_bitable_table_id

    while True:
        params: dict = {"page_size": 50}
        if page_token:
            params["page_token"] = page_token

        r2 = httpx.get(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base_token}/tables/{table_id}/records",
            params=params, headers=headers, timeout=30
        )
        data = r2.json()
        items = data.get("data", {}).get("items", [])

        for rec in items:
            val = rec.get("fields", {}).get("公司全称", [])
            # 处理嵌套文本格式 [{"text": "公司名", "type": "text"}]
            if isinstance(val, list) and val:
                if isinstance(val[0], dict):
                    company = val[0].get("text", "")
                else:
                    company = str(val[0])
            else:
                company = str(val) if val else ""
            if company and company.strip():
                companies.append(company.strip())

        if not data.get("data", {}).get("has_more"):
            break
        page_token = data.get("data", {}).get("page_token")

    return companies


async def batch_crawl(
    companies: list[str],
    force: bool = False,
    delay_between: float = 5.0,
    timeout_per_company: float = 60.0,
) -> dict:
    """逐公司爬取，汇总结果

    Args:
        companies: 公司名列表
        force: 是否强制重新爬取
        delay_between: 公司间的延迟秒数（避免请求过快）
        timeout_per_company: 每公司的超时秒数

    Returns:
        汇总统计
    """
    total = len(companies)
    summary = {
        "total": total,
        "success": 0,
        "failed": 0,
        "total_jobs": 0,
        "total_websites": 0,
        "total_news": 0,
        "total_chunks": 0,
        "total_bitable": 0,
        "elapsed_secs": 0.0,
        "details": [],
    }

    start_all = time.monotonic()
    print(f"\n{'='*60}")
    print(f"  批量爬取启动：{total} 家公司")
    print(f"  force={force}, delay={delay_between}s, timeout={timeout_per_company}s")
    print(f"{'='*60}\n")

    for i, company in enumerate(companies, 1):
        print(f"\n[{i}/{total}] 正在爬取：{company} ...")
        try:
            result = await crawl_and_store(
                company=company,
                force=force,
                timeout=timeout_per_company,
            )
            summary["total_jobs"] += result.get("jobs_count", 0)
            summary["total_chunks"] += result.get("chunks_stored", 0)
            summary["total_bitable"] += result.get("bitable_written", 0)
            if result.get("website_found"):
                summary["total_websites"] += 1
            if result.get("news_count", 0):
                summary["total_news"] += result["news_count"]

            if result.get("errors"):
                summary["failed"] += 1
                print(f"  ⚠️ {company} 部分失败: {result['errors']}")
            else:
                summary["success"] += 1
                print(
                    f"  ✅ {company} 完成 — "
                    f"职位:{result['jobs_count']} "
                    f"新闻:{result.get('news_count', 0)} "
                    f"官网:{result['website_found']} "
                    f"写入:{result['bitable_written']}条 "
                    f"耗时:{result['elapsed']}s"
                )

            summary["details"].append({
                "company": company,
                "success": not result.get("errors"),
                "jobs": result.get("jobs_count", 0),
                "website": result.get("website_found", False),
                "news": result.get("news_count", 0),
                "chunks": result.get("chunks_stored", 0),
                "bitable": result.get("bitable_written", 0),
                "elapsed": result.get("elapsed", 0),
                "errors": result.get("errors", []),
            })
        except Exception as e:
            summary["failed"] += 1
            print(f"  ❌ {company} 失败: {e}")
            summary["details"].append({
                "company": company,
                "success": False,
                "error": str(e),
            })

        # 公司间延迟（最后一家不需要）
        if i < total:
            await asyncio.sleep(delay_between)

    summary["elapsed_secs"] = round(time.monotonic() - start_all, 1)

    # 打印最终报告
    print(f"\n{'='*60}")
    print(f"  批量爬取完成！")
    print(f"  总计 {total} 家公司")
    print(f"  成功: {summary['success']} | 失败: {summary['failed']}")
    print(f"  职位: {summary['total_jobs']} | 官网: {summary['total_websites']}")
    print(f"  新闻: {summary['total_news']} | Chunks: {summary['total_chunks']}")
    print(f"  Bitable写入: {summary['total_bitable']} 条")
    print(f"  总耗时: {summary['elapsed_secs']:.0f}s")
    print(f"{'='*60}\n")

    return summary


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="批量爬取所有公司信息")
    parser.add_argument("--force", action="store_true", help="强制重新爬取（忽略缓存）")
    parser.add_argument("--dry-run", action="store_true", help="仅列出公司名不做爬取")
    parser.add_argument("--delay", type=float, default=5.0, help="公司间请求延迟秒数（默认5s）")
    parser.add_argument("--timeout", type=float, default=60.0, help="每公司超时秒数（默认60s）")
    parser.add_argument("--limit", type=int, default=0, help="限制爬取公司数（0=全部）")
    args = parser.parse_args()

    print("正在从飞书 Bitable 读取公司列表...")
    companies = get_all_companies()
    if not companies:
        print("❌ 未读取到任何公司")
        sys.exit(1)

    print(f"共 {len(companies)} 家公司")

    if args.dry_run:
        print("\n公司列表（dry-run 模式，不执行爬取）：")
        for i, c in enumerate(companies, 1):
            print(f"  {i:2d}. {c}")
        return

    if args.limit > 0:
        companies = companies[:args.limit]
        print(f"限制为前 {len(companies)} 家公司")

    await batch_crawl(
        companies=companies,
        force=args.force,
        delay_between=args.delay,
        timeout_per_company=args.timeout,
    )


if __name__ == "__main__":
    asyncio.run(main())
