"""飞书 OAuth 用户授权 — 管理 user_access_token 生命周期

用于以飞书用户身份访问 Wiki 空间（绕过应用授权限制）。

Token 存储层级：
1. 运行时：内存缓存（进程级，最快）
2. 持久化：本地 JSON 文件（Azure 实例重启后恢复）
3. 种子：环境变量 FEISHU_USER_REFRESH_TOKEN（首次启动或文件丢失时使用）

流程：
- 调用 get_user_access_token() → 返回有效的 token
- 自动检测过期 → 用 refresh_token 刷新
- 刷新后的新 refresh_token 写回文件
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

import httpx

from src.utils.config import settings
from src.utils.logger import logger

BASE_URL = "https://open.feishu.cn/open-apis"

# Token 持久化文件（Azure 上持久目录）
TOKEN_FILE = Path(os.environ.get("HOME", "/tmp")) / "site" / "wwwroot" / "data" / "feishu_token.json"
if not TOKEN_FILE.parent.exists():
    # 本地开发：放在 chroma_data 同级
    TOKEN_FILE = Path(settings.chroma_persist_dir).parent / "data" / "feishu_token.json"


def _load_token() -> dict:
    """从文件加载 token 信息"""
    if TOKEN_FILE.exists():
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_token(token_data: dict) -> None:
    """持久化 token 到文件"""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)
    logger.debug("飞书用户 token 已持久化")


def get_user_access_token() -> Optional[str]:
    """获取有效的 user_access_token

    Returns:
        有效的 access_token，如果未配置 OAuth 则返回 None
    """
    # 1. 内存缓存
    token = getattr(get_user_access_token, "_token", None)
    expire_at = getattr(get_user_access_token, "_expire_at", 0)
    now = time.time()
    if token and now < expire_at - 120:  # 提前 2 分钟刷新
        return token

    # 2. 从文件或环境变量读取 refresh_token
    token_data = _load_token()
    refresh_token = token_data.get("refresh_token", "")
    if not refresh_token:
        # 种子：从环境变量读取（首次启动）
        refresh_token = settings.feishu_user_refresh_token
        if not refresh_token:
            logger.debug("未配置飞书用户 OAuth（FEISHU_USER_REFRESH_TOKEN 为空），跳过用户授权")
            return None

    # 3. 刷新 access_token
    try:
        new_data = _refresh_user_token(refresh_token)
        get_user_access_token._token = new_data["access_token"]
        get_user_access_token._expire_at = now + new_data.get("expires_in", 3600)
        logger.debug("飞书 user_access_token 已刷新")
        return new_data["access_token"]
    except Exception as e:
        logger.error(f"刷新飞书 user_access_token 失败: {e}", exc_info=True)
        return None


def _refresh_user_token(refresh_token: str) -> dict:
    """用 refresh_token 换取新的 access_token

    API: POST /authen/v1/oidc/refresh_access_token
    详见: https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/authen-v1/oidc-refresh_access_token
    """
    url = f"{BASE_URL}/authen/v1/oidc/refresh_access_token"
    headers = {
        "Authorization": f"Bearer {_get_app_access_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    resp = httpx.post(url, json=payload, headers=headers, timeout=10)
    data = resp.json()

    if data.get("code") != 0:
        raise RuntimeError(f"刷新 token 失败: {data.get('msg', data)}")

    # 飞书 refresh_token 也是一次性的（刷新后旧 token 失效，返回新的）
    new_data = {
        "access_token": data["data"]["access_token"],
        "refresh_token": data["data"]["refresh_token"],
        "expires_in": data["data"].get("expires_in", 7200),
    }

    # 持久化新的 refresh_token
    _save_token(new_data)
    return new_data


def _get_app_access_token() -> str:
    """获取 app_access_token（用于 OAuth token 交换时的应用认证）"""
    token = getattr(_get_app_access_token, "_token", None)
    expire_at = getattr(_get_app_access_token, "_expire_at", 0)
    now = time.time()
    if token and now < expire_at - 60:
        return token

    url = f"{BASE_URL}/auth/v3/app_access_token/internal"
    payload = {
        "app_id": settings.feishu_app_id,
        "app_secret": settings.feishu_app_secret,
    }
    resp = httpx.post(url, json=payload, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 app_access_token 失败: {data.get('msg', data)}")
    token = data["app_access_token"]
    _get_app_access_token._token = token
    _get_app_access_token._expire_at = now + data.get("expire", 7200)
    return token


def init_user_token_from_code(code: str) -> str:
    """首次授权：用 authorization code 换取 token

    仅用于本地脚本初始化，不在生产环境使用。

    Args:
        code: OAuth 回调中的 authorization code

    Returns:
        refresh_token（用户需要保存到环境变量）
    """
    # 获取 app_access_token 用于 API 认证
    app_token = _get_app_access_token()

    url = f"{BASE_URL}/authen/v1/oidc/access_token"
    headers = {
        "Authorization": f"Bearer {app_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "grant_type": "authorization_code",
        "code": code,
    }
    resp = httpx.post(url, json=payload, headers=headers, timeout=10)
    data = resp.json()

    if data.get("code") != 0:
        raise RuntimeError(f"换取 token 失败: {data.get('msg', data)}")

    token_data = {
        "access_token": data["data"]["access_token"],
        "refresh_token": data["data"]["refresh_token"],
        "expires_in": data["data"].get("expires_in", 7200),
    }
    _save_token(token_data)

    # 同时写入内存缓存
    get_user_access_token._token = token_data["access_token"]
    get_user_access_token._expire_at = time.time() + token_data["expires_in"]

    logger.info(f"✅ 用户 OAuth 授权成功（access_token 有效期 {token_data['expires_in']}s）")
    return token_data["refresh_token"]
