"""LLM 验证层 — Phase 3 Enhancement

对爬虫抓取的原始数据进行 LLM 质量验证和信息提取，
解决纯规则解析的准确率低、覆盖不全问题。

设计原则：
- 批量调用：一次 LLM 调用处理全部候选，减少 API 开销
- 超时保护：单次 LLM 调用最长 20 秒，超时回退规则方法
- 规则回退：LLM 不可用时降级为现有规则逻辑
- 只读角色：不修改爬虫抓取逻辑，只做后处理验证

使用场景：
1. 官网 URL 确认：区分公司官网 vs 第三方文章（知乎/百科等）
2. 职位信息提取：从搜索摘要中结构化提取职位名/技术栈
3. 新闻相关性：验证新闻是否确实关于目标公司
4. 公司信息提取：从网页文本中提取结构化公司介绍
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from src.llm.azure_client import chat_completion
from src.utils.logger import logger

# LLM 调用超时（秒）
LLM_TIMEOUT = 45.0  # 推理模型处理批量数据可能较慢


async def _call_llm(prompt: str, system: str = "", max_tokens: int = 4000) -> str | None:
    """调用 LLM 生成，带超时保护

    gpt-5-nano 推理模型约 95% token 用于推理，需足够的 max_tokens 预算。
    默认 4000 → 约 200 输出 token，足够小 JSON 回复。
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        result = await asyncio.wait_for(
            chat_completion(
                messages=messages,
                temperature=0.1,
                max_tokens=max_tokens,
            ),
            timeout=LLM_TIMEOUT,
        )
        return result.strip() if result else None
    except asyncio.TimeoutError:
        logger.warning(f"[LLM验证] 调用超时（{LLM_TIMEOUT}s），回退规则方法")
        return None
    except Exception as e:
        logger.warning(f"[LLM验证] 调用失败: {e}，回退规则方法")
        return None


# ═══════════════════════════════════════════════════════════════════
# 1. 官网 URL 验证
# ═══════════════════════════════════════════════════════════════════

OFFICIAL_WEBSITE_SYSTEM = (
    "你是一个企业信息核实助手。你的任务是判断给定的 URL 是否"
    "确实是某公司的官方网站，而不是第三方网站上的文章或介绍。\n\n"
    "判断标准：\n"
    "- URL 的域名属于该公司本身（如 company.com）→ 官网\n"
    "- URL 是公司在自己域名下的页面（如 company.com/about）→ 官网\n"
    "- URL 是第三方平台（知乎、百度百科、新浪、搜狐、CSDN、36kr、"
    "微信公众号、LinkedIn 等）的文章 → 不是官网\n"
    "- URL 是招聘网站（zhipin.com、51job.com 等）的页面 → 不是官网\n"
    "- URL 是新闻媒体的报道 → 不是官网\n\n"
    "请严格按以下 JSON 格式输出，不要输出其他内容：\n"
    '{"is_official": true/false, "domain": "提取的域名", "reason": "简短理由"}'
)


async def verify_official_website(
    company: str,
    candidates: list[dict[str, str]],
) -> dict[str, Any]:
    """验证候选 URL 中哪个是公司官网

    Args:
        company: 公司名称
        candidates: 候选列表，每项 {url, title, snippet}

    Returns:
        {
            "official_url": str | None,    # 确认的官网 URL
            "all_verified": list[dict],    # 全部验证结果
            "llm_used": bool,
        }
    """
    if not candidates:
        return {"official_url": None, "all_verified": [], "llm_used": False}

    # 构建批量验证提示词
    lines = [(
        f"请逐一判断以下 URL 是否为「{company}」的官方网站。\n"
        f"返回一个 JSON 数组，每个元素为下列格式：\n"
        f'{{"is_official": true/false, "domain": "域名", "reason": "判断理由"}}\n'
    )]
    for i, c in enumerate(candidates, 1):
        title = c.get("title", "")[:100]
        snippet = c.get("snippet", "")[:150]
        url = c.get("url", "")
        lines.append(f"{i}. URL: {url}")
        if title:
            lines.append(f"   标题: {title}")
        if snippet:
            lines.append(f"   摘要: {snippet}")
        lines.append("")

    prompt = "\n".join(lines)

    result = await _call_llm(prompt, OFFICIAL_WEBSITE_SYSTEM, max_tokens=4000)
    if not result:
        return {"official_url": None, "all_verified": [], "llm_used": False}

    # 解析 LLM 返回的 JSON 数组
    verified = _parse_json_response(result)
    if not verified:
        return {"official_url": None, "all_verified": [], "llm_used": False}

    # 取第一个被标记为官网的 URL
    official_url = None
    all_verified = []
    if isinstance(verified, dict):
        verified = [verified]

    for i, v in enumerate(verified):
        # 尝试从验证结果中直接获取 URL，或按索引匹配候选
        url = v.get("url", "")
        if not url and i < len(candidates):
            url = candidates[i].get("url", "")
        entry = {
            "url": url,
            "is_official": v.get("is_official", False),
            "domain": v.get("domain", ""),
            "reason": v.get("reason", ""),
        }
        all_verified.append(entry)
        if entry["is_official"] and official_url is None:
            official_url = entry["url"]

    return {"official_url": official_url, "all_verified": all_verified, "llm_used": True}


# ═══════════════════════════════════════════════════════════════════
# 2. 职位信息提取
# ═══════════════════════════════════════════════════════════════════

JOB_EXTRACTION_SYSTEM = (
    "你是一个招聘信息分析助手。你的任务是从搜索引擎结果摘要中"
    "提取结构化的职位信息。\n\n"
    "提取规则：\n"
    "- 职位名称：去除公司名，只保留职位（如「Java开发工程师」「AI算法专家」）\n"
    "- 技术关键词：从JD描述中提取的技术栈（Python/AWS/K8s/AI等）\n"
    "- 如果文本中没有明确的职位信息，返回空列表\n"
    "- 不要编造不存在的职位\n\n"
    "请严格按以下 JSON 格式输出：\n"
    '{"jobs": [{"title": "职位名", "tech_keywords": ["技能1", "技能2"], '
    '"salary": "薪资范围（如有）", "source_url": "URL（如有）"}]}'
)


async def extract_jobs_batch(
    company: str,
    search_results: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """从搜索结果批量提取职位信息

    Args:
        company: 公司名称
        search_results: 搜索结果，每项 {url, title, snippet}

    Returns:
        结构化职位列表 [{title, tech_keywords, salary, source_url}]
    """
    if not search_results:
        return []

    lines = [
        f"请从以下关于「{company}」的搜索结果中提取招聘职位信息。\n"
        f'返回格式: {{"jobs": [{{"title": "职位名", "tech_keywords": ["技能"], '
        f'"salary": "薪资", "source_url": "URL"}}]}}\n'
    ]
    for i, r in enumerate(search_results[:10], 1):  # 最多 10 条
        url = r.get("url", "")
        title = r.get("title", "")[:120]
        snippet = r.get("snippet", "")[:200]
        lines.append(f"{i}. URL: {url}")
        lines.append(f"   标题: {title}")
        if snippet:
            lines.append(f"   摘要: {snippet}")
        lines.append("")

    prompt = "\n".join(lines)
    result = await _call_llm(prompt, JOB_EXTRACTION_SYSTEM, max_tokens=4000)
    if not result:
        return []

    parsed = _parse_json_response(result)
    if not parsed:
        return []

    jobs = parsed.get("jobs", []) if isinstance(parsed, dict) else []
    return jobs


# ═══════════════════════════════════════════════════════════════════
# 3. 新闻相关性验证
# ═══════════════════════════════════════════════════════════════════

NEWS_RELEVANCE_SYSTEM = (
    "你是一个企业新闻分析助手。你的任务是判断新闻文章是否"
    "确实与指定公司相关，以及属于什么技术主题。\n\n"
    "判断标准：\n"
    "- 文章主要报道该公司的业务/技术/产品 → 相关\n"
    "- 文章仅提及该公司（如行业综述中一笔带过）→ 不相关\n"
    "- 文章实际是关于另一家名字类似的公司 → 不相关\n"
    "- 文章是招聘广告而非新闻 → 不相关\n\n"
    "技术主题分类（选一个最匹配的）：\n"
    "- AI/人工智能 - 机器学习/深度学习/大模型/AIGC\n"
    "- 云计算 - AWS/Azure/云服务/云原生/SaaS\n"
    "- 大数据 - 数据平台/数据分析/数据治理\n"
    "- IT/安全 - 信息安全/网络安全/IT基础设施\n"
    "- 数字化转型 - 数字化/信息化/智能制造\n"
    "- 其他技术\n\n"
    "请严格按以下 JSON 格式输出：\n"
    '{"relevant": true/false, "topic": "主题分类", "confidence": "high/medium/low", '
    '"reason": "简短理由"}'
)


async def verify_news_batch(
    company: str,
    news_items: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """批量验证新闻相关性

    Args:
        company: 公司名称
        news_items: 新闻列表，每项 {url, title, summary}

    Returns:
        验证后的新闻列表，附加 relevant/topic/confidence 字段
    """
    if not news_items:
        return []

    results = []
    for item in news_items:
        title = item.get("title", "")[:200]
        summary = item.get("summary", "")[:300]
        url = item.get("url", "")

        prompt = (
            f"请判断以下新闻是否与「{company}」相关，仅输出一个 JSON 对象，不要其他内容：\n\n"
            f"URL: {url}\n"
            f"标题: {title}\n"
            f"摘要: {summary}\n"
            f'\n格式: {{"relevant": true/false, "topic": "主题", "confidence": "high/medium/low", "reason": "理由"}}'
        )

        llm_result = await _call_llm(prompt, NEWS_RELEVANCE_SYSTEM)
        verification = {}

        if llm_result:
            verification = _parse_json_response(llm_result) or {}

        results.append({
            **item,
            "relevant": verification.get("relevant", True),  # 默认认为相关
            "topic": verification.get("topic", item.get("topic", "")),
            "confidence": verification.get("confidence", "medium"),
            "reason": verification.get("reason", ""),
        })

    return results


# ═══════════════════════════════════════════════════════════════════
# 4. 公司信息结构化提取
# ═══════════════════════════════════════════════════════════════════

COMPANY_INFO_SYSTEM = (
    "你是一个企业信息分析师。从网页文本中提取结构化的公司信息。\n\n"
    "提取内容：\n"
    "1. 公司简介（2-3 句话）\n"
    "2. 主营业务 / 产品线\n"
    "3. 所属行业\n"
    "4. 公司规模（如有提及）\n\n"
    "规则：\n"
    "- 只提取网页中明确提到的信息\n"
    "- 不要编造或推测\n"
    "- 如果某信息未提及，标注「未提及」\n\n"
    "请严格按以下 JSON 格式输出：\n"
    '{"summary": "公司简介", "products": "主营业务", "industry": "行业", "scale": "规模"}'
)


async def extract_company_info(
    company: str,
    page_text: str,
) -> dict[str, str]:
    """从爬取的网页文本中提取结构化公司信息

    Args:
        company: 公司名称
        page_text: 爬取的网页文本（已清洗）

    Returns:
        {summary, products, industry, scale}
    """
    if not page_text or len(page_text) < 50:
        return {"summary": "", "products": "", "industry": "", "scale": ""}

    text = page_text[:2500]  # 限制长度避免 token 超限和超时
    prompt = (
        f"请从以下「{company}」官网页面文本中提取结构化公司信息，仅输出一个 JSON 对象：\n\n"
        f"{text}\n\n"
        f'格式: {{"summary": "简介", "products": "主营业务", "industry": "行业", "scale": "规模"}}'
    )

    result = await _call_llm(prompt, COMPANY_INFO_SYSTEM)
    if not result:
        return {"summary": "", "products": "", "industry": "", "scale": ""}

    parsed = _parse_json_response(result)
    if isinstance(parsed, dict):
        return {
            "summary": parsed.get("summary", ""),
            "products": parsed.get("products", ""),
            "industry": parsed.get("industry", ""),
            "scale": parsed.get("scale", ""),
        }
    return {"summary": "", "products": "", "industry": "", "scale": ""}


# ═══════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════

def _parse_json_response(text: str) -> dict | list | None:
    """从 LLM 回复中安全提取 JSON

    处理 gpt-5-nano 推理模型可能输出的各种格式：
    - 纯 JSON: {"key": "val"}
    - markdown 代码块: ```json ... ```
    - 推理 + JSON: （推理文字） {"key": "val"}
    - 多个连接的对象: {...}{...}
    """
    if not text:
        return None

    import re

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试提取 ```json ... ``` 代码块
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # 提取所有顶层 {...} 或 [...] 块
    all_blocks = re.findall(r'(\{[\s\S]*?\}|\[[\s\S]*?\])', text)

    if not all_blocks:
        # 最后尝试：贪婪匹配第一组 { ... }
        brace_match = re.search(r'\{[\s\S]*\}', text)
        if brace_match:
            try:
                return json.loads(brace_match.group())
            except json.JSONDecodeError:
                pass
        bracket_match = re.search(r'\[[\s\S]*\]', text)
        if bracket_match:
            try:
                return json.loads(bracket_match.group())
            except json.JSONDecodeError:
                pass
        logger.warning(f"[LLM验证] 无法解析 JSON 回复: {text[:300]}")
        return None

    results = []
    for block in all_blocks:
        try:
            results.append(json.loads(block))
        except json.JSONDecodeError:
            continue

    if not results:
        logger.warning(f"[LLM验证] 无法解析 JSON 回复: {text[:300]}")
        return None

    if len(results) == 1:
        return results[0]

    # 多个对象：如果是 dict 列表，按列表返回
    # 检查第一个元素的类型来决定
    if isinstance(results[0], dict):
        return results  # 返回为 list[dict]
    return results
