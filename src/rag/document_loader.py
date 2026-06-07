"""文档加载与文本分块 — Phase 2"""

from __future__ import annotations

from typing import Any


# ─── 文本分块 ──────────────────────────────────────────────────────────


def split_text(
    text: str,
    chunk_size: int = 800,
    chunk_overlap: int = 150,
) -> list[str]:
    """将长文本按字符数切分为重叠块（纯 Python 实现，不依赖 langchain）。

    Args:
        text: 待分块文本
        chunk_size: 每块最大字符数（中文约 400~500 字）
        chunk_overlap: 相邻块重叠字符数

    Returns:
        分块结果列表
    """
    if not text or len(text) <= chunk_size:
        return [text] if text else []

    chunks: list[str] = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunk = text[start:end]
        chunks.append(chunk)
        if end >= text_len:
            break
        start += chunk_size - chunk_overlap

    logger.info(f"文本分块完成：{text_len} 字 → {len(chunks)} 块（size={chunk_size}, overlap={chunk_overlap}）")
    return chunks


def split_documents(
    documents: list[dict[str, Any]],
    chunk_size: int = 800,
    chunk_overlap: int = 150,
) -> list[dict[str, Any]]:
    """对文档列表逐篇分块，返回带元数据的 chunk 列表。

    每篇文档会被拆成 N 个 chunk，每个 chunk 附带原始文档的元数据。

    Returns:
        [{"chunk_id":..., "content":..., "metadata": {...}}, ...]
    """
    results: list[dict[str, Any]] = []

    for doc in documents:
        content = doc.get("content", "")
        if not content.strip():
            continue
        chunks = split_text(content, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        for i, chunk_text in enumerate(chunks):
            results.append({
                "chunk_id": f"{doc['node_token']}::chunk_{i}",
                "content": chunk_text,
                "metadata": {
                    "node_token": doc.get("node_token", ""),
                    "title": doc.get("title", ""),
                    "space_id": doc.get("space_id", ""),
                    "url": doc.get("url", ""),
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                },
            })

    logger.info(f"文档分块完成：{len(documents)} 篇 → {len(results)} 个 chunk")
    return results


# ─── 延迟导入 logger（避免循环导入）────────────────────────────
from src.utils.logger import logger  # noqa: E402
