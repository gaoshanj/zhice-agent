"""RAG 检索器 — Phase 2"""

from __future__ import annotations

from typing import Any

from src.rag.vector_store import similarity_search
from src.utils.config import settings
from src.utils.logger import logger


SECTION_QUERY_MAP: dict[int, str] = {
    1: "公司介绍 产品介绍 服务体系 客户案例",
    2: "培训课程 认证体系 技术方案 产品优势",
    3: "交叉销售 增值服务 客户成功案例",
    4: "销售策略 竞争分析 定价策略 客户痛点",
    5: "沟通话术 常见问答 客户异议处理",
    6: "行动建议 跟进计划 下一步",
}


def retrieve_for_report(
    company: str,
    section_num: int,
    top_k: int = 4,
) -> list[str]:
    """为报告生成检索相关内部知识（飞书 Wiki）

    Args:
        company: 目标客户公司名
        section_num: 当前章节编号（1~6）
        top_k: 返回最相似的 N 条

    Returns:
        相关文本片段列表（已去重、按相关度排序）
    """
    # 构造查询：公司名 + 章节主题
    topic = SECTION_QUERY_MAP.get(section_num, "")
    query = f"{company} {topic}".strip()

    logger.info(f"RAG 检索: section={section_num}, query='{query}'")

    results = similarity_search(
        query=query,
        top_k=top_k,
        collection_name=settings.chroma_collection_internal,
    )

    if not results:
        logger.info("RAG 检索: 无结果")
        return []

    # 提取文本内容，去重
    seen: set[str] = set()
    contexts: list[str] = []
    for r in results:
        content = r.get("content", "").strip()
        # 用前 50 字做去重
        dedup_key = content[:50]
        if dedup_key not in seen and len(content) > 30:
            seen.add(dedup_key)
            contexts.append(content)

    logger.info(f"RAG 检索完成: {len(contexts)} 条相关内容")
    return contexts


def format_rag_context(contexts: list[str], max_chars: int = 2000) -> str:
    """将检索结果格式化为 Prompt 上下文

    Args:
        contexts: retrieve_for_report() 的返回值
        max_chars: 截断上限（避免 Prompt 过长）

    Returns:
        格式化后的上下文字符串
    """
    if not contexts:
        return ""

    parts: list[str] = ["【内部知识库参考内容（来自飞书 Wiki）】"]
    total = 0
    for i, ctx in enumerate(contexts, 1):
        if total + len(ctx) > max_chars:
            parts.append(f"...（已截断，共 {i - 1} 条）")
            break
        parts.append(f"■ 参考 {i}：\n{ctx}")
        total += len(ctx)

    return "\n\n".join(parts)
