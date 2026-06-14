#!/usr/bin/env python3
"""Bitable 知识库索引构建脚本

从飞书多维表格拉取数据 → 转文本 → 分块 → embedding → ChromaDB

使用方式：
    python scripts/build_bitable_index.py
    python scripts/build_bitable_index.py --rebuild

环境变量：
    单表模式（向后兼容）：
        FEISHU_BITABLE_BASE_TOKEN + FEISHU_BITABLE_TABLE_ID
    多表模式：
        FEISHU_BITABLE_TABLES='[{"base_token":"...","table_id":"...","enabled":true}]'
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(".env", override=False)

# ChromaDB SQLite 兼容性（Azure 环境需要，本地跳过）
try:
    __import__("pysqlite3")
    import sys as _sys
    _sys.modules["sqlite3"] = _sys.modules.pop("pysqlite3")
except ImportError:
    pass

import httpx

from src.rag.document_loader import split_documents
from src.rag.vector_store import add_chunks, clear_collection, collection_count
from src.utils.config import settings
from src.utils.logger import logger

BASE_URL = "https://open.feishu.cn/open-apis"


# ── 工具函数 ────────────────────────────────────────────

def _get_tables() -> list[dict]:
    """获取所有需要索引的表格配置

    优先读取 FEISHU_BITABLE_TABLES（JSON 数组），
    为空时回退到旧的单表配置。
    """
    raw = settings.feishu_bitable_tables.strip()
    if raw:
        try:
            tables = json.loads(raw)
            if not isinstance(tables, list):
                raise ValueError("FEISHU_BITABLE_TABLES 必须是 JSON 数组")
            # 过滤：只保留 enabled 非 false 的
            enabled = [t for t in tables if t.get("enabled", True) is not False]
            if not enabled:
                raise ValueError("FEISHU_BITABLE_TABLES 中没有启用的表格")
            logger.info(f"多表模式：{len(enabled)} 个表格")
            for t in enabled:
                logger.info(f"  - {t['table_id']} (base={t['base_token'][:12]}...)")
            return enabled
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"FEISHU_BITABLE_TABLES 解析失败: {e}")
            logger.warning("回退到单表模式")

    # 单表回退（自动包含爬虫结果表，确保多表数据都参与 RAG）
    if settings.feishu_bitable_base_token:
        tables = [{
            "base_token": settings.feishu_bitable_base_token,
            "table_id": settings.feishu_bitable_table_id,
        }]
        # 如果配置了爬虫结果表且与主表不同，自动追加
        crawl_id = settings.feishu_bitable_crawl_table_id
        if crawl_id and crawl_id != settings.feishu_bitable_table_id:
            tables.append({
                "base_token": settings.feishu_bitable_base_token,
                "table_id": crawl_id,
            })
        logger.info(f"单表模式（自动扩展为 {len(tables)} 个表）")
        for t in tables:
            logger.info(f"  - {t['table_id']}")
        return tables

    raise RuntimeError("未配置任何 Bitable：请设置 FEISHU_BITABLE_TABLES 或 "
                       "FEISHU_BITABLE_BASE_TOKEN + FEISHU_BITABLE_TABLE_ID")


def get_app_token() -> str:
    r = httpx.post(f"{BASE_URL}/auth/v3/app_access_token/internal", json={
        "app_id": settings.feishu_app_id,
        "app_secret": settings.feishu_app_secret,
    }, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 app_token 失败: {data}")
    return data["app_access_token"]


def fetch_option_map(token: str, base_token: str, table_id: str) -> dict[str, dict[str, str]]:
    """获取所有选择/多选字段的选项映射 {field_name: {option_id: label}}"""
    r = httpx.get(
        f"{BASE_URL}/bitable/v1/apps/{base_token}/tables/{table_id}/fields",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取字段失败 (table={table_id}): {data}")

    option_map: dict[str, dict[str, str]] = {}
    for field in data["data"]["items"]:
        if field["type"] in (3, 4):  # 3=单选, 4=多选
            name = field["field_name"]
            props = field.get("property", {})
            options = props.get("options", [])
            option_map[name] = {opt["id"]: opt["name"] for opt in options}
    return option_map


def resolve_value(value: object, field_name: str, option_map: dict) -> str:
    """将飞书字段值转为可读文本"""
    if value is None:
        return ""

    # 文本数组
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(item.get("text", str(item)))
            else:
                parts.append(str(item))
        # 如果是选项 ID 数组，尝试解析
        resolved = []
        for p in parts:
            if field_name in option_map and p in option_map[field_name]:
                resolved.append(option_map[field_name][p])
            else:
                resolved.append(p)
        return "、".join(resolved) if resolved else ""

    # 时间戳
    if isinstance(value, (int, float)) and value > 1e12:
        from datetime import datetime
        try:
            dt = datetime.fromtimestamp(value / 1000)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return str(value)

    return str(value)


def fetch_records(token: str, base_token: str, table_id: str) -> list[dict]:
    """拉取全部记录"""
    all_records = []
    page_token = None
    page = 0

    while True:
        page += 1
        params = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        r = httpx.get(
            f"{BASE_URL}/bitable/v1/apps/{base_token}/tables/{table_id}/records",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取记录失败 (table={table_id}): {data}")
        items = data["data"].get("items", [])
        all_records.extend(items)
        logger.info(f"    第 {page} 页: {len(items)} 条 (累计 {len(all_records)})")
        if not data["data"].get("has_more"):
            break
        page_token = data["data"].get("page_token")
    return all_records


# ── 多表字段映射 ──────────────────────────────────────────
# 不同 Bitable 表的字段名不同，这里定义已知表的字段提取逻辑。
# key=table_id, value={"company_field": str, "fields": [(label, field_name), ...]}
_TABLE_SCHEMAS: dict[str, dict] = {
    # 客户商机表
    "tblHp4aCxwHDJXKJ": {
        "company_field": "公司全称",
        "fields": [
            ("公司", "公司全称"), ("行业", "行业"), ("客户层级", "客户层级"),
            ("客户需求", "客户需求"), ("培训部门", "培训部门"),
            ("需求紧急程度", "需求紧急程度"), ("预计成交周期", "预计成交周期"),
            ("下一步动作", "下一步关键动作"), ("交付风险点", "交付风险点"),
            ("触发事件", "需求触发事件"), ("触发时间", "需求触发时间"),
            ("商机来源", "商机来源"), ("最近培训", "最近一次培训内容"),
            ("满意度", "最近一次整体满意度(100分满分)"),
            ("负责人", "TPM/..."), ("编号", "编号"),
        ],
        "contact_fields": [
            ("主要联系人姓名", "主要联系人角色", "影响力等级", "当前关系状态"),
        ],
        "extra_fields": ["厂商对接人", "客户ID", "最近一次覆盖人数"],
    },
    # 爬虫结果存储表
    "tblnZiEhmSl6htGB": {
        "company_field": "公司名",
        "fields": [
            ("公司", "公司名"), ("网址", "网址URL"), ("摘要", "摘要"),
            ("抓取时间", "抓取时间"), ("来源类型", "来源类型"),
        ],
        "contact_fields": [],
        "extra_fields": [],
    },
}


def _get_company_name(fields: dict, table_id: str) -> str:
    """从记录中提取公司名（兼容多表字段名）"""
    schema = _TABLE_SCHEMAS.get(table_id, {})
    company_field = schema.get("company_field", "公司全称")

    val = fields.get(company_field)
    if isinstance(val, list) and val:
        return val[0].get("text", "") if isinstance(val[0], dict) else str(val[0])

    # 该表无专用 company_field，尝试常见字段名
    for key in ("公司全称", "公司名", "公司", "客户名称"):
        val = fields.get(key)
        if isinstance(val, list) and val:
            return val[0].get("text", "") if isinstance(val[0], dict) else str(val[0])
        if isinstance(val, str) and val:
            return val
    return ""


def record_to_document(rec: dict, option_map: dict,
                       base_token: str = "", table_id: str = "") -> dict | None:
    """将一条 Bitable 记录转为知识库文档（支持多表 schema）"""
    fields = rec.get("fields", {})

    company = _get_company_name(fields, table_id)
    if not company:
        return None

    parts = []
    schema = _TABLE_SCHEMAS.get(table_id)

    if schema:
        # ── 已知表：按预定义字段提取 ──
        for label, field_name in schema["fields"]:
            val = resolve_value(fields.get(field_name), field_name, option_map)
            if val:
                parts.append(f"【{label}】{val}")

        # 联系人字段
        for contact_group in schema.get("contact_fields", []):
            contact_parts = []
            for fn in contact_group:
                cf_val = fields.get(fn, "")
                if isinstance(cf_val, str) and cf_val:
                    contact_parts.append(cf_val)
                elif isinstance(cf_val, list) and cf_val:
                    txt = cf_val[0].get("text", "") if isinstance(cf_val[0], dict) else str(cf_val[0])
                    if txt:
                        contact_parts.append(txt)
            if contact_parts:
                parts.append(f"【联系人】{' / '.join(contact_parts)}")

        # 额外字段
        for fn in schema.get("extra_fields", []):
            val = resolve_value(fields.get(fn), fn, option_map)
            if val:
                parts.append(f"【{fn}】{val}")

        # 编号
        record_id = fields.get("编号", "")
        if record_id:
            parts.append(f"【编号】{record_id}")

        # 客户ID
        customer_id = ""
        cid_val = fields.get("客户ID", [])
        if isinstance(cid_val, list) and cid_val:
            customer_id = cid_val[0].get("text", "") if isinstance(cid_val[0], dict) else str(cid_val[0])
    else:
        # ── 未知表：通用提取所有字段 ──
        parts.append(f"【公司】{company}")
        for key, value in fields.items():
            if key in ("公司名", "公司全称", "公司"):
                continue
            val = resolve_value(value, key, option_map)
            if val:
                parts.append(f"【{key}】{val}")
        customer_id = ""

    text = "\n".join(parts)

    return {
        "node_token": rec.get("record_id", str(uuid.uuid4())),
        "title": company or "未知公司",
        "content": text,
        "company": company,
        "customer_id": customer_id if schema else "",
        "record_id": fields.get("编号", ""),
        "source": "bitable",
        "url": f"https://bba12hub36.feishu.cn/base/{base_token}?table={table_id}",
    }


# ── 主流程 ──────────────────────────────────────────────

def build_index(rebuild: bool = False) -> None:
    """构建 Bitable 知识库索引（支持多表）"""
    tables = _get_tables()

    if rebuild:
        logger.warning("Rebuild 模式：清空现有集合并重建")
        try:
            clear_collection(settings.chroma_collection_internal)
        except Exception as e:
            logger.warning(f"清空集合失败（如首次构建则正常）: {e}")

    token = get_app_token()

    total_records = 0
    total_docs = 0
    total_chunks = 0

    for i, table_cfg in enumerate(tables):
        base_token = table_cfg["base_token"]
        table_id = table_cfg["table_id"]
        label = table_cfg.get("label", table_id[:12])

        logger.info(f"\n{'='*50}")
        logger.info(f"[{i+1}/{len(tables)}] 处理表格: {label}")
        logger.info(f"  base_token: {base_token[:12]}..., table_id: {table_id}")
        logger.info(f"{'='*50}")

        # 获取字段映射
        logger.info("  获取字段选项映射...")
        option_map = fetch_option_map(token, base_token, table_id)

        # 拉取数据
        logger.info("  拉取表格数据...")
        records = fetch_records(token, base_token, table_id)
        logger.info(f"  拉取完成：{len(records)} 条记录")
        total_records += len(records)

        # 转文档
        documents = []
        skipped = 0
        for rec in records:
            doc = record_to_document(rec, option_map, base_token, table_id)
            if doc:
                documents.append(doc)
            else:
                skipped += 1

        logger.info(f"  有效文档：{len(documents)} 条（跳过 {skipped} 条无公司名记录）")
        total_docs += len(documents)

        if not documents:
            logger.warning(f"  表格 {label} 无有效文档，跳过")
            continue

        # 分块
        chunks = []
        for doc in documents:
            chunks.append({
                "chunk_id": f"bitable::{doc['node_token']}",
                "content": doc["content"],
                "metadata": {
                    "title": doc["title"],
                    "source": "bitable",
                    "base_token": base_token,
                    "table_id": table_id,
                    "customer_id": doc.get("customer_id", ""),
                    "record_id": doc.get("record_id", ""),
                },
            })

        # 写入向量库
        logger.info(f"  生成 embedding 并写入向量库（{len(chunks)} chunks）...")
        start = time.time()
        count = add_chunks(chunks, collection_name=settings.chroma_collection_internal)
        elapsed = time.time() - start
        logger.info(f"  写入完成：{count} 条（耗时 {elapsed:.1f}s）")
        total_chunks += count

    # 最终验证
    final = collection_count()
    logger.info(f"\n{'='*50}")
    logger.info(f"🎉 所有表格索引构建完成！")
    logger.info(f"  总记录: {total_records} | 总文档: {total_docs} | 总 chunks: {total_chunks}")
    logger.info(f"  集合 '{settings.chroma_collection_internal}' 当前共 {final} 条记录")
    logger.info(f"{'='*50}")


import uuid


def main():
    parser = argparse.ArgumentParser(description="构建 Bitable 知识库 RAG 索引")
    parser.add_argument("--rebuild", action="store_true", help="清空现有索引后重建")
    args = parser.parse_args()

    build_index(rebuild=args.rebuild)
    logger.info("🎉 Bitable 索引构建完成！")


if __name__ == "__main__":
    main()
