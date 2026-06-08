#!/usr/bin/env python3
"""飞书 OAuth 用户授权 — 获取 refresh_token

使用方法：
  1. 确保已在飞书开放平台配置重定向 URL: http://localhost:8765/oauth/callback
  2. 运行本脚本: python scripts/get_user_token.py
  3. 在浏览器中打开打印的授权链接
  4. 授权完成后，脚本自动获取 refresh_token
  5. 将 refresh_token 配置到 .env 和 Azure 环境变量

环境变量名称: FEISHU_USER_REFRESH_TOKEN
"""

import hashlib
import secrets
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path, override=False)

from src.utils.config import settings



app_id = settings.feishu_app_id
if not app_id:
    print("❌ FEISHU_APP_ID 未配置，请检查 .env 文件")
    sys.exit(1)

redirect_uri = settings.feishu_oauth_redirect_uri  # "http://localhost:8765/oauth/callback"
state = secrets.token_hex(16)
server_port = 8765

# 构建飞书授权 URL
params = {
    "app_id": app_id,
    "redirect_uri": redirect_uri,
    "state": state,
}
auth_url = (
    "https://open.feishu.cn/open-apis/authen/v1/authorize?"
    + urllib.parse.urlencode(params)
)

print("=" * 60)
print("🔐 飞书 OAuth 用户授权")
print("=" * 60)
print()
print("📋 步骤:")
print(f"   1. 确保飞书开放平台已配置重定向 URL: {redirect_uri}")
print()
print("📎 请用浏览器打开以下链接并授权:")
print()
print(f"   {auth_url}")
print()
print("⏳ 等待授权完成...")
print()

# ── 本地 HTTP 服务器接收回调 ──────────────────────────
code_received: str | None = None
received_state: str | None = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global code_received, received_state
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/oauth/callback":
            query = urllib.parse.parse_qs(parsed.query)
            received_state = query.get("state", [None])[0]
            code = query.get("code", [None])[0]
            error = query.get("error", [None])[0]

            if error:
                body = f"<h1>授权失败</h1><p>错误: {error}</p>"
                self._respond(400, body)
                return

            if not code:
                body = "<h1>授权失败</h1><p>未收到授权码</p>"
                self._respond(400, body)
                return

            if received_state != state:
                body = "<h1>授权失败</h1><p>state 不匹配（可能有 CSRF 攻击）</p>"
                self._respond(400, body)
                return

            code_received = code
            body = "<h1>✅ 授权成功！</h1><p>可以关闭此页面，回到终端查看结果。</p>"
            self._respond(200, body)
        else:
            self._respond(404, "<h1>404</h1>")

    def _respond(self, status: int, body: str):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):
        pass  # 禁用访问日志


def run_server():
    server = HTTPServer(("localhost", server_port), CallbackHandler)
    server.timeout = 1  # 每秒检查一次

    start = time.time()
    timeout = 120  # 2 分钟超时
    while code_received is None and (time.time() - start) < timeout:
        server.handle_request()

    server.server_close()


run_server()

if code_received is None:
    print("❌ 授权超时（2 分钟），请重试")
    sys.exit(1)

# ── 用 code 换取 token ────────────────────────────────
print("🔑 正在换取 access_token...")

from src.rag.feishu_oauth import init_user_token_from_code

try:
    refresh_token = init_user_token_from_code(code_received)
except RuntimeError as e:
    print(f"❌ 换取 token 失败: {e}")
    sys.exit(1)

print()
print("=" * 60)
print("✅ 授权完成！")
print("=" * 60)
print()
print("📝 请将以下内容添加到 .env 文件中：")
print()
print(f"   FEISHU_USER_REFRESH_TOKEN={refresh_token}")
print()
print("🔧 同时添加到 Azure App Service 环境变量（同名）：")
print(f"   名称: FEISHU_USER_REFRESH_TOKEN")
print(f"   值: {refresh_token}")
print()
print("⚡ 提示: 配置完成后触发一次 reindex，即可用用户身份读取 Wiki 数据。")
