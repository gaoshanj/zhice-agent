"""通过 GitHub Contents API 将项目文件推送到空仓库。"""
import base64
import json
import os
import sys
import urllib.request
import urllib.error

TOKEN = sys.argv[1]
OWNER = "gaoshanj"
REPO = "zhice-agent"
REPO_DIR = r"C:\Users\unmar\WorkBuddy\2026-06-07-19-15-56\zhice-agent"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "Content-Type": "application/json",
}

def api(method, path, data=None):
    url = f"https://api.github.com/repos/{OWNER}/{REPO}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in HEADERS.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"  API Error {e.code}: {err[:200]}", file=sys.stderr)
        raise

def collect_files(base_dir):
    files = {}
    for root, dirs, names in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".pytest_cache", "node_modules")]
        for name in names:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, base_dir).replace("\\", "/")
            with open(full, "rb") as f:
                files[rel] = f.read()
    return files

def main():
    files = collect_files(REPO_DIR)
    print(f"Found {len(files)} files to upload via API\n")

    # Upload each file via Contents API (auto-creates commits)
    success = 0
    for path, content in sorted(files.items()):
        try:
            result = api("PUT", f"/contents/{path}", {
                "message": f"Add {path}",
                "content": base64.b64encode(content).decode(),
                "branch": "main",
            })
            print(f"  OK  {path}")
            success += 1
        except Exception as e:
            print(f"  FAIL {path}: {e}")

    print(f"\n=== Done: {success}/{len(files)} files uploaded ===")
    print(f"Repo: https://github.com/{OWNER}/{REPO}")

if __name__ == "__main__":
    main()
