"""飞书 Bitable 写入模块 — Phase 3.5

将爬虫结果写入飞书多维表格「网络信息抓取表」。
以「网址URL」为唯一键去重：同 URL 不重复写入。

用法：
    from src.crawler.bitable_writer import write_crawl_result

    result = await write_crawl_result(
        company="九号公司",
        url="https://www.ninebot.com",
        summary="九号公司是全球领先的...",
        source_type="官网",
    )
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from src.utils.config import settings
from src.utils.logger import logger

BASE_URL = "https://open.feishu.cn/open-apis"

# 爬虫数据表配置（网络信息抓取表）
CRAWL_TABLE_ID = settings.feishu_bitable_crawl_table_id

# 字段名称（飞书 Bitable API 用字段名而非 field_id）
FIELD_COMPANY = "公司名"
FIELD_URL = "网址URL"
FIELD_SUMMARY = "摘要"
FIELD_CRAWL_TIME = "抓取时间"
FIELD_SOURCE = "来源类型"

# token 缓存
_app_access_token: str = ""
_app_token_expire_at: float = 0.0


def _get_token() -> str:
    """获取 app_access_token（带缓存）"""
    global _app_access_token, _app_token_expire_at
    now = time.time()
    if _app_access_token and now < _app_token_expire_at - 60:
        return _app_access_token

    url = f"{BASE_URL}/auth/v3/app_access_token/internal"
    r = httpx.post(url, json={
        "app_id": settings.feishu_app_id,
        "app_secret": settings.feishu_app_secret,
    }, timeout=10)
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 app_access_token 失败: {data.get('msg', data)}")

    _app_access_token = data["app_access_token"]
    _app_token_expire_at = now + data.get("expire", 7200)
    return _app_access_token


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }


async def url_exists(url: str) -> bool:
    """检查 URL 是否已存在于爬虫数据表中"""
    try:
        base_token = settings.feishu_bitable_base_token
        # 用 filter 精确匹配 网址URL 字段
        filter_expr = f'CurrentValue.[网址URL] = "{url}"'
        api_url = f"{BASE_URL}/bitable/v1/apps/{base_token}/tables/{CRAWL_TABLE_ID}/records"
        r = httpx.get(api_url, params={
            "filter": filter_expr,
            "page_size": 1,
        }, headers=_headers(), timeout=10)
        data = r.json()
        if data.get("code") != 0:
            logger.warning(f"[Bitable写入] 查询URL去重失败: {data.get('msg')}")
            return False  # 查询失败时不阻塞写入
        items = data.get("data", {}).get("items", [])
        return len(items) > 0
    except Exception as e:
        logger.warning(f"[Bitable写入] URL去重检查异常: {e}")
        return False


async def write_crawl_result(
    company: str,
    url: str,
    summary: str,
    source_type: str = "官网",
) -> dict:
    """写入一条爬虫结果到飞书多维表格（按 URL 去重）

    Args:
        company: 公司名
        url: 网页 URL（唯一键）
        summary: 内容摘要（会被截断到约 50000 字以内）
        source_type: 来源类型，必须为「官网」或「招聘」

    Returns:
        {"written": bool, "record_id": str|None, "error": str|None}
    """
    if not CRAWL_TABLE_ID:
        return {"written": False, "record_id": None, "error": "未配置 CRAWL_TABLE_ID"}

    if source_type not in ("官网", "招聘"):
        logger.warning(f"[Bitable写入] 无效的来源类型: {source_type}")
        source_type = "官网"

    # 去重
    try:
        if await url_exists(url):
            logger.info(f"[Bitable写入] URL 已存在，跳过: {url[:60]}")
            return {"written": False, "record_id": None, "error": None}
    except Exception as e:
        logger.warning(f"[Bitable写入] 去重查询异常（继续写入）: {e}")

    # 截断摘要（飞书文本字段有长度限制）
    if len(summary) > 50000:
        summary = summary[:50000] + "..."

    # 构建记录
    fields = {
        FIELD_COMPANY: company,
        FIELD_URL: {"link": url, "text": url[:200]},
        FIELD_SUMMARY: summary,
        FIELD_CRAWL_TIME: int(time.time() * 1000),  # 飞书日期字段用毫秒时间戳
        FIELD_SOURCE: [source_type],  # 多选字段需要数组格式
    }

    try:
        base_token = settings.feishu_bitable_base_token
        api_url = f"{BASE_URL}/bitable/v1/apps/{base_token}/tables/{CRAWL_TABLE_ID}/records"
        r = httpx.post(api_url, json={"fields": fields}, headers=_headers(), timeout=15)
        data = r.json()

        if data.get("code") != 0:
            msg = data.get("msg", str(data))
            logger.error(f"[Bitable写入] 写入失败: {msg}")
            return {"written": False, "record_id": None, "error": msg}

        record_id = data.get("data", {}).get("record", {}).get("record_id", "")
        logger.info(f"[Bitable写入] 已写入: {company} | {url[:60]} | record_id={record_id}")
        return {"written": True, "record_id": record_id, "error": None}

    except Exception as e:
        msg = str(e)
        logger.error(f"[Bitable写入] 写入异常: {msg}")
        return {"written": False, "record_id": None, "error": msg}


async def batch_write_crawl_results(
    results: list[dict],
) -> list[dict]:
    """批量写入爬虫结果

    Args:
        results: 列表，每项为 {
            "company": str,
            "url": str,
            "summary": str,
            "source_type": "官网"|"招聘",
        }

    Returns:
        每个 result 的写入结果列表
    """
    outcomes = []
    for r in results:
        outcome = await write_crawl_result(
            company=r.get("company", ""),
            url=r.get("url", ""),
            summary=r.get("summary", ""),
            source_type=r.get("source_type", "官网"),
        )
        outcomes.append(outcome)
    return outcomes
