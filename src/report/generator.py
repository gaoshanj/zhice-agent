"""报告生成主逻辑 — Phase 4 路线B（CompanyContext 结构化注入）"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from src.llm.azure_client import chat_completion, classify_llm_error
from src.llm.prompt_templates import build_section_messages
from src.models.company_context import CompanyContext
from src.utils.config import settings
from src.utils.logger import logger


SECTION_NAMES = [
    "snapshot",
    "opportunity_scan",
    "cross_sell",
    "course_plan",
    "strategy",
    "talk_script",
    "action_plan",
]

SECTION_LABELS = [
    "客户 360° 快照",
    "培训商机深度扫描",
    "交叉销售机会挖掘",
    "微软培训课程方案",
    "销售策略建议",
    "推荐销售话术",
    "行动建议",
]


async def generate_report(
    parsed: dict[str, Any],
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """生成完整的销售策略报告。

    Phase 3：外部爬虫 + 内部 RAG 双源检索。
    流程：① 爬取公司外部数据（招聘+官网） → ② 内部+外部 RAG 检索 → ③ 逐节生成

    Args:
        parsed: parse_user_input() 的返回值
        reasoning_effort: 推理强度，传给底层 LLM 调用（"low"/"medium"/"high"）。
                          None=模型默认。设为 "low" 可大幅加速（减少推理 token 消耗）。

    Returns:
        dict: 包含各节内容和元数据的报告字典
    """
    company = parsed.get("company", "未知客户")
    logger.info(f"开始生成报告: {company}")

    # ── Phase 3：启动外部爬虫（并行运行，等待完成后再生成报告主章节）──
    # 爬虫数据对 Section 1/2 的质量至关重要（公司介绍、招聘分析等），
    # 所以先启动爬虫、等待完成（最多 35s），再开始 RAG 检索和章节生成。
    # 如果爬虫超时或失败，不影响后续流程 — 继续用已有数据生成。
    from src.crawler.crawler_dispatcher import crawl_and_store
    logger.info(f"Phase 3: 启动外部爬虫 → {company}")
    crawl_task = asyncio.create_task(
        asyncio.wait_for(crawl_and_store(company=company, timeout=35.0), timeout=40.0)
    )
    company_ctx: CompanyContext | None = None
    try:
        crawl_result = await crawl_task
        if crawl_result.get("chunks_stored", 0) > 0:
            logger.info(
                f"爬虫完成: {company} — "
                f"{crawl_result.get('jobs_count', 0)} 职位, "
                f"{crawl_result.get('chunks_stored', 0)} chunks"
            )
        else:
            logger.info(f"爬虫完成: {company} — 无新增外部数据")
        # 路线B：提取 CompanyContext（含完整 URL 溯源）
        company_ctx = crawl_result.get("context")
        if company_ctx and company_ctx.has_any_data:
            logger.info(
                f"CompanyContext 已构建: "
                f"官网={'有' if company_ctx.website_summary else '无'}, "
                f"职位={company_ctx.job_count}, "
                f"新闻={company_ctx.news_count}"
            )
    except asyncio.TimeoutError:
        logger.warning(f"外部爬虫超时: {company}（继续使用已有数据生成报告）")
    except Exception as e:
        logger.warning(f"外部爬虫异常: {company} — {e}（继续使用已有数据生成报告）")

    # ── 微软官方培训课程检索（基于 course_docs 知识库，离线语义检索）──
    # 根据客户技术方向，从课程知识库中检索最相关的 1-2 门课程，
    # 将其完整知识（大纲/学员对象/技术面/天数/链接）作为销售方案素材融入报告。
    learn_courses_text = "暂无相关微软官方培训课程信息。"
    tech_query = parsed.get("tech_product", "")
    if not tech_query and parsed.get("focus_areas"):
        # fallback：用 focus_areas 拼接为检索关键词
        tech_query = " ".join(parsed.get("focus_areas", []))
    if not tech_query and parsed.get("visit_purpose"):
        tech_query = parsed.get("visit_purpose", "")

    if tech_query:
        logger.info(f"course_docs 检索: {company} — 技术方向：{tech_query}")
        try:
            from src.rag.retriever import course_search
            courses = course_search(tech_query, top_k=2)
            if courses:
                learn_courses_text = "\n\n".join(c["content"] for c in courses)
                logger.info(
                    f"course_docs 检索到 {len(courses)} 门相关课程: "
                    f"{[c['course_number'] for c in courses]}"
                )
        except Exception as e:
            logger.warning(f"course_docs 检索失败: {e}（继续生成报告）")
    else:
        logger.info(f"course_docs 未触发: {company} — 未识别到技术产品方向")

    # 准备模板变量
    template_vars = {
        "company": company,
        "visit_target": parsed.get("visit_target", "未指定"),
        "known_info": parsed.get("known_info", "无"),
        "visit_purpose": parsed.get("visit_purpose", "未指定"),
        "focus_areas": "、".join(parsed.get("focus_areas", [])) or "未指定",
        "special_req": parsed.get("special_req", "无"),
        "learn_courses": learn_courses_text or "暂无相关微软官方培训课程信息。",
        "course_plan": "",
        "snapshot": "",
        "opportunity_scan": "",
        "cross_sell": "",
        "strategy": "",
    }

    report_data: dict[str, Any] = {
        "company": company,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "sections": {},
        "sources": [],  # RAG 来源链接列表
    }

    t_report_start = time.monotonic()

    # ── 顺序生成有依赖的章节（1→2→4→5：快照/商机/课程方案/策略）──
    sequential_sections = [1, 2, 4, 5]
    for section_num in sequential_sections:
        label = SECTION_LABELS[section_num - 1]
        logger.info(f"生成第 {section_num} 节: {label}")

        try:
            content = await _generate_section(
                section_num=section_num,
                template_vars=template_vars,
                max_tokens=4000,
                reasoning_effort=reasoning_effort,
                company_ctx=company_ctx,
            )
            key = SECTION_NAMES[section_num - 1]
            report_data[key] = content
            report_data["sections"][str(section_num)] = content

            # 将本节内容注入模板变量，供后续章节参考
            if section_num == 1:
                template_vars["snapshot"] = _truncate(content, 200)
            elif section_num == 2:
                template_vars["opportunity_scan"] = _truncate(content, 200)
            elif section_num == 4:
                # 课程方案：完整文本注入，供「销售策略建议」引用课程名称/大纲
                template_vars["course_plan"] = content
            elif section_num == 5:
                template_vars["strategy"] = _truncate(content, 200)

        except Exception as e:
            error_msg = str(e)
            logger.error(f"第 {section_num} 节生成失败: {error_msg}")
            key = SECTION_NAMES[section_num - 1]
            report_data[key] = f"[生成失败：{error_msg[:200]}]"

    # ── Section 3 交叉销售（固定占位，不调用 LLM）──────────────
    content = "🚧 交叉销售机会分析功能暂未上线，敬请期待。"
    report_data["cross_sell"] = content
    report_data["sections"]["3"] = content
    template_vars["cross_sell"] = content

    # ── 并行生成无依赖的章节（6 和 7 都只依赖 S5 的 strategy）───
    logger.info("并行生成第 6、7 节...")
    s6_task = asyncio.create_task(
        _generate_section(6, template_vars, max_tokens=4000, reasoning_effort=reasoning_effort, company_ctx=company_ctx)
    )
    s7_task = asyncio.create_task(
        _generate_section(7, template_vars, max_tokens=4000, reasoning_effort=reasoning_effort, company_ctx=company_ctx)
    )

    try:
        s6_content = await s6_task
        report_data["talk_script"] = s6_content
        report_data["sections"]["6"] = s6_content
    except Exception as e:
        logger.error(f"第 6 节生成失败: {e}")
        report_data["talk_script"] = f"[生成失败：{str(e)[:200]}]"

    try:
        s7_content = await s7_task
        report_data["action_plan"] = s7_content
        report_data["sections"]["7"] = s7_content
    except Exception as e:
        logger.error(f"第 7 节生成失败: {e}")
        report_data["action_plan"] = f"[生成失败：{str(e)[:200]}]"

    # ── 收集 RAG 来源（去重）──────────────────────────────────
    report_data["sources"] = _collect_sources(company, company_ctx)

    t_report_elapsed = time.monotonic() - t_report_start
    logger.info(
        f"报告生成完成: {company}，总耗时 {t_report_elapsed:.1f}s，"
        f"共 {len([k for k in report_data if k not in ('company','generated_at','sections','sources')])} 节"
    )
    return report_data


def _collect_sources(
    company: str,
    company_ctx: CompanyContext | None = None,
) -> list[dict[str, str]]:
    """收集该公司在 RAG 中检索到的所有 Bitable 来源 + CompanyContext URL（去重）"""
    from src.rag.retriever import retrieve_for_report_with_meta

    seen_urls: set[str] = set()
    sources: list[dict[str, str]] = []

    # 对每一节都检索一次，收集所有 Bitable 来源
    for section_num in (1, 2, 5, 6, 7):
        try:
            results = retrieve_for_report_with_meta(company, section_num, top_k=3)
            for r in results:
                url = r.get("source_url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    sources.append({
                        "title": r.get("title", "Bitable 记录"),
                        "url": url,
                        "type": r.get("source_type", "bitable"),
                    })
        except Exception:
            continue

    # 路线B：追加 CompanyContext 中的 URL（官网、招聘、新闻）
    if company_ctx:
        for cid, label, url in company_ctx.source_urls:
            if url and url not in seen_urls:
                seen_urls.add(url)
                sources.append({
                    "title": label,
                    "url": url,
                    "type": "external",
                })

    return sources


async def _generate_section(
    section_num: int,
    template_vars: dict[str, Any],
    max_retries: int = 1,
    max_tokens: int = 4000,
    reasoning_effort: str | None = None,
    company_ctx: CompanyContext | None = None,
) -> str:
    """生成单个章节，带智能重试逻辑（Phase 4 路线B：CompanyContext 结构化注入）

    重试策略：
      - 瞬态错误（超时/限流/连接失败）: 指数退避重试
      - 非瞬态错误（认证失败/部署不存在/参数错误）: 立即失败，不重试
      - 推理模型空输出: 倍增加大 max_completion_tokens 后重试

    Args:
        reasoning_effort: 推理强度（"low"/"medium"/"high"），None=模型默认。
                         设为 "low" 可大幅加速生成（减少推理 token 占比）。
        company_ctx: 路线B — 结构化公司外部数据上下文（含完整 URL 溯源）。
                     非 None 时直接注入 Prompt，绕过 ChromaDB metadata。
    """
    from src.llm.azure_client import (
        chat_completion, classify_llm_error, is_reasoning_model,
        REASONING_MIN_EFFECTIVE,
    )
    from src.rag.retriever import retrieve_for_report_with_meta, format_rag_context

    t_start = time.monotonic()
    company = template_vars.get("company", "")
    # 课程方案（第 4 节）仅依赖 course_docs 素材，不注入内部/外部 RAG，
    # 避免把公司知识库来源编号 [来源N] 混进课程卡片。
    if section_num == 4:
        contexts = []
    else:
        contexts = retrieve_for_report_with_meta(company, section_num)
    rag_context, source_map = format_rag_context(contexts)

    if rag_context:
        logger.info(f"第 {section_num} 节已注入 RAG 上下文（{len(contexts)} 条）")
    else:
        logger.debug(f"第 {section_num} 节无 RAG 结果")

    messages = build_section_messages(
        section_num=section_num,
        rag_context=rag_context,
        **template_vars,
    )

    # ── 路线B：注入 CompanyContext 结构化数据（含完整 URL 溯源）────
    # 将爬虫产出的公司上下文作为独立 user message 追加，
    # 这样 URL 不经过 ChromaDB metadata，100% 传递到 LLM。
    if company_ctx and company_ctx.has_any_data:
        ctx_text = company_ctx.format_for_prompt()
        if ctx_text:
            messages.append({"role": "user", "content": ctx_text})
            logger.info(f"第 {section_num} 节已注入 CompanyContext（{len(ctx_text)} 字符）")
            # 将 CompanyContext 的 URL 合并到 source_map，使 _linkify_sources 生效
            for cid, label, url in company_ctx.source_urls:
                source_map[cid] = url

    # 推理模型自适应 token 预算
    # reasoning_effort 显式设为 low/medium 时跳过自动提升（模型不会再耗尽预算）
    from src.utils.config import settings
    is_reasoning = is_reasoning_model(settings.azure_openai_deployment)
    effective_tokens = max_tokens
    skip_token_upgrade = reasoning_effort in ("low", "medium")
    if is_reasoning and not skip_token_upgrade and effective_tokens < REASONING_MIN_EFFECTIVE:
        effective_tokens = REASONING_MIN_EFFECTIVE

    last_error: Exception | None = None

    for attempt in range(1, max_retries + 2):  # 首次 + max_retries 次重试
        try:
            content = await chat_completion(
                messages,
                temperature=0.3,
                max_tokens=effective_tokens,
                reasoning_effort=reasoning_effort,
            )
            if content and len(content.strip()) > 20:
                # 后处理：将 [来源N] 替换为可点击的 Markdown 超链接
                content = _linkify_sources(content, source_map)
                elapsed = time.monotonic() - t_start
                logger.info(
                    f"第 {section_num} 节 LLM 生成完成"
                    f"（{len(content)} 字，{elapsed:.1f}s）"
                )
                return content.strip()

            # 空内容/过短：推理模型常见的 token 预算问题
            if not content and is_reasoning:
                old_tokens = effective_tokens
                effective_tokens = min(effective_tokens + 6000, 20000)
                logger.warning(
                    f"第 {section_num} 节返回空内容，可能是推理预算不足："
                    f"max_completion_tokens {old_tokens} → {effective_tokens}，重试..."
                )
            else:
                logger.warning(
                    f"第 {section_num} 节返回内容过短 "
                    f"({len(content) if content else 0} chars)，重试 {attempt}"
                )
        except Exception as e:
            last_error = e
            reason, retryable = classify_llm_error(e)
            logger.warning(
                f"第 {section_num} 节第 {attempt} 次尝试失败: {reason}"
            )

            if not retryable:
                raise RuntimeError(f"第 {section_num} 节失败 — {reason}") from e

            if attempt <= max_retries:
                backoff = min(2 * attempt, 8)
                logger.info(f"第 {section_num} 节等待 {backoff}s 后重试...")
                await asyncio.sleep(backoff)
            else:
                break

    # 所有重试均失败
    if last_error:
        reason, _ = classify_llm_error(last_error)
    else:
        reason = "推理模型返回空内容（token 预算可能不足）"
    raise RuntimeError(f"第 {section_num} 节失败 — {reason}（已重试 {max_retries} 次）")


def _linkify_sources(content: str, source_map: dict[int, str]) -> str:
    """将报告内容中的 [来源N] 纯文本标记替换为 Markdown 超链接。

    Args:
        content: LLM 生成的报告文本
        source_map: {cite_id: url}（来自 format_rag_context 返回值）

    Returns:
        替换后的文本，[来源1] → [来源1](url)
    """
    if not source_map:
        return content

    # 按 cite_id 降序替换，避免替换 [来源1] 时意外影响 [来源10]、[来源11] 等
    for cid in sorted(source_map, reverse=True):
        url = source_map[cid]
        marker = f"[来源{cid}]"
        replacement = f"[来源{cid}]({url})"
        content = content.replace(marker, replacement)
    return content


def _truncate(text: str, max_len: int = 300) -> str:
    """截断文本，用于注入后续 Prompt 的上下文"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "...（截断）"
