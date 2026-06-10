"""LLM 验证层本地测试 — 验证 LLM 能否修正爬虫质量问题

测试场景（对应用户报告的 4 个问题）：
1. zhihu.com 误判为官网 → verify_official_website 应拒识
2. "未知职位" → extract_jobs_batch 应从搜索摘要提取真实职位名
3. 不相关新闻混入 → verify_news_batch 应过滤
4. URL 含公司名但非官网 → verify_official_website 综合判断
"""

import asyncio
import sys
import os
import json
from pathlib import Path

# 确保项目根目录在 sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.crawler.llm_verifier import (
    verify_official_website,
    extract_jobs_batch,
    verify_news_batch,
    extract_company_info,
)
from src.utils.config import settings
from src.utils.logger import logger

PASS = "✅"
FAIL = "❌"
SKIP = "⏭️"
WARN = "⚠️"


# ── 测试数据 ─────────────────────────────────────────────────

# 场景 1: zhihu.com 等非官网被误判为官网
BAD_OFFICIAL_URLS = [
    {
        "url": "https://www.zhihu.com/question/312456789/answer/1234567890",
        "title": "上海汉得信息技术股份有限公司怎么样？",
        "snippet": "在知乎上看到很多人问汉得信息的工作体验...",
    },
    {
        "url": "https://baike.baidu.com/item/汉得信息",
        "title": "汉得信息_百度百科",
        "snippet": "汉得信息是一家IT咨询服务公司，成立于2002年...",
    },
    {
        "url": "https://www.donews.com/news/detail/1/3124567.html",
        "title": "汉得信息最新财报：营收增长20% - DoNews",
        "snippet": "汉得信息发布2024年财报...",
    },
]

# 场景 2: 正确的官网
GOOD_OFFICIAL_URLS = [
    {
        "url": "https://www.hand-china.com",
        "title": "汉得信息 - 数字化转型服务商",
        "snippet": "汉得信息是中国领先的IT咨询服务商，为企业提供数字化转型解决方案...",
    },
    {
        "url": "https://www.byd.com",
        "title": "比亚迪 - 用技术创新满足人们对美好生活的向往",
        "snippet": "比亚迪股份有限公司是一家致力于用技术创新...",
    },
]

# 场景 3: 招聘搜索摘要（容易出 "未知职位"）
JOB_SEARCH_RESULTS = [
    {
        "url": "https://www.zhipin.com/job_detail/abc123.html",
        "title": "「大数据开发工程师」招聘-上海汉得信息",
        "snippet": "【岗位职责】1、负责大数据平台的开发与维护；2、熟悉Hadoop/Spark生态；3、Python/Scala编程。薪资：25-40K·14薪",
    },
    {
        "url": "https://www.zhaopin.com/jobs/def456.html",
        "title": "云计算架构师 - 汉得信息",
        "snippet": "【岗位要求】1、熟悉AWS/Azure/阿里云等主流云平台；2、5年以上云架构经验；3、持有云认证优先。月薪30K-50K",
    },
    {
        "url": "https://www.zhipin.com/job_detail/ghi789.html",
        "title": "「高级前端开发工程师」汉得信息技术招聘",
        "snippet": "岗位职责：负责公司产品前端架构设计与开发，精通React/Vue。薪资：20-35K",
    },
    {
        "url": "https://www.51job.com/job/xyz.html",
        "title": "职位招聘页面",
        "snippet": "点击查看详细职位信息，公司福利待遇优厚，五险一金，年终奖金...",  # 摘要无效
    },
    {
        "url": "https://www.liepin.com/job/uvw.html",
        "title": "【社招】AI算法工程师",
        "snippet": "负责大语言模型训练与优化，熟悉Transformer架构，有NLP/CV经验。薪资面议",
    },
]

# 场景 4: 新闻（混合相关和不相关）
MIXED_NEWS = [
    {
        "url": "https://www.163.com/tech/article/ABC123.html",
        "title": "汉得信息与微软达成战略合作 推进AI在企业服务领域应用",
        "summary": "汉得信息宣布与微软达成战略合作，将基于Azure AI技术推进企业服务智能化转型...",
        "topic": "AI",
    },
    {
        "url": "https://www.sohu.com/a/123456_789012",
        "title": "汉得信息2025Q1财报：营收增长15% 云服务业务亮眼",
        "summary": "汉得信息发布2025年第一季度财报，营收突破15亿元，同比增长15%...",
        "topic": "技术新闻",
    },
    {
        "url": "https://www.ithome.com/0/111/222.htm",
        "title": "汉得信息上线基于大模型的智能客服系统",
        "summary": "汉得信息近日上线了基于自研大模型的智能客服系统，覆盖IT运维、财务咨询等场景...",
        "topic": "AI",
    },
    {
        "url": "https://www.163.com/sports/article/DEF456.html",
        "title": "2025中超联赛：上海海港3:1战胜对手",
        "summary": "中超联赛第15轮，上海海港主场迎战...",  # 完全不相关
        "topic": "体育",
    },
    {
        "url": "https://www.toutiao.com/article/999888.html",
        "title": "汉得信息怎么样？入职体验分享",
        "summary": "最近有很多朋友问我汉得信息的工作体验，今天来分享一下...",  # 知乎类内容
        "topic": "其他",
    },
]


async def test_verify_zhihu_rejected():
    """测试1：zhihu/baike 等非官网被 LLM 正确拒识"""
    print(f"\n{'='*60}")
    print(f"  测试 1: 官网 URL 验证 — 非官网拒绝")
    print(f"{'='*60}")

    # 先测坏 URL
    bad_result = await verify_official_website("上海汉得信息技术股份有限公司", BAD_OFFICIAL_URLS)

    if not bad_result.get("llm_used"):
        print(f"  {WARN} LLM 不可用，跳过测试")
        return False

    bad_candidates = bad_result.get("all_verified", [])
    for bc in bad_candidates:
        is_official = bc.get("is_official", False)
        url = bc.get("url", "")[:60]
        reason = bc.get("reason", "")
        mark = FAIL if is_official else PASS
        print(f"  {mark} {url}")
        if reason:
            print(f"      理由: {reason}")

    # 检查是否所有坏 URL 都被拒绝了
    all_rejected = all(not bc.get("is_official") for bc in bad_candidates)
    if all_rejected:
        print(f"  {PASS} 所有非官网 URL 均被正确拒识")
        return True
    else:
        print(f"  {FAIL} 存在误判为官网的 URL")
        return False


async def test_verify_good_official_accepted():
    """测试2：正确官网被确认"""
    print(f"\n{'='*60}")
    print(f"  测试 2: 官网 URL 验证 — 正确官网确认")
    print(f"{'='*60}")

    good_result = await verify_official_website("比亚迪股份有限公司", GOOD_OFFICIAL_URLS)

    if not good_result.get("llm_used"):
        print(f"  {WARN} LLM 不可用，跳过测试")
        return False

    good_candidates = good_result.get("all_verified", [])
    for gc in good_candidates:
        is_official = gc.get("is_official", False)
        url = gc.get("url", "")[:60]
        reason = gc.get("reason", "")
        mark = PASS if is_official else FAIL
        print(f"  {mark} {url}")
        if reason:
            print(f"      理由: {reason}")

    official_url = good_result.get("official_url", "")
    if official_url:
        print(f"  {PASS} 确认官网: {official_url[:60]}")
        return True
    else:
        print(f"  {FAIL} 未能确认官网")
        return False


async def test_job_extraction_no_unknown():
    """测试3：职位提取 — 不应出现"未知职位" """
    print(f"\n{'='*60}")
    print(f"  测试 3: 职位信息 LLM 提取")
    print(f"{'='*60}")

    result = await extract_jobs_batch("上海汉得信息技术股份有限公司", JOB_SEARCH_RESULTS)

    if not result:
        print(f"  {WARN} LLM 返回空，可能不可用")
        return False

    print(f"  输入 {len(JOB_SEARCH_RESULTS)} 条搜索摘要，提取到 {len(result)} 条职位\n")

    has_unknown = False
    valid_count = 0
    for i, job in enumerate(result):
        title = job.get("title", "")
        tech = job.get("tech_keywords", [])
        salary = job.get("salary", "")

        if title in ("未知职位", "未知", ""):
            has_unknown = True
            mark = FAIL
        else:
            valid_count += 1
            mark = PASS

        print(f"  {mark} [{i+1}] {title}")
        print(f"      技术关键词: {tech}")
        print(f"      薪资: {salary or '(未提取)'}")

    if has_unknown:
        print(f"\n  {FAIL} 存在\"未知职位\" — LLM 未能从摘要中提取职位名")
        return False
    elif valid_count >= 3:
        print(f"\n  {PASS} 成功提取 {valid_count} 条有效职位")
        return True
    else:
        print(f"\n  {WARN} 有效职位数较少 ({valid_count}/{len(JOB_SEARCH_RESULTS)})")
        return valid_count >= 2


async def test_news_filtering():
    """测试4：新闻过滤 — 不相关新闻被过滤"""
    print(f"\n{'='*60}")
    print(f"  测试 4: 新闻相关性验证 & 过滤")
    print(f"{'='*60}")

    result = await verify_news_batch("上海汉得信息技术股份有限公司", MIXED_NEWS)

    if not result:
        print(f"  {WARN} LLM 不可用或返回空")
        return False

    relevant = 0
    irrelevant = 0
    for i, nr in enumerate(result):
        is_rel = nr.get("relevant", True)
        title = nr.get("title", "")[:50]
        reason = nr.get("reason", "")
        if is_rel is False:
            irrelevant += 1
            print(f"  {PASS} [过滤] {title}")
            if reason:
                print(f"       理由: {reason}")
        else:
            relevant += 1
            print(f"  {PASS} [保留] {title}")

    print(f"\n  保留 {relevant} 条, 过滤 {irrelevant} 条")
    # 预期: 体育新闻 和 知乎体验 应被过滤
    if irrelevant >= 1:
        print(f"  {PASS} 成功过滤不相关新闻")
        return True
    else:
        print(f"  {FAIL} 未能过滤不相关新闻")
        return False


async def test_company_intro_summary():
    """测试5：公司介绍提取（新增能力）"""
    print(f"\n{'='*60}")
    print(f"  测试 5: 公司介绍 LLM 摘要提取")
    print(f"{'='*60}")

    about_text = """
    上海汉得信息技术股份有限公司（HAND Enterprise Solutions）成立于2002年，
    是一家专业的IT咨询服务公司，总部位于上海。公司于2011年在深交所上市（股票代码：300170）。
    
    汉得信息的主营业务包括：ERP实施与咨询、数字化中台建设、云计算服务、大数据分析、
    AI智能应用开发等。公司在国内拥有20+分支机构，员工超过10000人。
    
    公司服务客户超过6000家，涵盖制造、金融、零售、医疗等多个行业。
    """

    result = await extract_company_info("上海汉得信息技术股份有限公司", about_text)

    if not result or not result.get("summary"):
        print(f"  {WARN} LLM 不可用，跳过测试")
        return False

    summary = result.get("summary", "")
    products = result.get("products", "")
    industry = result.get("industry", "")
    print(f"  原文 {len(about_text)} 字 → 摘要 {len(summary)} 字")
    print(f"  {'-'*50}")
    print(f"  摘要: {summary[:300]}")
    if products:
        print(f"  产品: {products[:200]}")
    if industry:
        print(f"  行业: {industry}")
    print(f"  {'-'*50}")

    # 检查是否包含关键信息
    combined = summary + products + industry
    checks = ["2002", "上市", "IT咨询", "数字化", "汉得"]
    score = sum(1 for kw in checks if kw in combined)
    if score >= 2:
        print(f"  {PASS} 摘要含关键信息 ({score}/{len(checks)} 关键词命中)")
        return True
    else:
        print(f"  {WARN} 摘要关键信息较少 ({score}/{len(checks)})")
        return False


async def main():
    print(f"\n{'#'*60}")
    print(f"  LLM 验证层测试套件")
    print(f"  Model: {settings.azure_openai_deployment}")
    print(f"{'#'*60}")

    results = {}

    # 测试1: 非官网拒绝
    results["bad_url_rejected"] = await test_verify_zhihu_rejected()

    # 测试2: 正确官网确认
    results["good_url_accepted"] = await test_verify_good_official_accepted()

    # 测试3: 职位提取无未知
    results["no_unknown_jobs"] = await test_job_extraction_no_unknown()

    # 测试4: 新闻过滤
    results["news_filtered"] = await test_news_filtering()

    # 测试5: 公司介绍摘要
    results["intro_summary"] = await test_company_intro_summary()

    # ── 汇总 ──
    print(f"\n{'#'*60}")
    print(f"  测试汇总")
    print(f"{'#'*60}")

    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for name, ok in results.items():
        mark = PASS if ok else FAIL
        print(f"  {mark} {name}")

    print(f"\n  通过: {passed}/{total}")

    if passed == total:
        print(f"  {PASS} 全部通过！LLM 验证层有效")
        return 0
    elif passed >= total - 1:
        print(f"  {WARN} 基本通过，有 1 项需关注")
        return 0
    else:
        print(f"  {FAIL} 多项失败，需排查")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
