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
