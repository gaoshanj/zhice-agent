#!/usr/bin/env python3
"""从飞书 Bitable 表重新构建 external_docs 索引"""

import sys
import os
import json
import httpx
import hashlib
from typing import Any

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.rag.vector_store import add_chunks, collection_count, clear_collection


# 飞书 API 配置（从环境变量读取，不硬编码）
BASE_URL = "https://open.feishu.cn/open-apis"
BASE_TOKEN = os.environ.get("FEISHU_BITABLE_BASE_TOKEN", "")
TABLE_ID = os.environ.get("FEISHU_CRAWL_TABLE_ID", "tblnZiEhmSl6htGB")  # 网络信息抓取表


def get_tenant_access_token() -> str:
    """获取租户访问令牌"""
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        raise SystemExit("错误: 请设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET 环境变量")
    resp = httpx.post(
        f"{BASE_URL}/auth/v3/tenant_access_token/internal",
        json={
            "app_id": app_id,
            "app_secret": app_secret,
        },
        timeout=10
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"获取 token 失败: {data}")
    return data["tenant_access_token"]


def fetch_all_records(token: str) -> list[dict[str, Any]]:
    """获取表中的所有记录"""
    headers = {"Authorization": f"Bearer {token}"}
    all_records = []
    page_token = None

    while True:
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token

        resp = httpx.get(
            f"{BASE_URL}/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/records",
            params=params,
            headers=headers,
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise Exception(f"获取记录失败: {data}")

        items = data.get("data", {}).get("items", [])
        for item in items:
            # 提取字段
            fields = item.get("fields", {})
            all_records.append({
                "record_id": item.get("record_id"),
                "公司名": fields.get("公司名", ""),
                "来源类型": fields.get("来源类型", ""),
                "文本": fields.get("文本", ""),
                "摘要": fields.get("摘要", ""),
                "URL": fields.get("URL", ""),
                "时间": fields.get("时间", "")
            })

        if not data.get("data", {}).get("has_more"):
            break
        page_token = data.get("data", {}).get("page_token")

    return all_records


def convert_to_chunks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将飞书记录转换为 ChromaDB chunks"""
    chunks = []

    for record in records:
        company = record.get("公司名", "")
        source_type = record.get("来源类型", "")
        text = record.get("文本", "") or record.get("摘要", "")
        url = record.get("URL", "")

        if not text or not company:
            continue

        # 用飞书 record_id 作为 chunk_id（天然唯一），fallback 到 MD5
        raw_id = record.get("record_id", "")
        chunk_id = raw_id or hashlib.md5(
            f"external_{company}_{source_type}_{text[:100]}".encode()
        ).hexdigest()[:16]

        metadata = {
            "source": f"external_{source_type}",
            "company": company,
            "data_type": source_type,
        }
        if url:
            metadata["url"] = url

        chunks.append({
            "chunk_id": chunk_id,
            "content": text,
            "metadata": metadata
        })

    return chunks


def main():
    print("=== 开始重建 external_docs 索引 ===\n")

    # 1. 清空 existing collection
    print("1. 清空 external_docs 集合...")
    try:
        clear_collection("external_docs")
        print("   ✅ 清空完成")
    except Exception as e:
        print(f"   ⚠️ 清空失败（可能不存在）: {e}")

    # 2. 获取飞书数据
    print("\n2. 从飞书表读取数据...")
    token = get_tenant_access_token()
    records = fetch_all_records(token)
    print(f"   ✅ 读取到 {len(records)} 条记录")

    # 3. 转换为 chunks
    print("\n3. 转换为 ChromaDB chunks...")
    chunks = convert_to_chunks(records)
    print(f"   ✅ 转换了 {len(chunks)} 个 chunks")

    # 4. 写入 ChromaDB
    print("\n4. 写入 external_docs 集合...")
    if chunks:
        add_chunks(
            chunks=chunks,
            collection_name="external_docs"
        )
        print(f"   ✅ 写入 {len(chunks)} 个 chunks")

    # 5. 验证
    print("\n5. 验证结果...")
    count = collection_count("external_docs")
    print(f"   ✅ external_docs 集合中有 {count} 个文档")

    print("\n=== 重建完成 ===")


if __name__ == "__main__":
    main()
