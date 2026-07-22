"""Microsoft Learn Catalog API 封装 — 搜索官方培训课程

API 文档: https://learn.microsoft.com/en-us/training/support/catalog-api-developer-reference
端点: https://learn.microsoft.com/api/catalog/
无需认证，直接调用。

返回字段说明:
- learningPaths: [{"courseId", "title", "summary", "url", "duration", "level", "products": [...]}]
- modules: [{"moduleId", "title", "summary", "url", "duration", "level"}]
"""

from __future__ import annotations

import asyncio
import httpx
import re
import html

from src.utils.config import settings
from src.utils.logger import logger


# Learn Catalog API 端点
_LEARN_API_URL = "https://learn.microsoft.com/api/catalog"
_LEARN_LOCALE = "zh-cn"  # 中文优先，回退到 en-us

# 产品名 → Learn 产品 ID 的静态映射（LLM 映射失败时的 fallback）
_PRODUCT_ID_MAP = {
    "copilot studio": ["microsoft-copilot-studio"],
    "copilot": ["microsoft-365-copilot", "microsoft-copilot"],
    "microsoft 365": ["microsoft-365"],
    "azure": ["azure"],
    "azure ai": ["azure-ai"],
    "azure openai": ["azure-openai"],
    "power bi": ["power-bi"],
    "power platform": ["power-platform"],
    "dynamics 365": ["dynamics-365"],
    "microsoft learn": ["microsoft-learn"],
    "ai": ["azure-ai", "ai"],
    "machine learning": ["azure-machine-learning"],
    "ml": ["azure-machine-learning"],
    "devops": ["azure-devops"],
    "github": ["github"],
    "security": ["security"],
    "身份": ["identity"],
    "数据": ["data"],
}

# 单次搜索最多返回课程数
_MAX_COURSES = 5


async def search_learn_courses(query: str, max_results: int = _MAX_COURSES) -> list[dict]:
    """搜索 Microsoft Learn 官方培训课程。

    Args:
        query: 技术方向关键词，如 "Copilot Studio for AI Agent"
        max_results: 最多返回课程数

    Returns:
        list[dict]: 课程列表，每个元素包含:
            - course_id: 课程 ID
            - title: 课程名称
            - summary: 课程摘要
            - url: 课程链接
            - duration: 时长（小时）
            - level: 难度（初级/中级/高级）
            - products: 相关产品列表
    """
    if not query or not query.strip():
        return []

    # 第一步：用 LLM 将用户技术方向映射为 Learn 产品 ID（更准确）
    product_ids = await _map_to_learn_products(query)

    # 第二步：调用 Learn Catalog API
    courses = await _fetch_catalog(query, product_ids, max_results)

    # 去重（按 course_id）
    seen = set()
    unique = []
    for c in courses:
        cid = c.get("course_id", "")
        if cid and cid not in seen:
            seen.add(cid)
            unique.append(c)
        elif not cid and c["title"] not in {x["title"] for x in unique}:
            unique.append(c)

    return unique[:max_results]


async def _map_to_learn_products(query: str) -> list[str]:
    """用 LLM 将技术方向映射为 Microsoft Learn 产品 ID。

    优先用 LLM 映射，失败时用静态映射表 fallback。
    """
    try:
        from src.llm.azure_client import chat_completion

        system = (
            "你是一个 Microsoft Learn 产品 ID 映射助手。"
            "根据用户的技术方向描述，返回对应的 Microsoft Learn 产品 ID 列表。"
            "产品 ID 是 Learn Catalog API 的 `products` 过滤参数值，"
            "例如：microsoft-copilot-studio、azure-ai、power-bi 等。"
            "只返回 JSON 数组，不要有其他内容。"
        )
        user = (
            f"用户技术方向：{query}\n\n"
            "请返回最相关的 1-3 个 Microsoft Learn 产品 ID（JSON 数组格式）。"
            "如果无法确定，返回空数组 []。"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        content = await chat_completion(
            messages,
            max_completion_tokens=200,
            reasoning_effort="low",
        )

        # 解析 JSON
        import json
        import re
        content = content.strip()
        # 尝试从 markdown 代码块提取
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
        if m:
            content = m.group(1).strip()
        try:
            product_ids = json.loads(content)
            if isinstance(product_ids, list) and all(isinstance(x, str) for x in product_ids):
                logger.info(f"Learn 产品映射 LLM 结果: {query} → {product_ids}")
                return product_ids
        except (json.JSONDecodeError, AttributeError):
            pass

    except Exception as e:
        logger.warning(f"Learn 产品 LLM 映射失败: {e}，将使用静态映射")

    # Fallback：静态映射
    query_lower = query.lower()
    matched = []
    for key, ids in _PRODUCT_ID_MAP.items():
        if key in query_lower:
            matched.extend(ids)
    if matched:
        logger.info(f"Learn 产品映射静态结果: {query} → {matched}")
    return matched


async def _fetch_catalog(query: str, product_ids: list[str], max_results: int) -> list[dict]:
    """调用 Learn Catalog API 获取课程列表"""
    headers = {
        "Accept": "application/json",
        "User-Agent": "zhice-agent/0.2",
    }

    # 并行尝试中文和英文
    locales = [_LEARN_LOCALE, "en-us"]

    async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
        tasks = []
        for locale in locales:
            params = {
                "locale": locale,
                "type": "learningPaths,modules",
                "pageSize": max_results + 5,
            }
            if product_ids:
                params["products"] = ",".join(product_ids)
            # 关键词搜索
            params["search"] = query[:100]

            tasks.append(client.get(_LEARN_API_URL, params=params))

        results = await asyncio.gather(*tasks, return_exceptions=True)

    courses = []
    for resp in results:
        if isinstance(resp, Exception):
            logger.warning(f"Learn API 请求异常: {resp}")
            continue
        if resp.status_code != 200:
            logger.warning(f"Learn API 返回 {resp.status_code}: {resp.text[:200]}")
            continue

        try:
            data = resp.json()
        except Exception:
            continue

        # 解析 learningPaths
        for lp in data.get("learningPaths", []):
            courses.append(_parse_learning_path(lp))

        # 解析 modules（作为补充）
        for mod in data.get("modules", []):
            courses.append(_parse_module(mod))

    return courses


def _parse_learning_path(lp: dict) -> dict:
    """解析 learningPath 为统一格式"""
    # 时长：PT4H30M → 4.5 小时
    duration_str = lp.get("duration", "")
    duration_hours = _parse_iso_duration(duration_str)

    return {
        "course_id": lp.get("learningPathId", lp.get("courseId", "")),
        "title": lp.get("title", ""),
        "summary": lp.get("summary", "") or lp.get("description", ""),
        "url": lp.get("url", "") or f"https://learn.microsoft.com/learn/{lp.get('learningPathId', '')}",
        "duration": duration_hours,
        "level": lp.get("level", ""),
        "products": lp.get("products", []),
        "type": "learningPath",
    }


def _parse_module(mod: dict) -> dict:
    """解析 module 为统一格式"""
    duration_str = mod.get("duration", "")
    duration_hours = _parse_iso_duration(duration_str)

    return {
        "course_id": mod.get("moduleId", mod.get("courseId", "")),
        "title": mod.get("title", ""),
        "summary": mod.get("summary", "") or mod.get("description", ""),
        "url": mod.get("url", ""),
        "duration": duration_hours,
        "level": mod.get("level", ""),
        "products": mod.get("products", []),
        "type": "module",
    }


def _parse_iso_duration(duration: str) -> float:
    """解析 ISO 8601 时长（PT4H30M）为小时数"""
    if not duration:
        return 0.0
    import re
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", duration)
    if not match:
        return 0.0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    return round(hours + minutes / 60, 1)


def format_courses_for_prompt(courses: list[dict]) -> str:
    """将课程列表格式化为 Prompt 可用的文本块"""
    if not courses:
        return "暂无相关微软官方培训课程信息。"

    lines = ["## 📚 微软官方培训课程参考\n"]
    for i, c in enumerate(courses, 1):
        title = c.get("title", "未知课程")
        course_id = c.get("course_id", "")
        summary = c.get("summary", "")[:150]
        url = c.get("url", "")
        duration = c.get("duration", 0)
        level = c.get("level", "")

        line = f"{i}. **[{title}]({url})**"
        if course_id:
            line += f" `{course_id}`"
        if duration:
            line += f" ({duration}h)"
        if level:
            line += f" · {level}"
        lines.append(line)

        if summary:
            lines.append(f"   > {summary}...")

        if url:
            lines.append(f"   🔗 {url}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 课程详情抓取（用于知识库：大纲 / 学员对象 / 技术面 / 天数）
# 数据源：Microsoft Learn Catalog API `courses` 类型
# ─────────────────────────────────────────────────────────────

def _strip_html(raw_html: str) -> str:
    """剥离 HTML 标签，保留纯文本"""
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_audience_profile(summary_html: str) -> str:
    """从课程 summary HTML 中提取 Audience Profile 段落文本"""
    if not summary_html:
        return ""
    m = re.search(
        r'id=["\']audience-profile["\'][^>]*>(.*?)(?=<h4|$)',
        summary_html, re.S | re.I,
    )
    if not m:
        return ""
    return _strip_html(m.group(1))


async def _fetch_catalog_type(type_name: str, locale: str, page_size: int = 500) -> list:
    """拉取 Catalog API 某类型的全量数据（处理分页）"""
    headers = {"Accept": "application/json", "User-Agent": "zhice-agent/0.2"}
    results: list = []
    params = {"locale": locale, "type": type_name, "pageSize": page_size}
    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        skip = 0
        while True:
            params["skip"] = skip
            try:
                resp = await client.get(_LEARN_API_URL, params=params)
                if resp.status_code != 200:
                    logger.warning(f"Learn API {type_name} 返回 {resp.status_code}")
                    break
                data = resp.json()
            except Exception as e:
                logger.warning(f"Learn API {type_name} 请求失败: {e}")
                break

            # 数据键名可能为 learningPaths / modules / courses
            key = None
            for k in ("learningPaths", "modules", "courses"):
                if k in data:
                    key = k
                    break
            items = data.get(key, []) if key else []
            if not items:
                break
            results.extend(items)
            if len(items) < page_size:
                break
            skip += page_size
    return results


async def fetch_course_detail(course_number: str, locale: str = "en-us") -> dict | None:
    """按课程编号精确抓取单门课原始 JSON（本地匹配 course_number）"""
    if not course_number:
        return None
    target = course_number.strip().upper()
    courses = await _fetch_catalog_type("courses", locale)
    for c in courses:
        if str(c.get("course_number", "")).upper() == target:
            return c
    for c in courses:  # 兜底模糊匹配
        if target in str(c.get("course_number", "")).upper():
            return c
    logger.warning(f"未找到课程: {course_number}")
    return None


def parse_course_detail(raw: dict) -> dict:
    """将原始 course JSON 解析为结构化课程知识 dict

    天数口径：duration_days = duration_in_hours / 6（用户确认 6 学时=1天）
    """
    summary_html = raw.get("summary", "")
    duration_hours = raw.get("duration_in_hours", 0) or 0
    duration_days = round(duration_hours / 6, 1) if duration_hours else 0

    study_guide = raw.get("study_guide", []) or []
    lp_uids = [i.get("uid") for i in study_guide if i.get("type") == "learningPath"]

    return {
        "course_number": raw.get("course_number", ""),
        "title": raw.get("title", ""),
        "summary_text": _strip_html(summary_html),
        "audience_profile": _extract_audience_profile(summary_html),
        "roles": raw.get("roles", []),
        "products": raw.get("products", []),
        "duration_in_hours": duration_hours,
        "duration_days": duration_days,
        "levels": raw.get("levels", []),
        "study_guide_uids": lp_uids,
        "url": raw.get("url", ""),
        "locales": raw.get("locales", ["en"]),
    }


async def expand_outline(study_guide_uids: list, locale: str = "en-us") -> str:
    """展开大纲：learningPath 标题 + 其下属 module 标题

    策略：拉全量 learningPaths + modules 后在本地按 uid 匹配，
    仅需 2 次 API 调用即可展开任意课程大纲。
    """
    if not study_guide_uids:
        return ""

    lps = await _fetch_catalog_type("learningPaths", locale)
    lp_map = {lp["uid"]: lp for lp in lps if lp.get("uid")}

    mod_uids: list = []
    for uid in study_guide_uids:
        lp = lp_map.get(uid)
        if lp:
            mod_uids.extend(lp.get("modules", []))

    mods = await _fetch_catalog_type("modules", locale)
    mod_map = {m["uid"]: m for m in mods if m.get("uid")}

    lines: list = []
    for uid in study_guide_uids:
        lp = lp_map.get(uid)
        if not lp:
            continue
        lines.append(f"## {lp.get('title', '')}")
        for muid in lp.get("modules", []):
            m = mod_map.get(muid)
            if m:
                lines.append(f"- {m.get('title', '')}")
    return "\n".join(lines)


async def fetch_course_full(course_number: str, locale: str = "en-us") -> dict:
    """组合：抓取 + 解析 + 展开大纲，返回完整课程知识 dict（含 outline）"""
    raw = await fetch_course_detail(course_number, locale)
    if not raw:
        return {}
    parsed = parse_course_detail(raw)
    parsed["outline"] = await expand_outline(parsed["study_guide_uids"], locale)
    return parsed


def format_course_knowledge(parsed: dict) -> str:
    """将完整课程知识格式化为知识库 chunk 文本（用于 ChromaDB 存储）"""
    if not parsed:
        return ""
    lines = [
        f"# {parsed['course_number']} — {parsed['title']}",
        f"课程编号: {parsed['course_number']}",
        f"技术方向: {', '.join(parsed.get('products', []))}",
        f"学员对象(角色): {', '.join(parsed.get('roles', []))}",
        f"难度: {', '.join(parsed.get('levels', []))}",
        f"时长: {parsed.get('duration_in_hours', 0)} 学时 (约 {parsed.get('duration_days', 0)} 天)",
        f"课程链接: {parsed.get('url', '')}",
        "",
        "## 学员对象详述",
        parsed.get("audience_profile", "") or "（无）",
        "",
        "## 课程大纲",
        parsed.get("outline", "") or "（无）",
    ]
    return "\n".join(lines)
