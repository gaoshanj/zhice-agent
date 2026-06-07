"""飞书消息卡片构建器 — Phase 1 实现

根据项目规格文档，报告卡片包含 6 个章节：
  1. 客户 360° 快照
  2. 培训商机深度扫描
  3. 交叉销售机会挖掘
  4. 销售策略建议
  5. 推荐销售话术
  6. 行动建议
"""

from __future__ import annotations

from typing import Any


# ── 卡片颜色模板 ──────────────────────────────────────────────────
TEMPLATE_COLORS: dict[str, str] = {
    "blue": "blue",
    "wathet": "wathet",
    "turquoise": "turquoise",
    "green": "green",
    "yellow": "yellow",
    "orange": "orange",
    "red": "red",
    "carmine": "carmine",
    "violet": "violet",
    "purple": "purple",
    "indigo": "indigo",
    "grey": "grey",
}

def _md(text: str) -> dict[str, Any]:
    """包装飞书 markdown 文本对象"""
    return {"tag": "lark_md", "content": text[:2000]}


def _div(text: str) -> dict[str, Any]:
    """普通文本块"""
    return {"tag": "div", "text": _md(text)}


def _hr() -> dict[str, str]:
    return {"tag": "hr"}


def _header(title: str, template: str = "blue") -> dict[str, Any]:
    return {
        "title": {"tag": "plain_text", "content": title[:50]},
        "template": template,
    }


# ── 主入口 ────────────────────────────────────────────────────────
def build_report_card(report_data: dict[str, Any]) -> dict[str, Any]:
    """将报告数据渲染为飞书交互卡片 JSON。

    report_data 结构（由 report/generator.py 生成）：
    {
        "company": str,
        "snapshot": str,           # 客户快照
        "opportunity_scan": str,    # 商机扫描
        "cross_sell": str,          # 交叉销售
        "strategy": str,            # 销售策略
        "talk_script": str,         # 话术
        "action_plan": str,         # 行动建议
        "generated_at": str,
    }
    """
    company = report_data.get("company", "未知客户")
    elements: list[dict[str, Any]] = []

    # ── 1. 客户快照 ──
    snapshot = report_data.get("snapshot", "")
    if snapshot:
        elements.append(_div(f"**📊 客户 360° 快照 — {company}**\n\n{snapshot}"))
        elements.append(_hr())

    # ── 2. 培训商机深度扫描 ──
    opp = report_data.get("opportunity_scan", "")
    if opp:
        elements.append(_div(f"**🎯 培训商机深度扫描**\n\n{opp}"))
        elements.append(_hr())

    # ── 3. 交叉销售机会挖掘 ──
    cross = report_data.get("cross_sell", "")
    if cross:
        elements.append(_div(f"**🔗 交叉销售机会**\n\n{cross}"))
        elements.append(_hr())

    # ── 4. 销售策略建议 ──
    strategy = report_data.get("strategy", "")
    if strategy:
        elements.append(_div(f"**🧠 销售策略建议**\n\n{strategy}"))
        elements.append(_hr())

    # ── 5. 推荐销售话术 ──
    script = report_data.get("talk_script", "")
    if script:
        elements.append(_div(f"**💬 推荐销售话术**\n\n{script}"))
        elements.append(_hr())

    # ── 6. 行动建议 ──
    actions = report_data.get("action_plan", "")
    if actions:
        elements.append(_div(f"**🚀 行动建议**\n\n{actions}"))
        elements.append(_hr())

    # 尾部信息
    generated_at = report_data.get("generated_at", "")
    elements.append(
        _div(
            f"---\n🤖 由 *培训智策 Agent* 生成"
            + (f" · {generated_at}" if generated_at else "")
        )
    )

    card = {
        "config": {"wide_screen_mode": True},
        "header": _header(f"📋 {company} · 销售策略报告"),
        "elements": elements,
    }
    return card


def build_error_card(error_msg: str, company: str = "") -> dict[str, Any]:
    """生成错误提示卡片"""
    title = f"❌ {company} 报告生成失败" if company else "❌ 报告生成失败"
    return {
        "config": {"wide_screen_mode": True},
        "header": _header(title, template="red"),
        "elements": [
            _div(f"**错误信息：**\n{error_msg[:500]}"),
            _div("请检查输入格式后重试，或联系技术支持。"),
        ],
    }


def build_help_card() -> dict[str, Any]:
    """发送使用说明卡片"""
    return {
        "config": {"wide_screen_mode": True},
        "header": _header("📋 培训智策 Agent · 使用说明", template="blue"),
        "elements": [
            _div(
                "**请按以下格式输入（复制后修改括号内内容）：**\n"
                "```\n"
                "@培训智策Agent 请帮我生成针对以下客户的销售策略报告：\n"
                "- 客户公司：[公司全称]\n"
                "- 拜访对象部门/职位：[如：技术研发部/CTO]\n"
                "- 当前已知信息（选填）：[...]\n"
                "- 本次拜访主要目的：[如：首次接触/挖掘培训需求]\n"
                "- 期望侧重方向（多选）：微软Agent培训 / AWS培训 / MSP / 安服 / MA\n"
                "- 特别要求（选填）：[...]\n"
                "```"
            ),
            _hr(),
            _div("⚠️ 至少提供「客户公司」名称，否则无法生成报告。"),
        ],
    }
