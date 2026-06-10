"""RAG 检索器 — Phase 3（内部知识库 + 外部爬虫数据双集合检索）"""

from __future__ import annotations

from typing import Any

from src.rag.vector_store import similarity_search, collection_count
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
    """为报告生成检索相关知识（内部 + 外部双集合）

    Args:
        company: 目标客户公司名
        section_num: 当前章节编号（1~6）
        top_k: 每个集合返回最相似的 N 条

    Returns:
        相关文本片段列表（已去重、按相关度排序）
    """
    topic = SECTION_QUERY_MAP.get(section_num, "")
    query = f"{company} {topic}".strip()

    logger.info(f"RAG 检索: section={section_num}, query='{query}'")

    # ── 内部知识库检索 ──────────────────────────────────────────
    internal_results = similarity_search(
        query=query,
        top_k=top_k,
        collection_name=settings.chroma_collection_internal,
    )

    # ── 外部数据检索（爬虫）──────────────────────────────────────
    # Section 1（客户快照）和 Section 2（商机扫描）最能从外部数据获益
    external_top_k = top_k if section_num in (1, 2) else 2
    external_results: list[dict[str, Any]] = []

    ext_count = collection_count(settings.chroma_collection_external)
    if ext_count > 0:
        external_results = similarity_search(
            query=query,
            top_k=external_top_k,
            collection_name=settings.chroma_collection_external,
        )
        if external_results:
            logger.info(f"RAG 检索（外部）: {len(external_results)} 条结果")
    else:
        logger.debug("外部数据集合为空，跳过外部检索")

    # ── 合并 + 去重 ─────────────────────────────────────────────
    all_results = internal_results + external_results
    if not all_results:
        logger.info("RAG 检索: 无结果")
        return []

    # 去重（基于内容前 50 字）
    seen: set[str] = set()
    contexts: list[str] = []
    for r in all_results:
        content = r.get("content", "").strip()
        dedup_key = content[:50]
        if dedup_key not in seen and len(content) > 30:
            seen.add(dedup_key)
            contexts.append(content)

    logger.info(
        f"RAG 检索完成: {len(internal_results)} 内部 + "
        f"{len(external_results)} 外部 = {len(contexts)} 条（去重后）"
    )
    return contexts


def format_rag_context(
    contexts: list[str],
    max_chars: int = 2500,
) -> str:
    """将检索结果格式化为 Prompt 上下文

    Args:
        contexts: retrieve_for_report() 的返回值
        max_chars: 截断上限（避免 Prompt 过长）

    Returns:
        格式化后的上下文字符串
    """
    if not contexts:
        return ""

    # 分离内部和外部数据（通过内容前缀判断）
    internal: list[str] = []
    external: list[str] = []
    for ctx in contexts:
        if "外部数据" in ctx[:100]:
            external.append(ctx)
        else:
            internal.append(ctx)

    parts: list[str] = []

    if internal:
        parts.append("【内部知识库参考内容（来自飞书 Bitable）】")
        total = 0
        for i, ctx in enumerate(internal, 1):
            if total + len(ctx) > max_chars * 0.7:  # 内部数据占 70%
                parts.append(f"...（已截断，共 {i - 1} 条）")
                break
            parts.append(f"■ 参考 {i}：\n{ctx}")
            total += len(ctx)

    if external:
        parts.append("\n【外部数据参考内容（来自互联网公开信息）】")
        total = 0
        for i, ctx in enumerate(external, 1):
            if total + len(ctx) > max_chars * 0.3:  # 外部数据占 30%
                break
            parts.append(f"■ 外部 {i}：\n{ctx}")
            total += len(ctx)

    return "\n\n".join(parts)
