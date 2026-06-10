"""报告生成主逻辑 — Phase 3（外部爬虫 + 内部 RAG 双源检索）"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from src.llm.azure_client import chat_completion
from src.llm.prompt_templates import build_section_messages
from src.utils.config import settings
from src.utils.logger import logger


SECTION_NAMES = [
    "snapshot",
    "opportunity_scan",
    "cross_sell",
    "strategy",
    "talk_script",
    "action_plan",
]

SECTION_LABELS = [
    "客户 360° 快照",
    "培训商机深度扫描",
    "交叉销售机会挖掘",
    "销售策略建议",
    "推荐销售话术",
    "行动建议",
]


async def generate_report(parsed: dict[str, Any]) -> dict[str, Any]:
    """生成完整的销售策略报告。

    Phase 3：外部爬虫 + 内部 RAG 双源检索。
    流程：① 爬取公司外部数据（招聘+官网） → ② 内部+外部 RAG 检索 → ③ 逐节生成

    Args:
        parsed: parse_user_input() 的返回值

    Returns:
        dict: 包含各节内容和元数据的报告字典
    """
    company = parsed.get("company", "未知客户")
    logger.info(f"开始生成报告: {company}")

    # ── Phase 3：外部爬虫 fire-and-forget（后台触发，不等待）──────────
    # 爬虫数据是「加分项」，不应阻塞报告生成主流程。
    # 爬虫会在后台完成并将结果写入 ChromaDB，后续请求自动受益。
    try:
        from src.crawler.crawler_dispatcher import crawl_and_store
        logger.info(f"Phase 3: 后台触发外部爬虫 → {company}")
        # fire-and-forget：创建后台任务，不 await
        asyncio.create_task(
            _fire_crawl(company)
        )
    except Exception as e:
        logger.warning(f"外部爬虫后台触发异常（不影响报告生成）: {e}")

    # 准备模板变量
    template_vars = {
        "company": company,
        "visit_target": parsed.get("visit_target", "未指定"),
        "known_info": parsed.get("known_info", "无"),
        "visit_purpose": parsed.get("visit_purpose", "未指定"),
        "focus_areas": "、".join(parsed.get("focus_areas", [])) or "未指定",
        "special_req": parsed.get("special_req", "无"),
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

    # ── 顺序生成有依赖的章节（1→2→4）─────────────────────────
    sequential_sections = [1, 2, 4]
    for section_num in sequential_sections:
        label = SECTION_LABELS[section_num - 1]
        logger.info(f"生成第 {section_num} 节: {label}")

        try:
            content = await _generate_section(
                section_num=section_num,
                template_vars=template_vars,
                max_retries=2,
                max_tokens=4000,
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
                template_vars["strategy"] = _truncate(content, 200)

        except Exception as e:
            logger.error(f"第 {section_num} 节生成失败: {e}")
            key = SECTION_NAMES[section_num - 1]
            report_data[key] = f"[生成失败：{str(e)[:100]}]"

    # ── Section 3 交叉销售（固定占位，不调用 LLM）──────────────
    content = "🚧 交叉销售机会分析功能暂未上线，敬请期待。"
    report_data["cross_sell"] = content
    report_data["sections"]["3"] = content
    template_vars["cross_sell"] = content

    # ── 并行生成无依赖的章节（5 和 6 都只依赖 S4 的 strategy）───
    logger.info("并行生成第 5、6 节...")
    s5_task = asyncio.create_task(
        _generate_section(5, template_vars, max_retries=2, max_tokens=4000)
    )
    s6_task = asyncio.create_task(
        _generate_section(6, template_vars, max_retries=2, max_tokens=4000)
    )

    try:
        s5_content = await s5_task
        report_data["talk_script"] = s5_content
        report_data["sections"]["5"] = s5_content
    except Exception as e:
        logger.error(f"第 5 节生成失败: {e}")
        report_data["talk_script"] = f"[生成失败：{str(e)[:100]}]"

    try:
        s6_content = await s6_task
        report_data["action_plan"] = s6_content
        report_data["sections"]["6"] = s6_content
    except Exception as e:
        logger.error(f"第 6 节生成失败: {e}")
        report_data["action_plan"] = f"[生成失败：{str(e)[:100]}]"

    # ── 收集 RAG 来源（去重）──────────────────────────────────
    report_data["sources"] = _collect_sources(company)

    t_report_elapsed = time.monotonic() - t_report_start
    logger.info(
        f"报告生成完成: {company}，总耗时 {t_report_elapsed:.1f}s，"
        f"共 {len([k for k in report_data if k not in ('company','generated_at','sections','sources')])} 节"
    )
    return report_data


def _collect_sources(company: str) -> list[dict[str, str]]:
    """收集该公司在 RAG 中检索到的所有 Bitable 来源（去重）"""
    from src.rag.retriever import retrieve_for_report_with_meta

    seen_urls: set[str] = set()
    sources: list[dict[str, str]] = []

    # 对每一节都检索一次，收集所有来源
    for section_num in (1, 2, 4, 5, 6):
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

    return sources


async def _fire_crawl(company: str) -> None:
    """后台执行外部爬虫（fire-and-forget，异常静默处理）"""
    try:
        from src.crawler.crawler_dispatcher import crawl_and_store
        result = await asyncio.wait_for(
            crawl_and_store(company=company, timeout=35.0),
            timeout=38.0,
        )
        if result.get("chunks_stored", 0) > 0:
            logger.info(
                f"后台爬虫完成: {company} — "
                f"{result['jobs_count']} 职位, "
                f"{result['chunks_stored']} chunks"
            )
        else:
            logger.info(f"后台爬虫完成: {company} — 无新增数据")
    except asyncio.TimeoutError:
        logger.warning(f"后台爬虫超时: {company}")
    except Exception as e:
        logger.warning(f"后台爬虫异常: {company} — {e}")


async def _generate_section(
    section_num: int,
    template_vars: dict[str, Any],
    max_retries: int = 2,
    max_tokens: int = 4000,
) -> str:
    """生成单个章节，带重试逻辑（Phase 2：接入 RAG）"""
    t_start = time.monotonic()
    # RAG 检索：根据公司名 + 章节主题检索飞书 Wiki 相关知识
    from src.rag.retriever import retrieve_for_report, format_rag_context

    company = template_vars.get("company", "")
    contexts = retrieve_for_report(company, section_num)
    rag_context = format_rag_context(contexts)

    if rag_context:
        logger.info(f"第 {section_num} 节已注入 RAG 上下文（{len(contexts)} 条）")
    else:
        logger.debug(f"第 {section_num} 节无 RAG 结果")

    messages = build_section_messages(
        section_num=section_num,
        rag_context=rag_context,
        **template_vars,
    )

    for attempt in range(1, max_retries + 2):  # 首次 + max_retries 次重试
        try:
            content = await chat_completion(
                messages,
                temperature=0.3,
                max_tokens=max_tokens,
            )
            if content and len(content.strip()) > 20:
                elapsed = time.monotonic() - t_start
                logger.info(f"第 {section_num} 节 LLM 生成完成（{len(content)} 字，{elapsed:.1f}s）")
                return content.strip()
            else:
                logger.warning(f"第 {section_num} 节返回内容过短，重试 {attempt}")
        except Exception as e:
            logger.warning(f"第 {section_num} 节第 {attempt} 次尝试失败: {e}")
            if attempt <= max_retries:
                await asyncio.sleep(2 * attempt)  # 指数退避
            else:
                raise

    # 所有重试均失败
    raise RuntimeError(f"第 {section_num} 节生成失败（已重试 {max_retries} 次）")


def _truncate(text: str, max_len: int = 300) -> str:
    """截断文本，用于注入后续 Prompt 的上下文"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "...（截断）"
