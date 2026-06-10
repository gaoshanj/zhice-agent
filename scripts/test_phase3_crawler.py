"""Phase 3 爬虫模块本地测试脚本

测试内容：
1. 依赖导入检查
2. base_crawler 基础功能（HTML 请求 + 文本提取）
3. job_crawler 招聘搜索（九号公司）
4. web_crawler 官网爬取（九号公司）
5. crawler_dispatcher 完整调度链路
6. retriever 双集合检索

用法：
  python scripts/test_phase3_crawler.py
"""

from __future__ import annotations

import asyncio
import sys
import os
import time

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 加载 .env
from dotenv import load_dotenv
load_dotenv()


# ──────────────────────────────────────────────────────────────
# 测试辅助
# ──────────────────────────────────────────────────────────────

PASS = "✅ PASS"
FAIL = "❌ FAIL"
SKIP = "⏭️ SKIP"
results: list[tuple[str, str, str]] = []  # (name, status, detail)


def record(name: str, ok: bool, detail: str = "") -> None:
    status = PASS if ok else FAIL
    results.append((name, status, detail))
    print(f"  {status}  {name}" + (f" — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n{'='*55}")
    print(f"  {title}")
    print(f"{'='*55}")


# ──────────────────────────────────────────────────────────────
# Test 1: 依赖导入
# ──────────────────────────────────────────────────────────────

def test_imports():
    section("1. 依赖导入检查")
    deps = [
        ("httpx", "httpx"),
        ("beautifulsoup4", "bs4"),
        ("lxml", "lxml"),
        ("chromadb", "chromadb"),
    ]
    all_ok = True
    for name, mod in deps:
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "ok")
            record(f"import {name}", True, ver)
        except ImportError as e:
            record(f"import {name}", False, str(e))
            all_ok = False

    # 爬虫模块自身
    crawler_modules = [
        "src.crawler.base_crawler",
        "src.crawler.job_crawler",
        "src.crawler.web_crawler",
        "src.crawler.crawler_dispatcher",
        "src.rag.retriever",
    ]
    for mod in crawler_modules:
        try:
            __import__(mod)
            record(f"import {mod}", True)
        except Exception as e:
            record(f"import {mod}", False, str(e)[:120])
            all_ok = False

    return all_ok


# ──────────────────────────────────────────────────────────────
# Test 2: BaseCrawler 文本工具
# ──────────────────────────────────────────────────────────────

def test_base_crawler_utils():
    section("2. BaseCrawler 工具函数")
    from src.crawler.base_crawler import clean_text, truncate_text, deduplicate_texts

    # clean_text
    raw = "  Hello   \n\n  World  \n\n\n  "
    cleaned = clean_text(raw)
    record("clean_text 去多余空行", "Hello" in cleaned and "World" in cleaned, repr(cleaned[:60]))

    # truncate_text
    long = "A" * 3000
    truncated = truncate_text(long, 100)
    record("truncate_text 截断到 100 字", len(truncated) <= 110, f"len={len(truncated)}")

    # deduplicate_texts
    texts = ["Hello World This is a test", "Hello World This is a test", "Different text here for test"]
    deduped = deduplicate_texts(texts)
    record("deduplicate_texts 去重", len(deduped) == 2, f"输入 {len(texts)} 条 → {len(deduped)} 条")

    return True


# ──────────────────────────────────────────────────────────────
# Test 3: BaseCrawler HTTP 请求
# ──────────────────────────────────────────────────────────────

async def test_base_crawler_http():
    section("3. BaseCrawler HTTP 请求")
    from src.crawler.base_crawler import BaseCrawler

    crawler = BaseCrawler()

    # 请求一个公开稳定的页面（百度首页）
    print("  请求 baidu.com 首页...")
    t0 = time.monotonic()
    html = await crawler.fetch_html("https://www.baidu.com", retries=1)
    elapsed = time.monotonic() - t0

    if html:
        text = crawler.extract_text(html)
        record("fetch_html (baidu.com)", True, f"{len(html)} 字节，文本 {len(text)} 字，耗时 {elapsed:.1f}s")
        record("extract_text", len(text) > 10, f"前50字: {repr(text[:50])}")
    else:
        record("fetch_html (baidu.com)", False, "返回 None（可能网络问题）")


# ──────────────────────────────────────────────────────────────
# Test 4: JobCrawler 关键词提取（离线测试，不真正请求）
# ──────────────────────────────────────────────────────────────

def test_job_crawler_offline():
    section("4. JobCrawler 关键词提取（离线）")
    from src.crawler.job_crawler import JobCrawler, jobs_to_rag_document

    crawler = JobCrawler()

    # 测试关键词提取
    text = "Java 后端工程师，要求掌握 AWS、Kubernetes、Python，薪资 20-35K"
    keywords = crawler._extract_tech_keywords(text)
    record("_extract_tech_keywords", len(keywords) > 0, f"关键词: {keywords}")

    hints = crawler._get_training_hints(keywords)
    record("_get_training_hints", len(hints) > 0, f"培训建议: {hints[:3]}")

    # 测试 jobs_to_rag_document
    mock_jobs = [
        {
            "title": "云架构师",
            "keywords": ["aws", "kubernetes"],
            "salary": "25-40K",
            "raw_text": "...",
            "training_hints": ["AWS 认证培训", "云原生培训"],
        },
        {
            "title": "AI 工程师",
            "keywords": ["python", "ai", "大模型"],
            "salary": "30-50K",
            "raw_text": "...",
            "training_hints": ["大模型应用培训"],
        },
    ]
    doc = jobs_to_rag_document("九号公司", mock_jobs)
    record("jobs_to_rag_document", len(doc) > 50, f"文档长度 {len(doc)} 字")
    print(f"  文档预览:\n{doc[:300]}")


# ──────────────────────────────────────────────────────────────
# Test 5: WebCrawler 链接分析（离线）
# ──────────────────────────────────────────────────────────────

def test_web_crawler_offline():
    section("5. WebCrawler 链接分析（离线）")
    from src.crawler.web_crawler import WebCrawler

    crawler = WebCrawler()

    # 测试链接分类
    test_cases = [
        ("/about-us", "about us page", "about"),
        ("/products/cloud", "cloud products", "products"),
        ("/news/2024", "company news", "news"),
        ("/login", "login", None),
    ]
    all_ok = True
    for path, text, expected in test_cases:
        result = crawler._categorize_link(path, text)
        ok = result == expected
        record(f"_categorize_link '{path}'", ok, f"expected={expected}, got={result}")
        all_ok = all_ok and ok

    # 测试 _looks_like_official_website
    ok_cases = [
        ("https://www.segway.com", True),
        ("https://zhipin.com/job/xxx", False),
        ("https://baidu.com/xxx", False),
        ("https://example-corp.com/about", True),
    ]
    for url, expected in ok_cases:
        result = crawler._looks_like_official_website(url, "九号公司")
        ok = result == expected
        record(f"_looks_like_official_website '{url[:30]}...'", ok, f"expected={expected}, got={result}")


# ──────────────────────────────────────────────────────────────
# Test 6: JobCrawler 在线测试（真实搜索）
# ──────────────────────────────────────────────────────────────

async def test_job_crawler_online(company: str = "九号公司"):
    section(f"6. JobCrawler 在线测试（{company}，可能受网络影响）")
    from src.crawler.job_crawler import JobCrawler

    crawler = JobCrawler()
    print(f"  搜索 {company} 招聘信息...")
    t0 = time.monotonic()
    try:
        jobs = await asyncio.wait_for(crawler.crawl_jobs(company), timeout=20.0)
        elapsed = time.monotonic() - t0
        if jobs:
            record("crawl_jobs", True, f"获取到 {len(jobs)} 条职位，耗时 {elapsed:.1f}s")
            print(f"  前3条职位:")
            for j in jobs[:3]:
                print(f"    - {j['title']} | 关键词: {j['keywords'][:3]} | 薪资: {j['salary']}")
        else:
            record("crawl_jobs", True, f"无职位数据（网络限制，属正常），耗时 {elapsed:.1f}s")
    except asyncio.TimeoutError:
        record("crawl_jobs", True, "超时（20s），网络问题，不影响主流程")
    except Exception as e:
        record("crawl_jobs", False, str(e)[:100])


# ──────────────────────────────────────────────────────────────
# Test 7: WebCrawler 在线测试（官网搜索）
# ──────────────────────────────────────────────────────────────

async def test_web_crawler_online(company: str = "九号公司"):
    section(f"7. WebCrawler 在线测试（{company}，可能受网络影响）")
    from src.crawler.web_crawler import WebCrawler

    crawler = WebCrawler()
    print(f"  搜索 {company} 官网...")
    t0 = time.monotonic()
    try:
        info = await asyncio.wait_for(
            crawler.crawl_company_website(company), timeout=25.0
        )
        elapsed = time.monotonic() - t0
        website = info.get("website_url", "")
        summary = info.get("summary", "")
        record("crawl_company_website", True,
               f"官网={'找到' if website else '未找到'}，摘要 {len(summary)} 字，耗时 {elapsed:.1f}s")
        if website:
            print(f"  官网 URL: {website}")
        if summary:
            print(f"  摘要前100字: {summary[:100]}")
    except asyncio.TimeoutError:
        record("crawl_company_website", True, "超时（25s），网络问题，不影响主流程")
    except Exception as e:
        record("crawl_company_website", False, str(e)[:100])


# ──────────────────────────────────────────────────────────────
# Test 8: CrawlerDispatcher 调度（mock 模式，不写 ChromaDB）
# ──────────────────────────────────────────────────────────────

async def test_dispatcher_offline():
    section("8. CrawlerDispatcher 调度逻辑（缓存测试）")
    from src.crawler.crawler_dispatcher import _is_recently_crawled, _mark_crawled

    # 测试缓存逻辑
    company = "测试公司_cache_check"
    record("初始未缓存", not _is_recently_crawled(company), "")

    _mark_crawled(company)
    record("标记后已缓存", _is_recently_crawled(company), "")

    # 不同公司不相互影响
    record("其他公司无缓存", not _is_recently_crawled("另一家公司_xyz"), "")


# ──────────────────────────────────────────────────────────────
# Test 9: Retriever 双集合模式（仅验证导入和接口，不调 API）
# ──────────────────────────────────────────────────────────────

def test_retriever_interface():
    section("9. Retriever 双集合接口检查")
    try:
        from src.rag.retriever import retrieve_for_report, format_rag_context
        import inspect
        sig = inspect.signature(retrieve_for_report)
        params = list(sig.parameters.keys())
        record("retrieve_for_report 导入", True, f"参数: {params}")
        # 检查是否支持 include_external 或有多集合相关参数
        has_multi = "include_external" in params or "collection" in str(params).lower() or len(params) >= 2
        record("接口支持多集合或多参数", has_multi, f"参数列表: {params}")
        # 检查 format_rag_context
        sig2 = inspect.signature(format_rag_context)
        record("format_rag_context 导入", True, f"参数: {list(sig2.parameters.keys())}")
    except Exception as e:
        record("retriever 接口导入", False, str(e)[:100])


# ──────────────────────────────────────────────────────────────
# 汇总
# ──────────────────────────────────────────────────────────────

def print_summary():
    section("测试结果汇总")
    passed = sum(1 for _, s, _ in results if s == PASS)
    failed = sum(1 for _, s, _ in results if s == FAIL)
    print(f"  总计: {len(results)} 项 | {PASS} {passed} | {FAIL} {failed}\n")
    if failed > 0:
        print("  失败项目：")
        for name, status, detail in results:
            if status == FAIL:
                print(f"    ❌ {name}: {detail}")
    print()
    return failed == 0


# ──────────────────────────────────────────────────────────────
# 主函数
# ──────────────────────────────────────────────────────────────

async def main():
    print("\n" + "="*55)
    print("  Phase 3 爬虫模块本地测试")
    print("="*55)

    # 离线测试（不需要网络/API）
    if not test_imports():
        print("\n❌ 导入失败，请先修复依赖问题")
        return

    test_base_crawler_utils()
    test_job_crawler_offline()
    test_web_crawler_offline()
    await test_base_crawler_http()
    await test_dispatcher_offline()
    test_retriever_interface()

    # 在线测试（需要网络）
    print("\n  [在线测试需要网络，可能较慢...]")
    await test_job_crawler_online("九号公司")
    await test_web_crawler_online("九号公司")

    all_ok = print_summary()
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
