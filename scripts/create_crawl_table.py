"""在飞书多维表格中创建「爬虫数据」存储表 — Phase 3

设计：
  - 公司名 (Text) — 分组维度
  - 网址URL (URL) — 唯一ID（避免重复抓取同一页面）
  - 摘要 (Long Text) — 爬取的内容摘要
  - 抓取时间 (DateTime) — 抓取时间戳
  - 来源类型 (Single Select) — 官网/招聘

用法：
  python scripts/create_crawl_table.py
"""

import httpx
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.utils.config import settings

BASE_URL = "https://open.feishu.cn/open-apis"
BASE_TOKEN = settings.feishu_bitable_base_token  # CeitbAhJGaHqD1s1EricZp9intf
TABLE_NAME = "爬虫数据"

# 字段定义（类型编号参考飞书文档）
#   1=Text, 3=Number, 4=Select(Single), 5=DateTime
#   15=URL, 18=Long Text (Bitable 专属)
FIELD_DEFS = [
    {"field_name": "公司名", "type": 1},         # Text
    {"field_name": "网址URL", "type": 15},        # URL (link)
    {"field_name": "摘要", "type": 18},           # Long Text (richtext)
    {"field_name": "抓取时间", "type": 5},         # DateTime
    {"field_name": "来源类型", "type": 4,          # Single Select
     "property": {"options": [
         {"name": "官网"},
         {"name": "招聘"},
     ]}},
]


def get_token() -> str:
    """获取 tenant_access_token（bot 身份）"""
    url = f"{BASE_URL}/auth/v3/tenant_access_token/internal"
    r = httpx.post(url, json={
        "app_id": settings.feishu_app_id,
        "app_secret": settings.feishu_app_secret,
    }, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {data}")
    print(f"✅ tenant_access_token 获取成功")
    return data["tenant_access_token"]


def list_tables(token: str) -> list[dict]:
    """列出 Base 下所有表格"""
    url = f"{BASE_URL}/bitable/v1/apps/{BASE_TOKEN}/tables"
    r = httpx.get(url, headers={
        "Authorization": f"Bearer {token}",
    }, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"列出表格失败: {data.get('msg', data)}")
    return data["data"].get("items", [])


def create_table(token: str) -> dict:
    """创建新表格"""
    url = f"{BASE_URL}/bitable/v1/apps/{BASE_TOKEN}/tables"
    body = {
        "table": {
            "name": TABLE_NAME,
            "default_view_name": "按公司分组",
            "fields": FIELD_DEFS,
        }
    }
    r = httpx.post(url, json=body, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"创建表格失败: {data.get('msg', data)}")
    return data["data"]


def list_fields(token: str, table_id: str) -> list[dict]:
    """列出指定表格的所有字段"""
    url = f"{BASE_URL}/bitable/v1/apps/{BASE_TOKEN}/tables/{table_id}/fields"
    r = httpx.get(url, headers={
        "Authorization": f"Bearer {token}",
    }, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"列出字段失败: {data.get('msg', data)}")
    return data["data"]["items"]


def main():
    print(f"🔧 在 Base {BASE_TOKEN} 中创建「{TABLE_NAME}」表...\n")

    token = get_token()

    # 1. 检查是否已存在同名表
    print("\n📋 检查现有表格...")
    existing = list_tables(token)
    for t in existing:
        print(f"  - {t['name']} (table_id={t['table_id']})")
        if t["name"] == TABLE_NAME:
            print(f"\n⚠️  表「{TABLE_NAME}」已存在！table_id={t['table_id']}")
            print("  如果想重建，请先在飞书手动删除该表后重新运行。")
            # 显示现有字段
            fields = list_fields(token, t["table_id"])
            print(f"\n  现有字段 ({len(fields)} 个):")
            for f in fields:
                print(f"    - {f['field_name']} (type={f['type']}) [id={f['field_id']}]")
            return

    # 2. 创建新表
    print(f"\n📝 创建新表「{TABLE_NAME}」...")
    result = create_table(token)
    table_id = result["table_id"]
    print(f"✅ 表格创建成功！")
    print(f"   table_id: {table_id}")
    print(f"   url: https://bba12hub36.feishu.cn/base/{BASE_TOKEN}?table={table_id}")

    # 3. 验证字段
    print(f"\n📋 验证字段定义...")
    fields = list_fields(token, table_id)
    for f in fields:
        print(f"  ✅ {f['field_name']} (type={f['type']}) — id={f['field_id']}")

    print(f"\n🎉 完成！共 {len(fields)} 个字段")
    print(f"\n💡 下一步：在飞书打开表格查看")
    print(f"   https://bba12hub36.feishu.cn/base/{BASE_TOKEN}?table={table_id}")
    print(f"\n💡 将 table_id={table_id} 添加到 .env 文件：")
    print(f"   FEISHU_CRAWL_TABLE_ID={table_id}")


if __name__ == "__main__":
    main()
