"""报告生成主逻辑 — Phase 2（RAG 接入飞书 Wiki）"""

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

    Phase 1：无 RAG，直接按模板调用 LLM 逐节生成。

    Args:
        parsed: parse_user_input() 的返回值

    Returns:
        dict: 包含各节内容和元数据的报告字典
    """
    company = parsed.get("company", "未知客户")
    logger.info(f"开始生成报告: {company}")

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
        "sections": {},  # section_num -> content
    }

    # ── 逐节生成（section 1 不需要前置依赖）───────────────────
    for i, section_num in enumerate([1, 2, 3, 4, 5, 6], 1):
        label = SECTION_LABELS[section_num - 1]
        logger.info(f"生成第 {section_num} 节: {label}")

        try:
            content = await _generate_section(
                section_num=section_num,
                template_vars=template_vars,
                max_retries=2,
            )
            key = SECTION_NAMES[section_num - 1]
            report_data[key] = content
            report_data["sections"][str(section_num)] = content

            # 将本节内容注入模板变量，供后续章节参考
            if section_num == 1:
                template_vars["snapshot"] = _truncate(content, 300)
            elif section_num == 2:
                template_vars["opportunity_scan"] = _truncate(content, 300)
            elif section_num == 3:
                template_vars["cross_sell"] = _truncate(content, 300)
            elif section_num == 4:
                template_vars["strategy"] = _truncate(content, 300)

            logger.info(f"第 {section_num} 节生成完成（{len(content)} 字）")

        except Exception as e:
            logger.error(f"第 {section_num} 节生成失败: {e}")
            key = SECTION_NAMES[section_num - 1]
            report_data[key] = f"[生成失败：{str(e)[:100]}]"

    logger.info(f"报告生成完成: {company}，共 {len(report_data)} 节")
    return report_data


async def _generate_section(
    section_num: int,
    template_vars: dict[str, Any],
    max_retries: int = 2,
) -> str:
    """生成单个章节，带重试逻辑（Phase 2：接入 RAG）"""
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
            content = await chat_completion(messages, temperature=0.3)
            if content and len(content.strip()) > 20:
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
