"""ChromaDB 向量库封装 — Phase 2"""

from __future__ import annotations

import uuid
from typing import Any

from src.llm.azure_client import get_embedding
from src.utils.config import settings
from src.utils.logger import logger

import chromadb
from chromadb.config import Settings as ChromaSettings


# ─── 单例客户端（进程内复用）───────────────────────────────
_chroma_client: chromadb.Client | None = None
_collections: dict[str, chromadb.Collection] = {}


def _get_client() -> chromadb.Client:
    """获取 ChromaDB 持久化客户端（单例）"""
    global _chroma_client
    if _chroma_client is None:
        persist_dir = settings.chroma_persist_dir
        logger.info(f"初始化 ChromaDB，持久化目录: {persist_dir}")
        _chroma_client = chromadb.PersistentClient(
            path=persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
    return _chroma_client


def _get_collection(name: str) -> chromadb.Collection:
    """获取（或创建）指定名称的集合（单例缓存）"""
    if name not in _collections:
        client = _get_client()
        _collections[name] = client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},  # cosine 相似度
        )
        logger.info(f"集合就绪: {name}（共 {_collections[name].count()} 条）")
    return _collections[name]


# ─── 公开接口 ─────────────────────────────────────────────────


def add_chunks(
    chunks: list[dict[str, Any]],
    collection_name: str = "",
) -> int:
    """将分块文本批量写入向量库

    Args:
        chunks: split_documents() 的输出
        collection_name: 集合名（空则使用配置的 internal 集合）

    Returns:
        成功写入的 chunk 数量
    """
    if not chunks:
        logger.warning("add_chunks: chunks 为空，跳过")
        return 0

    name = collection_name or settings.chroma_collection_internal
    coll = _get_collection(name)

    # 批量构建参数
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    embeddings: list[list[float]] = []

    # 逐条生成 embedding（Chroma 也支持自动 embedding，但我们用 Azure 统一管）
    logger.info(f"开始为 {len(chunks)} 个 chunk 生成 embedding...")
    for chunk in chunks:
        try:
            emb = get_embedding(chunk["content"])
        except Exception as e:
            logger.warning(f"chunk {chunk['chunk_id']} embedding 失败，跳过: {e}")
            continue
        ids.append(chunk["chunk_id"])
        documents.append(chunk["content"])
        # Chroma 要求 metadata 值必须是 str / int / float / bool
        md: dict[str, Any] = {}
        for k, v in chunk.get("metadata", {}).items():
            md[k] = str(v) if not isinstance(v, (str, int, float, bool)) else v
        metadatas.append(md)
        embeddings.append(emb)

    if not ids:
        logger.error("所有 chunk 的 embedding 均失败，未写入向量库")
        return 0

    # 批量 upsert
    coll.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings,
    )
    logger.info(f"向量库写入完成: {len(ids)} 条 → {name}")
    return len(ids)


def similarity_search(
    query: str,
    top_k: int = 5,
    collection_name: str = "",
    filter_dict: dict | None = None,
) -> list[dict[str, Any]]:
    """相似度检索

    Args:
        query: 查询文本（如公司名 + 章节主题）
        top_k: 返回最相似的 N 条
        collection_name: 集合名
        filter_dict: Chroma 元数据过滤条件，如 {"space_id": "xxx"}

    Returns:
        [{"content":..., "metadata":..., "distance":...}, ...]
    """
    name = collection_name or settings.chroma_collection_internal
    try:
        coll = _get_collection(name)
    except Exception as e:
        logger.error(f"集合 {name} 不存在: {e}")
        return []

    try:
        query_emb = get_embedding(query)
    except Exception as e:
        logger.error(f"查询 embedding 失败: {e}")
        return []

    count = coll.count()
    if count == 0:
        logger.warning(f"集合 {name} 为空，跳过检索")
        return []

    results = coll.query(
        query_embeddings=[query_emb],
        n_results=min(top_k, count),
        where=filter_dict,
        include=["documents", "metadatas", "distances"],
    )

    output: list[dict[str, Any]] = []
    if not results or not results.get("ids"):
        return output

    for i, doc in enumerate(results["documents"][0]):
        output.append({
            "content": doc,
            "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
            "distance": results["distances"][0][i] if results.get("distances") else None,
        })

    logger.debug(f"相似度检索: query='{query[:50]}...' → {len(output)} 条结果")
    return output


def collection_count(collection_name: str = "") -> int:
    """返回集合中文档数量"""
    name = collection_name or settings.chroma_collection_internal
    try:
        return _get_collection(name).count()
    except Exception:
        return 0


def clear_collection(collection_name: str = "") -> None:
    """清空指定集合（慎用）"""
    name = collection_name or settings.chroma_collection_internal
    try:
        client = _get_client()
        client.delete_collection(name)
        _collections.pop(name, None)
        logger.warning(f"集合已清空: {name}")
    except Exception as e:
        logger.error(f"清空集合失败 {name}: {e}")
