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
    """为报告生成检索相关知识（内部 + 外部双集合）—— 返回纯文本列表（兼容旧接口）"""
    results = retrieve_for_report_with_meta(company, section_num, top_k)
    return [r["content"] for r in results]


def _build_source_url(metadata: dict[str, Any]) -> str:
    """根据 metadata 构造来源链接"""
    source = metadata.get("source", "")
    if source == "bitable":
        base_token = metadata.get("base_token", "")
        table_id = metadata.get("table_id", "")
        record_id = metadata.get("record_id", "")
        if base_token and table_id:
            # 飞书 Bitable 记录链接（含 record_id 定位到具体行）
            url = f"https://bba12hub36.feishu.cn/base/{base_token}?table={table_id}"
            if record_id:
                url += f"&record={record_id}"
            return url
    elif source == "external":
        url = metadata.get("url", "")
        if url:
            return url
    return ""


def retrieve_for_report_with_meta(
    company: str,
    section_num: int,
    top_k: int = 4,
) -> list[dict[str, Any]]:
    """为报告生成检索相关知识（内部 + 外部双集合）—— 返回带元数据的结果

    Returns:
        [{"content": str, "metadata": dict, "distance": float,
          "source_url": str, "source_type": str, "title": str}, ...]
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

    # ── 合并 + 去重 + 附加来源信息 ──────────────────────────────
    all_results = internal_results + external_results
    if not all_results:
        logger.info("RAG 检索: 无结果")
        return []

    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for r in all_results:
        content = r.get("content", "").strip()
        dedup_key = content[:50]
        if dedup_key in seen or len(content) <= 30:
            continue
        seen.add(dedup_key)

        meta = r.get("metadata", {})
        source_url = _build_source_url(meta)
        source_type = meta.get("source", "unknown")
        title = meta.get("title", "")

        output.append({
            "content": content,
            "metadata": meta,
            "distance": r.get("distance"),
            "source_url": source_url,
            "source_type": source_type,
            "title": title or ("外部数据" if source_type == "external" else "Bitable 记录"),
        })

    logger.info(
        f"RAG 检索完成: {len(internal_results)} 内部 + "
        f"{len(external_results)} 外部 = {len(output)} 条（去重后）"
    )
    return output


def format_rag_context(
    contexts: list[str] | list[dict[str, Any]],
    max_chars: int = 2500,
) -> str:
    """将检索结果格式化为 Prompt 上下文

    Args:
        contexts: retrieve_for_report() 或 retrieve_for_report_with_meta() 的返回值
        max_chars: 截断上限（避免 Prompt 过长）

    Returns:
        格式化后的上下文字符串（含来源标记 [来源1] [来源2] 等）
    """
    if not contexts:
        return ""

    # 统一转为结构化 dict（兼容旧接口传 list[str]）
    structured: list[dict[str, Any]] = []
    for ctx in contexts:
        if isinstance(ctx, dict):
            structured.append(ctx)
        else:
            is_external = "外部数据" in ctx[:100]
            structured.append({
                "content": ctx,
                "source_type": "external" if is_external else "bitable",
                "source_url": "",
                "title": "",
            })

    internal = [c for c in structured if c.get("source_type") != "external"]
    external = [c for c in structured if c.get("source_type") == "external"]

    parts: list[str] = []
    source_index = 0

    if internal:
        parts.append("【内部知识库参考内容（来自飞书 Bitable）】")
        total = 0
        for i, ctx in enumerate(internal, 1):
            content = ctx["content"]
            if total + len(content) > max_chars * 0.7:
                parts.append(f"...（已截断，共 {i - 1} 条）")
                break
            source_index += 1
            ctx["_cite_id"] = source_index
            parts.append(f"■ 参考 {i} [来源{source_index}]：\n{content}")
            total += len(content)

    if external:
        parts.append("\n【外部数据参考内容（来自互联网公开信息）】")
        total = 0
        for i, ctx in enumerate(external, 1):
            content = ctx["content"]
            if total + len(content) > max_chars * 0.3:
                break
            source_index += 1
            ctx["_cite_id"] = source_index
            parts.append(f"■ 外部 {i} [来源{source_index}]：\n{content}")
            total += len(content)

    # 追加来源索引表（供 LLM 引用）
    if any(c.get("source_url") for c in structured):
        parts.append("\n【来源索引】")
        for c in structured:
            cid = c.get("_cite_id")
            url = c.get("source_url", "")
            if cid and url:
                parts.append(f"[来源{cid}] {c.get('title', '记录')} — {url}")

    return "\n\n".join(parts)
