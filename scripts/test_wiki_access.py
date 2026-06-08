#!/usr/bin/env python3
"""测试直接访问指定 Wiki 空间"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path, override=False)

import httpx
from src.utils.config import settings

# 1. 获取 token
url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
resp = httpx.post(url, json={"app_id": settings.feishu_app_id, "app_secret": settings.feishu_app_secret}, timeout=10)
token = resp.json()["tenant_access_token"]
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json; charset=utf-8",
}

space_id = settings.feishu_wiki_space_id
if not space_id:
    print("❌ FEISHU_WIKI_SPACE_ID 未配置！")
    sys.exit(1)
print(f"测试 space_id: {space_id}")

# 2. 获取空间信息
info_url = f"https://open.feishu.cn/open-apis/wiki/v2/spaces/{space_id}"
info_resp = httpx.get(info_url, headers=headers, timeout=15)
print(f"\n空间信息 API (status={info_resp.status_code}):")
print(info_resp.text[:1000])

# 3. 获取空间节点列表
nodes_url = f"https://open.feishu.cn/open-apis/wiki/v2/spaces/{space_id}/nodes"
nodes_resp = httpx.get(nodes_url, headers=headers, params={"page_size": 50}, timeout=15)
print(f"\n节点列表 API (status={nodes_resp.status_code}):")
print(nodes_resp.text[:2000])
