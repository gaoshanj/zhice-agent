#!/usr/bin/env python3
"""通过 GitHub Contents API 批量推送 Phase 1 文件到仓库"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
import urllib.error

TOKEN = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GH_TOKEN", "")
OWNER = "gaoshanj"
REPO = "zhice-agent"
BRANCH = "main"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES_TO_PUSH = [
    "src/main.py",
    "src/utils/config.py",
    "src/utils/logger.py",
    "src/utils/__init__.py",
    "src/bot/__init__.py",
    "src/bot/feishu_handler.py",
    "src/bot/message_parser.py",
    "src/bot/card_builder.py",
    "src/llm/__init__.py",
    "src/llm/azure_client.py",
    "src/llm/weixue_client.py",
    "src/llm/prompt_templates.py",
    "src/report/__init__.py",
    "src/report/generator.py",
    "tests/__init__.py",
    "tests/test_message_parser.py",
    "tests/test_card_builder.py",
    "pyproject.toml",
    ".env.example",
    ".gitignore",
]

BASE_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/contents"

def api_request(method: str, path: str, data: dict | None = None) -> dict:
    url = f"{BASE_URL}/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "zhice-agent-push",
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else str(e)
        return {"error": e.code, "message": err_body}


def push_file(filepath: str) -> tuple[str, bool]:
    """上传或更新单个文件"""
    full_path = os.path.join(ROOT, filepath)
    if not os.path.isfile(full_path):
        return filepath, False

    with open(full_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode()

    path_encoded = filepath.replace("/", "%2F")

    # 检查文件是否已存在
    existing = api_request("GET", path_encoded)
    sha = existing.get("sha") if "sha" in existing else None

    payload = {
        "message": f"Phase 1: {filepath}",
        "content": content_b64,
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha

    result = api_request("PUT", path_encoded, payload)
    if "sha" in result or "content" in result:
        return filepath, True
    else:
        print(f"  ⚠️ {filepath}: {result.get('message', result)}")
        return filepath, False


def main():
    success = 0
    skipped = 0

    print(f"🚀 推送 {len(FILES_TO_PUSH)} 个文件到 {OWNER}/{REPO}@{BRANCH}...")
    print()

    for fp in FILES_TO_PUSH:
        _, ok = push_file(fp)
        if ok:
            print(f"  ✅ {fp}")
            success += 1
        else:
            print(f"  ⚠️ {fp} (skipped)")
            skipped += 1

    print()
    print(f"完成: {success} 成功, {skipped} 跳过")


if __name__ == "__main__":
    main()
