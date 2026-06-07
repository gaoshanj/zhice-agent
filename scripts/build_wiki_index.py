#!/usr/bin/env python3
"""Wiki 索引构建脚本 — Phase 2

从飞书 Wiki 拉取文档 → 分块 → embedding → 写入 ChromaDB。

使用方式：
    # 构建默认空间（需配置 FEISHU_WIKI_SPACE_ID）
    python scripts/build_wiki_index.py

    # 指定空间 ID
    python scripts/build_wiki_index.py --space-id YOUR_SPACE_ID

    # 只重建索引（清空后重建）
    python scripts/build_wiki_index.py --rebuild

环境变量依赖（在 .env 中配置）：
    FEISHU_APP_ID
    FEISHU_APP_SECRET
    FEISHU_WIKI_SPACE_ID   # 要索引的 Wiki 空间 ID
    AZURE_OPENAI_ENDPOINT
    AZURE_OPENAI_API_KEY
    AZURE_EMBEDDING_DEPLOYMENT
"""

from __future__ import annotations

import argparse
import sys
import time

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pathlib import Path

from dotenv import load_dotenv

load_dotenv("env", override=False)

from src.rag.feishu_wiki import fetch_all_wiki_documents, list_wiki_spaces
from src.rag.document_loader import split_documents
from src.rag.vector_store import add_chunks, clear_collection, collection_count
from src.utils.config import settings
from src.utils.logger import logger


def build_index(space_id: str, rebuild: bool = False) -> None:
    """执行完整的索引构建流程"""

    if rebuild:
        logger.warning("Rebuild 模式：将清空现有集合后重建")
        clear_collection(settings.chroma_collection_internal)

    # 1. 拉取 Wiki 文档
    logger.info(f"开始拉取 Wiki 空间：{space_id}")
    documents = fetch_all_wiki_documents(space_id)
    if not documents:
        logger.error("未拉取到任何文档，请检查 space_id 和飞书应用权限")
        sys.exit(1)
    logger.info(f"拉取完成：{len(documents)} 篇文档")

    # 2. 分块
    logger.info("开始文本分块...")
    chunks = split_documents(documents, chunk_size=800, chunk_overlap=150)
    if not chunks:
        logger.error("分块结果为空")
        sys.exit(1)
    logger.info(f"分块完成：{len(chunks)} 个 chunk")

    # 3. 写入向量库
    logger.info("开始生成 embedding 并写入向量库...")
    start = time.time()
    count = add_chunks(chunks, collection_name=settings.chroma_collection_internal)
    elapsed = time.time() - start
    logger.info(f"写入完成：{count} 条（耗时 {elapsed:.1f}s）")

    # 4. 验证
    total = collection_count()
    logger.info(f"集合 '{settings.chroma_collection_internal}' 当前共有 {total} 条记录")


def interactive_select_space() -> str:
    """交互式选择 Wiki 空间（如果未配置 space_id）"""
    spaces = list_wiki_spaces()
    if not spaces:
        logger.error("未找到任何 Wiki 空间，请在飞书开放平台创建 Wiki 并授权应用访问")
        sys.exit(1)

    print("\n检测到多个 Wiki 空间，请选择：")
    for i, sp in enumerate(spaces, 1):
        print(f"  [{i}] {sp.get('name', sp.get('space_id', ''))}  (id={sp.get('space_id', '')})")
    print()

    while True:
        try:
            choice = input("请输入编号：").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(spaces):
                return spaces[idx]["space_id"]
            else:
                print(f"请输入 1~{len(spaces)} 之间的数字")
        except ValueError:
            print("请输入有效数字")


def main():
    parser = argparse.ArgumentParser(description="构建飞书 Wiki RAG 索引")
    parser.add_argument("--space-id", default="", help="Wiki 空间 ID（留空则自动检测或从配置读取）")
    parser.add_argument("--rebuild", action="store_true", help="清空现有索引后重建")
    args = parser.parse_args()

    # 确定 space_id
    space_id = args.space_id or settings.feishu_wiki_space_id or ""
    if not space_id:
        space_id = interactive_select_space()
        print(f"\n已选择空间：{space_id}")
        print("（可将该值写入 .env 的 FEISHU_WIKI_SPACE_ID 避免重复选择）\n")

    build_index(space_id, rebuild=args.rebuild)
    logger.info("🎉 索引构建完成！")


if __name__ == "__main__":
    main()
