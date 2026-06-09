#!/usr/bin/env python3
"""手动模式：提前显示授权链接，服务器先启动再引导用户点击"""
import sys, time, urllib.parse, threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
from src.utils.config import settings

PORT = 8765
STATE = "zhice-oauth-2026"

code_received = None

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global code_received
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/oauth/callback":
            q = urllib.parse.parse_qs(parsed.query)
            err = q.get("error", [None])[0]
            if err:
                self._resp(400, f"<h1>授权失败</h1><p>{err}</p>")
                return
            code = q.get("code", [None])[0]
            if not code:
                self._resp(400, "<h1>失败</h1><p>无授权码</p>")
                return
            code_received = code
            print(f"\n✅ 收到授权码！code={code[:10]}...", flush=True)
            self._resp(200, "<h1>✅ 授权成功！关闭此页面，回到终端。</h1>")
        else:
            self._resp(404, "<h1>404</h1>")

    def _resp(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *a): pass

# 先启动服务器
server = HTTPServer(("localhost", PORT), Handler)
server.timeout = 1

print(f"✅ 服务器已启动在 http://localhost:{PORT}")
print("=" * 60)

params = urllib.parse.urlencode({
    "app_id": settings.feishu_app_id,
    "redirect_uri": f"http://localhost:{PORT}/oauth/callback",
    "state": STATE,
    "scope": "bitable:app:readonly wiki:wiki wiki:wiki:readonly",
})
auth_url = f"https://open.feishu.cn/open-apis/authen/v1/authorize?{params}"
print(f"\n🔗 授权链接（现在点）：\n\n{auth_url}\n")
print("等待授权（120秒）...\n")

start = time.time()
while code_received is None and (time.time() - start) < 120:
    server.handle_request()

server.server_close()

if code_received is None:
    print("❌ 超时")
    sys.exit(1)

print("🔑 换取 token...")
from src.rag.feishu_oauth import init_user_token_from_code

try:
    refresh_token = init_user_token_from_code(code_received)
except RuntimeError as e:
    print(f"❌ {e}")
    sys.exit(1)

print(f"\n{'='*60}")
print("✅ 授权成功！新的 refresh_token:")
print(f"\nFEISHU_USER_REFRESH_TOKEN={refresh_token}\n")
print("请把上面这行告诉我，我来更新配置。")
