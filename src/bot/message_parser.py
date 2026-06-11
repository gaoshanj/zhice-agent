"""用户输入解析 — 从飞书消息文本中提取结构化字段

v2: 三层提取策略 — Regex 优先级重排 → 公司名验证 → LLM 兜底
"""

import re
import json
import asyncio
from typing import Optional


def parse_user_input(text: str) -> dict:
    """解析用户输入，提取报告生成所需字段。

    支持两种输入格式：
    1. 结构化格式（按项目规格文档的模板）：
       - 客户公司：[xxx]
       - 拜访对象部门/职位：[xxx]
       ...

    2. 自由文本格式：
       直接输入公司名称，其余字段使用默认值

    Returns:
        dict with keys:
        - company (str): 客户公司名称（必填）
        - visit_target (str): 拜访对象，如「技术研发部/CTO」
        - known_info (str): 当前已知信息
        - visit_purpose (str): 拜访目的
        - focus_areas (list[str]): 期望侧重方向
        - special_req (str): 特别要求
    """
    result = {
        "company": "",
        "visit_target": "",
        "known_info": "",
        "visit_purpose": "",
        "focus_areas": [],
        "special_req": "",
        "raw_text": text[:500],
    }

    # 1. 尝试按结构化模板解析
    _parse_structured(text, result)

    # 2. 如果未解析到公司名，尝试从自由文本中提取（三层策略）
    if not result["company"]:
        _parse_free_text(text, result)

    # 3. 自由文本中未提取到拜访目的时，从原文截取
    if not result["visit_purpose"] and result["raw_text"]:
        _extract_purpose_fallback(text, result)

    return result


def _parse_structured(text: str, result: dict):
    """按结构化模板解析"""
    # 客户公司
    m = re.search(r"客户公司[：:]\s*([^\n]+)", text)
    if m:
        result["company"] = m.group(1).strip()

    # 拜访对象部门/职位
    m = re.search(r"拜访对象部门/职位[：:][ \t]*([^\n]+)", text)
    if m:
        result["visit_target"] = m.group(1).strip()

    # 当前已知信息
    m = re.search(r"当前已知信息（选填）[：:][ \t]*([^\n]+)", text)
    if m:
        result["known_info"] = m.group(1).strip()

    # 本次拜访主要目的
    m = re.search(r"本次拜访主要目的[：:][ \t]*([^\n]+)", text)
    if m:
        result["visit_purpose"] = m.group(1).strip()

    # 期望侧重方向（多选）
    focus_pattern = r"期望侧重方向（多选）[：:][ \t]*([^\n]+)"
    m = re.search(focus_pattern, text)
    if m:
        raw = m.group(1).strip()
        # 支持用 / 、或空格分隔
        areas = re.split(r"[／/、]+", raw)
        result["focus_areas"] = [a.strip() for a in areas if a.strip()]

    # 特别要求
    m = re.search(r"特别要求（选填）[：:][ \t]*([^\n]+)", text)
    if m:
        result["special_req"] = m.group(1).strip()


def _parse_free_text(text: str, result: dict):
    """从自由文本中提取公司名

    三层策略：
      Layer 1: 高优先级 Regex（拜访/走访/去XX等公司名上下文）
      Layer 2: 中优先级 Regex（传统模式：针对/面向/公司后缀）
      Layer 3: LLM 兜底提取（异步，需在 async 上下文中调用）
    """
    cleaned = re.sub(r"@\S+\s*", "", text).strip()

    # ── Layer 1: 高优先级 — 明确的公司上下文 ─────────────────
    # ⚠️ "拜访/走访/去XX出差" 的上下文是最强的公司名信号
    #    优先匹配，避免被后续的 "生成XXX" 等模式误抓
    layer1_patterns = [
        # "要去拜访 XX"、"前往拜访 XX"、"去拜访 XX"
        r"(?:要\s*)?(?:去|前往)\s*拜访\s*([\u4e00-\u9fffA-Za-z（）\(\)]{2,25}(?:公司|集团|科技|有限|股份|制药|医药|软件|网络|银行|保险|半导体|汽车|实业|控股)?)",
        # "拜访 XX"、"走访 XX"
        r"(?:拜访|走访|出差去|出差到)\s*([\u4e00-\u9fffA-Za-z（）\(\)]{2,25}(?:公司|集团|科技|有限|股份|制药|医药|软件|网络|银行|保险|半导体|汽车|实业|控股)?)",
        # "去XX拜访"、"去XX出差"、"前往XX"
        r"(?:去|前往)\s*([\u4e00-\u9fffA-Za-z（）\(\)]{2,25}(?:公司|集团|科技|有限|股份|制药|医药|软件|网络|银行|保险|半导体|汽车|实业|控股)?)\s*(?:拜访|走访|出差)",
        # "针对XX的销售方案"（比"生成XX的"更可靠）
        r"针对\s*([\u4e00-\u9fffA-Za-z（）\(\)]{2,25}(?:公司|集团|科技|有限|股份|制药|医药)?)\s*(?:的|，|,)",
    ]

    for pat in layer1_patterns:
        m = re.search(pat, cleaned)
        if m:
            raw_company = m.group(1).strip()
            if _is_valid_company(raw_company):
                result["company"] = raw_company
                _extract_purpose_from_text(cleaned, result)
                return

    # ── Layer 2: 中优先级 — 公司名后缀匹配 / 传统模式 ────────
    layer2_patterns = [
        # 明确的公司后缀（最强信号）
        r"([\u4e00-\u9fff（）\(\)]{2,20}(?:公司|集团|科技|制药|技术|有限|股份|软件|网络|银行|保险|半导体|汽车|实业|控股|咨询))",
        # 针对/面向 + 可选冒号（不跨过"的"）
        r"针对\s*[：:]?\s*([^\s，,。的]{2,25})",
        r"面向\s*[：:]?\s*([^\s，,。的]{2,25})",
        r"关于\s*([^\s，,。的]{2,25})(?:的|，|,|。)",
        # "生成XXX的" — 低优先级，依赖 _is_valid_company() 过滤错误结果
        # 例如："生成阿里巴巴的培训方案" → "阿里巴巴"(有效)
        #        "生成一个Copilot的销售方案" → "一个Copilot"(无效，被过滤)
        r"生成\s*([^\s，,。]+)\s*[的，。,为]",
    ]

    for pat in layer2_patterns:
        m = re.search(pat, cleaned)
        if m:
            raw_company = m.group(1).strip()
            if _is_valid_company(raw_company):
                result["company"] = raw_company
                _extract_purpose_from_text(cleaned, result)
                return

    # ── Layer 3: LLM 兜底（低优先级，不在此处阻塞调用）────────
    # 仅在 Regex 完全无匹配时标记需要 LLM 提取
    # 实际 LLM 调用由调用方（feishu_handler）在收到空 company 时触发
    result["company"] = ""


def _extract_purpose_from_text(text: str, result: dict):
    """从自由文本中进一步提取拜访目的和侧重方向"""
    # 提取 "生成 XXX 的销售方案/报告" 作为拜访目的
    purpose_patterns = [
        r"生成\s*(?:一个|一份|一篇)?\s*([^，。的]+(?:方案|报告|策略|建议))",
        r"帮我\s*(?:生成|写|做|弄)\s*(?:一个|一份)?\s*([^，。]+(?:方案|报告|策略|建议))",
        r"(?:销售|培训|技术)\s*方案[：:]*\s*([^\n]{5,50})",
    ]
    for pat in purpose_patterns:
        m = re.search(pat, text)
        if m:
            purpose = m.group(1).strip()
            # 去掉开头的"的"字
            purpose = re.sub(r"^的\s*", "", purpose)
            if len(purpose) > 2:
                result["visit_purpose"] = purpose
                break

    # 提取培训/产品方向作为 focus_areas
    focus_keywords = {
        "Copilot": ["Copilot", "Microsoft 365 Copilot", "AI Agent"],
        "Azure": ["Azure", "微软云", "Azure OpenAI"],
        "AWS": ["AWS", "亚马逊云", "Amazon"],
        "AI Agent": ["AI Agent", "智能体", "Agent"],
        "Power BI": ["Power BI", "PowerBI"],
        "安全": ["安全", "安服", "Security"],
        "MSP": ["MSP", "管理服务"],
        "MA": ["MA", "管理会计"],
    }

    found_focus = []
    for area, keywords in focus_keywords.items():
        for kw in keywords:
            if kw.lower() in text.lower():
                found_focus.append(area)
                break

    if found_focus and not result.get("focus_areas"):
        result["focus_areas"] = found_focus


def _extract_purpose_fallback(text: str, result: dict):
    """如果 structured 和 free_text 都没提取到拜访目的，从原文截取"""
    # 去掉公司名之后剩余的部分作为拜访目的
    company = result.get("company", "")
    remaining = text

    if company and company in remaining:
        # 取公司名之后的内容
        idx = remaining.index(company) + len(company)
        remaining = remaining[idx:].strip()
        # 去掉开头的标点和常见连接词
        remaining = re.sub(r"^[，,。.\s]+", "", remaining)
        remaining = re.sub(r"^(请帮我|帮我|请|的|，)", "", remaining)

    # 截取前 100 字
    if remaining:
        result["visit_purpose"] = remaining[:100].strip()


def _is_valid_company(company: str) -> bool:
    """验证提取结果是否像一个有效的公司名称。

    过滤掉明显不是公司名的内容：
    - 以 "一个"、"某个"、"一家" 开头的（量词 + 名词，不是公司名）
    - 纯英文且全是常见非公司词
    - 过短/过长的字符串
    """
    if not company:
        return False

    company = company.strip()

    # ── 长度验证 ──
    if len(company) < 2:
        return False
    if len(company) > 50:
        return False

    # ── 排除量词开头 ──
    invalid_starts = ("一个", "某个", "一家", "这个", "那个", "哪家",
                       "什么", "一些", "几个", "这种", "那种", "这些")
    for prefix in invalid_starts:
        if company.startswith(prefix):
            return False

    # ── 排除纯数字 ──
    if company.isdigit():
        return False

    # ── 排除纯符号/纯英文常见词 ──
    # 全英文且小于 3 个字符且不是已知缩写
    if re.match(r"^[A-Za-z]{1,2}$", company):
        return False

    # ── 排除明显是产品名/技术名（无公司后缀的纯英文）────
    # 如果是 "Copilot Studio" 这种形式, 且没有公司后缀, 大概率是产品名
    # 但如果文本中只有一个实体, 且无其他公司名, 则保留（可能是直接输入公司名的情况）
    tech_terms = (
        r"^(copilot|azure|aws|power\s*bi|powerbi|microsoft|google"
        r"|office|windows|linux|python|java|docker|kubernetes)$"
    )
    if re.match(tech_terms, company.lower().replace(" ", "")):
        return False

    # ── 排除纯标点/空白 ──
    if re.match(r"^[\s\W_]+$", company):
        return False

    return True


# ── 异步 LLM 实体提取（由 feishu_handler 调用）─────────────────

async def extract_entities_via_llm(text: str) -> Optional[dict]:
    """使用 LLM 从自然语言消息中提取结构化实体（兜底方案）。

    当 Regex 完全无法匹配公司名时，由 feishu_handler 调用此函数。

    Args:
        text: 用户输入的自然语言文本

    Returns:
        dict 或 None: {"company": "百济神州", "visit_purpose": "...", "focus_areas": [...]}
    """
    try:
        from src.llm.azure_client import chat_completion
        from src.llm.prompt_templates import build_extraction_messages

        messages = build_extraction_messages(text)
        content = await chat_completion(
            messages,
            temperature=0.0,
            max_tokens=300,  # 实体很小，不需要太多 token
        )

        # 解析 JSON 响应
        parsed = _parse_llm_extraction(content)
        return parsed

    except Exception:
        # LLM 提取失败不影响主流程，返回 None
        return None


def _parse_llm_extraction(content: str) -> Optional[dict]:
    """从 LLM 返回中解析 JSON 实体"""
    if not content:
        return None

    # 尝试直接解析
    try:
        result = json.loads(content)
        if isinstance(result, dict) and result.get("company"):
            return result
    except json.JSONDecodeError:
        pass

    # 尝试从 markdown 代码块中提取
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    if m:
        try:
            result = json.loads(m.group(1))
            if isinstance(result, dict) and result.get("company"):
                return result
        except json.JSONDecodeError:
            pass

    return None
