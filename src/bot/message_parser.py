"""用户输入解析 — 从飞书消息文本中提取结构化字段"""

import re
from typing import Optional


def parse_user_input(text: str) -> dict:
    """解析用户输入，提取报告生成所需字段。

    支持两种输入格式：
    1. 结构化格式（按项目规格文档的模板）：
       - 客户公司：[xxx]
       - 拜访对象部门/职位：[xxx]
       ...

    2. 自由文本格式：
       直接输入公司名称，其余字段使用默认值

    Returns:
        dict with keys:
        - company (str): 客户公司名称（必填）
        - visit_target (str): 拜访对象，如「技术研发部/CTO」
        - known_info (str): 当前已知信息
        - visit_purpose (str): 拜访目的
        - focus_areas (list[str]): 期望侧重方向
        - special_req (str): 特别要求
    """
    result = {
        "company": "",
        "visit_target": "",
        "known_info": "",
        "visit_purpose": "",
        "focus_areas": [],
        "special_req": "",
        "raw_text": text[:500],
    }

    # 1. 尝试按结构化模板解析
    _parse_structured(text, result)

    # 2. 如果未解析到公司名，尝试从自由文本中提取
    if not result["company"]:
        _parse_free_text(text, result)

    return result


def _parse_structured(text: str, result: dict):
    """按结构化模板解析"""
    # 客户公司
    m = re.search(r"客户公司[：:]\s*([^\n]+)", text)
    if m:
        result["company"] = m.group(1).strip()

    # 拜访对象部门/职位
    m = re.search(r"拜访对象部门/职位[：:][ \t]*([^\n]+)", text)
    if m:
        result["visit_target"] = m.group(1).strip()

    # 当前已知信息
    m = re.search(r"当前已知信息（选填）[：:][ \t]*([^\n]+)", text)
    if m:
        result["known_info"] = m.group(1).strip()

    # 本次拜访主要目的
    m = re.search(r"本次拜访主要目的[：:][ \t]*([^\n]+)", text)
    if m:
        result["visit_purpose"] = m.group(1).strip()

    # 期望侧重方向（多选）
    focus_pattern = r"期望侧重方向（多选）[：:][ \t]*([^\n]+)"
    m = re.search(focus_pattern, text)
    if m:
        raw = m.group(1).strip()
        # 支持用 / 、或空格分隔
        areas = re.split(r"[／/、]+", raw)
        result["focus_areas"] = [a.strip() for a in areas if a.strip()]

    # 特别要求
    m = re.search(r"特别要求（选填）[：:][ \t]*([^\n]+)", text)
    if m:
        result["special_req"] = m.group(1).strip()


def _parse_free_text(text: str, result: dict):
    """从自由文本中提取公司名（简单启发式）"""
    # 去除 @ 提及内容
    cleaned = re.sub(r"@\S+\s*", "", text).strip()

    # 尝试提取「针对/面向/关于 + 公司名」模式
    patterns = [
        r"针对\s*[：:]\s*([^\s，,。]+)",
        r"面向\s*[：:]\s*([^\s，,。]+)",
        r"关于\s*[：:]\s*([^\s，,。]+)",
        r"生成\s*([^\s，,。]+)\s*的?",
        r"([\u4e00-\u9fff]{2,20}(?:公司|集团|科技|技术|有限|股份))",
    ]
    for pat in patterns:
        m = re.search(pat, cleaned)
        if m:
            result["company"] = m.group(1).strip()
            return

    # 兜底：取去除 @ 后的第一个非空句子（限 20 字）
    if cleaned:
        first_line = cleaned.split("\n")[0][:20].strip()
        if first_line:
            result["company"] = first_line
