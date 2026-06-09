"""从飞书多维表格拉取数据（用于知识库导入）

用法：
    cd zhice-agent
    python scripts/fetch_bitable.py
"""

import httpx
import json
import sys
import os

# 确保能找到 src
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.utils.config import settings

BASE_URL = "https://open.feishu.cn/open-apis"
BASE_TOKEN = "CeitbAhJGaHqD1s1EricZp9intf"
TABLE_ID = "tblHp4aCxwHDJXKJ"


def get_token() -> str:
    """获取访问 token，优先使用 bot 身份（app_access_token）"""
    app_url = f"{BASE_URL}/auth/v3/app_access_token/internal"
    r = httpx.post(app_url, json={
        "app_id": settings.feishu_app_id,
        "app_secret": settings.feishu_app_secret,
    }, timeout=10)
    r.raise_for_status()
    app_data = r.json()
    if app_data.get("code") != 0:
        raise RuntimeError(f"获取 app_access_token 失败: {app_data}")
    token = app_data["app_access_token"]
    print(f"✅ app_access_token 获取成功")
    return token


def list_fields(token: str) -> list:
    """列出多维表格的所有字段"""
    url = f"{BASE_URL}/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/fields"
    r = httpx.get(url, headers={
        "Authorization": f"Bearer {token}",
    }, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"列出字段失败: {data.get('msg', data)}")
    return data["data"]["items"]


def list_records(token: str, page_token: str = None, limit: int = 500) -> dict:
    """列出多维表格的所有记录（支持分页）"""
    url = f"{BASE_URL}/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/records"
    params = {"page_size": min(limit, 500)}
    if page_token:
        params["page_token"] = page_token
    r = httpx.get(url, params=params, headers={
        "Authorization": f"Bearer {token}",
    }, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"列出记录失败: {data.get('msg', data)}")
    return data["data"]


def main():
    token = get_token()

    # 1. 列出字段
    print("\n📋 字段列表:")
    fields = list_fields(token)
    for f in fields:
        print(f"  - {f['field_name']} ({f['type']}) [id={f['field_id']}]")
    print(f"\n  共 {len(fields)} 个字段")

    # 2. 列出全部记录
    print("\n📊 记录数据:")
    all_records = []
    page_token = None
    page = 0

    while True:
        page += 1
        data = list_records(token, page_token=page_token)
        items = data.get("items", [])
        all_records.extend(items)
        print(f"  第 {page} 页: {len(items)} 条 (累计 {len(all_records)})")
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")

    print(f"\n  ✅ 共 {len(all_records)} 条记录\n")

    # 3. 打印每条记录
    for i, rec in enumerate(all_records):
        print(f"--- 记录 {i+1} ---")
        fields_data = rec.get("fields", {})
        for key, value in fields_data.items():
            # 截断过长文本
            val_str = json.dumps(value, ensure_ascii=False)
            if len(val_str) > 200:
                val_str = val_str[:200] + "..."
            print(f"  {key}: {val_str}")
        print()

    # 4. 保存到文件
    output_file = os.path.join(os.path.dirname(__file__), "..", "data", "bitable_export.json")
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    export = {
        "base_token": BASE_TOKEN,
        "table_id": TABLE_ID,
        "fields": fields,
        "records": [{"record_id": r.get("record_id"), "fields": r.get("fields", {})} for r in all_records],
    }
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, indent=2)
    print(f"💾 已保存到 {output_file}")


if __name__ == "__main__":
    main()
