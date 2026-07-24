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
    4: "微软培训课程 官方课程 课程大纲 学员对象 受众",
    5: "销售策略 竞争分析 定价策略 客户痛点",
    6: "沟通话术 常见问答 客户异议处理",
    7: "行动建议 跟进计划 下一步",
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
    """根据 metadata 构造来源链接

    爬虫 source 值规则：
      - bitable          ← 飞书 Bitable 知识库
      - external_website ← 公司官网（URL 存在 website_url 字段）
      - external_jobs    ← 招聘数据（无独立 URL）
      - external_news    ← 新闻数据（无独立 URL）
      - external         ← 通用外部（URL 存在 url 字段，旧格式兼容）
    """
    source = metadata.get("source", "")
    if source == "bitable":
        base_token = metadata.get("base_token", "")
        table_id = metadata.get("table_id", "")
        record_id = metadata.get("record_id", "")
        if base_token and table_id:
            url = f"https://bba12hub36.feishu.cn/base/{base_token}?table={table_id}"
            if record_id:
                url += f"&record={record_id}"
            return url
    elif source == "external_website":
        # 官网爬虫将 URL 存为 website_url
        url = metadata.get("website_url", "") or metadata.get("url", "")
        return url
    elif source in ("external", "external_news", "external_jobs"):
        # 旧格式 / 新闻 / 招聘：尝试 url 字段
        url = metadata.get("url", "") or metadata.get("website_url", "")
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
        # ⚠️ 必须按 company 过滤，否则向量语义搜索会返回其他公司的数据（跨公司泄漏）
        external_results = similarity_search(
            query=query,
            top_k=external_top_k,
            collection_name=settings.chroma_collection_external,
            filter_dict={"company": company},
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
        raw_source = meta.get("source", "unknown")
        # 将所有 external_* 统一归类为 "external"，方便下游处理
        source_type = "external" if raw_source.startswith("external") else raw_source
        title = meta.get("title", "")
        # 根据 raw_source 生成更具体的标题
        if not title:
            if raw_source == "external_website":
                title = f"{meta.get('company', '')} 官网"
            elif raw_source == "external_jobs":
                title = f"{meta.get('company', '')} 招聘信息"
            elif raw_source == "external_news":
                title = f"{meta.get('company', '')} 行业新闻"
            elif source_type == "external":
                title = "外部数据"
            else:
                title = "Bitable 记录"

        output.append({
            "content": content,
            "metadata": meta,
            "distance": r.get("distance"),
            "source_url": source_url,
            "source_type": source_type,
            "title": title,
        })

    logger.info(
        f"RAG 检索完成: {len(internal_results)} 内部 + "
        f"{len(external_results)} 外部 = {len(output)} 条（去重后）"
    )
    return output


def format_rag_context(
    contexts: list[str] | list[dict[str, Any]],
    max_chars: int = 2500,
) -> tuple[str, dict[int, str]]:
    """将检索结果格式化为 Prompt 上下文

    Args:
        contexts: retrieve_for_report() 或 retrieve_for_report_with_meta() 的返回值
        max_chars: 截断上限（避免 Prompt 过长）

    Returns:
        (formatted_text, source_map) — formatted_text 含来源标记 [来源1] [来源2]，
        source_map 为 {cite_id: url} 供下游将 [来源N] 替换为超链接。
    """
    if not contexts:
        return "", {}

    # 统一转为结构化 dict（兼容旧接口传 list[str]）
    structured: list[dict[str, Any]] = []
    for ctx in contexts:
        if isinstance(ctx, dict):
            structured.append(ctx)
        else:
            # 旧接口传 str 时：通过关键词猜测是否外部数据
            is_external = any(kw in ctx[:150] for kw in ("外部数据", "官网信息", "招聘信息", "行业新闻"))
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
    source_map: dict[int, str] = {}
    if any(c.get("source_url") for c in structured):
        parts.append("\n【来源索引】")
        for c in structured:
            cid = c.get("_cite_id")
            url = c.get("source_url", "")
            if cid and url:
                parts.append(f"[来源{cid}] {c.get('title', '记录')} — {url}")
                source_map[cid] = url

    return "\n\n".join(parts), source_map


def course_search(query: str, top_k: int = 2) -> list[dict[str, Any]]:
    """在 course_docs 集合中语义检索最相关的培训课程，用于融入销售策略报告。

    根据客户的技术方向描述，从课程知识库中检索最相关的 1-2 门课程，
    返回其完整知识文本（大纲 / 学员对象 / 技术面 / 天数 / 链接），
    供 LLM 撰写「课程销售方案」并自然融入回复。

    Args:
        query: 客户技术方向描述（如 "Copilot Studio AI Agent" 或 "Azure AI 应用开发"）
        top_k: 返回最相关的 N 门课（默认 2）
    Returns:
        [{"course_number", "title", "content", "url", "distance"}, ...]
    """
    name = settings.chroma_collection_course
    if collection_count(name) == 0:
        logger.info("course_docs 集合为空，跳过课程检索")
        return []
    try:
        results = similarity_search(query=query, top_k=top_k, collection_name=name)
    except Exception as e:
        logger.warning(f"course_docs 检索失败: {e}")
        return []

    output: list[dict[str, Any]] = []
    for r in results:
        meta = r.get("metadata", {})
        output.append({
            "course_number": meta.get("course_number", ""),
            "title": meta.get("title", ""),
            "content": r.get("content", ""),
            "url": meta.get("url", "") or "",
            "distance": r.get("distance"),
        })
    logger.info(f"course_docs 检索: query='{query[:40]}' → {len(output)} 门课")
    return output
