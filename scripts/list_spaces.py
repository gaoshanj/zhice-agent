#!/usr/bin/env python3
"""快速列出所有飞书 Wiki 空间（帮助找到 space_id）"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path, override=False)

# 先试试获取 token
from src.utils.config import settings

print(f"APP_ID: {settings.feishu_app_id[:8]}..." if settings.feishu_app_id else "APP_ID: (EMPTY!)")
print(f"APP_SECRET: {'configured' if settings.feishu_app_secret else '(EMPTY!)'}")

url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
payload = {
    "app_id": settings.feishu_app_id,
    "app_secret": settings.feishu_app_secret,
}
resp = httpx.post(url, json=payload, timeout=10)
data = resp.json()
if data.get("code") != 0:
    print(f"\nToken API 响应 (status={resp.status_code}):")
    print(resp.text[:500])
    print(f"\n❌ 获取 token 失败！错误码={data.get('code')}, 消息={data.get('msg')}")
    sys.exit(1)

token = data["tenant_access_token"]
print(f"✅ Token 获取成功\n")

# 直接调用 Wiki API 并打印原始响应
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json; charset=utf-8",
}
wiki_url = "https://open.feishu.cn/open-apis/wiki/v2/spaces"
wiki_resp = httpx.get(wiki_url, headers=headers, params={"page_size": 20}, timeout=15)
print(f"Wiki API 响应 (status={wiki_resp.status_code}):")
import json
print(json.dumps(wiki_resp.json(), indent=2, ensure_ascii=False)[:2000])

from src.rag.feishu_wiki import list_wiki_spaces

spaces = list_wiki_spaces()
if not spaces:
    print("❌ 未找到任何 Wiki 空间！")
    print("请确认：1) 飞书应用已开通 Wiki 权限 2) 飞书开放平台已授权应用访问 Wiki")
    sys.exit(1)

print(f"\n找到 {len(spaces)} 个 Wiki 空间：\n")
for i, sp in enumerate(spaces, 1):
    print(f"  [{i}] {sp.get('name', '未命名')}")
    print(f"      space_id = {sp.get('space_id', 'N/A')}")
    print(f"      描述: {sp.get('description', '无')}")
    print()
