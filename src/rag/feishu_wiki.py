"""飞书 Wiki API 封装 — Phase 2

支持两种认证方式：
1. tenant_access_token（应用身份）— 默认，用于消息/普通 API
2. user_access_token（用户身份）— 用于 Wiki 空间访问（绕过应用授权限制）

优先级：user_access_token > tenant_access_token（对于 Wiki API）
"""

import json
import time
from typing import Any, Optional

import httpx

from src.utils.config import settings
from src.utils.logger import logger


# ─── 飞书开放平台 API 基础地址 ──────────────────────────
BASE_URL = "https://open.feishu.cn/open-apis"


def _get_tenant_token() -> str:
    """获取 tenant_access_token（缓存，自动刷新）"""
    token = getattr(_get_tenant_token, "_token", None)
    expire_at = getattr(_get_tenant_token, "_expire_at", 0)
    now = time.time()
    if token and now < expire_at - 60:
        return token

    url = f"{BASE_URL}/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": settings.feishu_app_id,
        "app_secret": settings.feishu_app_secret,
    }
    resp = httpx.post(url, json=payload, timeout=10)
    data = resp.json()
    token = data["tenant_access_token"]
    expire_at = now + data.get("expire", 7200)
    _get_tenant_token._token = token
    _get_tenant_token._expire_at = expire_at
    logger.info("飞书 tenant_access_token 已刷新")
    return token


def _get_user_token() -> Optional[str]:
    """获取 user_access_token（用户 OAuth 授权）"""
    try:
        from src.rag.feishu_oauth import get_user_access_token
        return get_user_access_token()
    except ImportError:
        return None
    except Exception as e:
        logger.warning(f"获取用户 token 失败: {e}")
        return None


def _headers(wiki_api: bool = False) -> dict:
    """构建请求头，Wiki API 优先使用用户 token

    Args:
        wiki_api: 是否为 Wiki API 调用。Wiki API 优先使用 user_access_token。
    """
    if wiki_api:
        user_token = _get_user_token()
        if user_token:
            return {
                "Authorization": f"Bearer {user_token}",
                "Content-Type": "application/json; charset=utf-8",
            }
    return {
        "Authorization": f"Bearer {_get_tenant_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }


# ─── 公开接口（供外部调用）────────────────────────────────


def list_wiki_spaces(page_size: int = 20) -> list[dict]:
    """列出企业所有 Wiki 空间

    Returns:
        [{"space_id":..., "name":..., "description":...}, ...]
    """
    url = f"{BASE_URL}/wiki/v2/spaces"
    params = {"page_size": page_size}
    resp = httpx.get(url, headers=_headers(wiki_api=True), params=params, timeout=15)
    data = resp.json()
    if data.get("code") != 0:
        logger.error(f"列出 Wiki 空间失败: {data}")
        return []
    items = data.get("data", {}).get("items", [])
    logger.info(f"找到 {len(items)} 个 Wiki 空间")
    return items


def list_wiki_nodes(space_id: str, parent_node_token: str = "") -> list[dict]:
    """列出 Wiki 空间下的所有节点（递归展开子节点）

    Args:
        space_id: Wiki 空间 ID
        parent_node_token: 父节点 token，空字符串表示根节点

    Returns:
        [{"node_token":..., "title":..., "node_type":..., "parent_node_token":...}, ...]
    """
    url = f"{BASE_URL}/wiki/v2/spaces/{space_id}/nodes"
    params: dict[str, Any] = {"page_size": 50}
    if parent_node_token:
        params["parent_node_token"] = parent_node_token

    all_nodes: list[dict] = []
    page_token = ""

    while True:
        if page_token:
            params["page_token"] = page_token
        resp = httpx.get(url, headers=_headers(wiki_api=True), params=params, timeout=15)
        data = resp.json()
        if data.get("code") != 0:
            logger.error(f"列出 Wiki 节点失败: {data}")
            break
        d = data.get("data", {})
        for item in d.get("items", []):
            node_info = {
                "node_token": item.get("node_token", ""),
                "title": item.get("title", ""),
                "node_type": item.get("obj_type", ""),  # "wiki", "docx", "sheet" 等
                "parent_node_token": parent_node_token,
                "space_id": space_id,
            }
            all_nodes.append(node_info)
            # 递归展开子节点
            children = list_wiki_nodes(space_id, item.get("node_token", ""))
            all_nodes.extend(children)

        page_token = d.get("page_token", "")
        if not page_token:
            break

    logger.info(f"Wiki 空间 {space_id} 共找到 {len(all_nodes)} 个节点")
    return all_nodes


def get_docx_blocks(document_id: str, page_size: int = 500) -> list[dict]:
    """获取 DocX 文档的所有 Block（递归展开子 Block）

    Args:
        document_id: 文档 ID（即 node_token）
        page_size: 单次拉取 Block 数量

    Returns:
        Block 列表（平铺）
    """
    url = f"{BASE_URL}/docx/v1/documents/{document_id}/blocks"
    params: dict[str, Any] = {"page_size": page_size}

    all_blocks: list[dict] = []
    page_token = ""

    while True:
        if page_token:
            params["page_token"] = page_token
        resp = httpx.get(url, headers=_headers(wiki_api=True), params=params, timeout=15)
        data = resp.json()
        if data.get("code") != 0:
            logger.error(f"获取文档 Block 失败 document_id={document_id}: {data}")
            break
        d = data.get("data", {})
        items = d.get("items", [])
        for block in items:
            all_blocks.append(block)
        page_token = d.get("page_token", "")
        if not page_token:
            break

    logger.debug(f"文档 {document_id} 共 {len(all_blocks)} 个 Block")
    return all_blocks


def blocks_to_text(blocks: list[dict]) -> str:
    """将 DocX Block 列表转换为纯文本

    只提取 text_run / heading / code_block 等文字内容，
    忽略图片、表格等富媒体（Phase 2 暂不支持）。
    """
    lines: list[str] = []

    for block in blocks:
        block_type = block.get("block_type", "")
        # block_type 枚举：
        #   1: 正文（text）
        #   2: 标题（heading）
        #   3: 代码块（code）
        #   4: 引用
        #   5: 列表（bullet / ordered）
        #   7: 图片（跳过）
        #   12: 表格（跳过）
        #   13: 分栏（跳过）
        # 详见飞书文档 Block 结构

        parent = block.get("parent", {})
        # 提取文字内容
        text_content = _extract_text_from_block(block)
        if text_content:
            lines.append(text_content)

    return "\n".join(lines)


def _extract_text_from_block(block: dict) -> str:
    """从单个 Block 中提取文字"""
    # 飞书 DocX Block 结构：
    # block = {
    #   "block_id": "...",
    #   "block_type": 1,
    #   "text": {"elements": [{"text_run": {"content": "..."}}]}
    # }
    elements = block.get("text", {}).get("elements", [])
    parts: list[str] = []
    for elem in elements:
        # text_run
        if "text_run" in elem:
            parts.append(elem["text_run"].get("content", ""))
        # mention / equation 等暂忽略
    return "".join(parts)


def fetch_wiki_document(node_token: str, space_id: str) -> str:
    """获取单个 Wiki 节点的完整文档文本

    Args:
        node_token: Wiki 节点 token
        space_id: 所属空间 ID

    Returns:
        文档纯文本；获取失败返回空字符串
    """
    try:
        blocks = get_docx_blocks(node_token)
        text = blocks_to_text(blocks)
        logger.info(f"获取文档 {node_token} 成功（{len(text)} 字）")
        return text
    except Exception as e:
        logger.error(f"获取文档 {node_token} 失败: {e}", exc_info=True)
        return ""


# ─── 批量任务：拉取整个 Wiki 空间的内容 ────────────────────────


def fetch_all_wiki_documents(space_id: str) -> list[dict[str, Any]]:
    """拉取指定 Wiki 空间内所有文档的内容

    Returns:
        [{"node_token":..., "title":..., "space_id":..., "content":...}, ...]
    """
    nodes = list_wiki_nodes(space_id)
    documents: list[dict[str, Any]] = []

    for node in nodes:
        node_token = node["node_token"]
        title = node["title"]
        node_type = node["node_type"]

        # 只处理 DocX 文档类型
        if node_type not in ("docx", "doc"):
            logger.debug(f"跳过非文档节点: {title}（类型={node_type}）")
            continue

        content = fetch_wiki_document(node_token, space_id)
        if content:
            documents.append({
                "node_token": node_token,
                "title": title,
                "space_id": space_id,
                "content": content,
                "url": f"https://{space_id}.feishu.cn/wiki/{node_token}",
            })

    logger.info(f"Wiki 空间 {space_id} 共拉取 {len(documents)} 篇文档")
    return documents
