"""构建微软官方培训课程知识库（course_docs 集合）

用法:
    python scripts/build_course_index.py                      # 使用默认列表
    python scripts/build_course_index.py AB-620T00 GH-200T00   # 指定课程编号
    python scripts/build_course_index.py --file scripts/course_list.txt

课程编号取自 FY27 Q1 Course Map。每次运行清空并重建 course_docs 集合。
需要先配置 .env 中的 Azure OpenAI 凭据（用于生成 embedding）。
"""
from __future__ import annotations

import asyncio
import argparse
import sys
from pathlib import Path

# 确保项目根目录在 sys.path（脚本位于 scripts/ 下）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.llm.learn_search import build_course_knowledge_list, format_course_knowledge
from src.rag.vector_store import clear_collection, add_chunks
from src.utils.config import settings
from src.utils.logger import logger


# Course Map (FY27 Q1) 默认课程列表 —— 用户可从图片补充完整列表到 --file
DEFAULT_COURSES = [
    "AB-620T00",   # Copilot Studio 集成 AI Agent 解决方案（12 天）
    "GH-200T00",    # GitHub Actions 自动化（1 天）
    "DP-604T00",    # Microsoft Fabric 数据科学与 ML（AI）
    "MB-310T00",    # Dynamics 365 Finance
    "MS-4014",      # 创建智能 agent（Course Map）
    "AI-3016",      # 构建 AI Agent（Course Map）
    "PL-7008",      # Copilot Studio（可能为演进编号，找不到则跳过）
]


def _read_course_list(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        logger.warning(f"课程列表文件不存在: {path}")
        return []
    nums: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            nums.append(line)
    return nums


async def run_build(course_numbers: list[str], locale: str = "en-us") -> int:
    """构建课程知识库核心逻辑，返回写入的课程数（CLI 与 /admin 端点共用）"""
    if not course_numbers:
        logger.error("没有要构建的课程编号")
        return 0
    logger.info(f"开始构建课程知识库，共 {len(course_numbers)} 门课程")
    parsed_list = await build_course_knowledge_list(course_numbers, locale=locale)
    if not parsed_list:
        logger.error("没有任何课程构建成功")
        return 0

    chunks: list[dict] = []
    for parsed in parsed_list:
        content = format_course_knowledge(parsed)
        if not content:
            continue
        chunks.append({
            "chunk_id": f"course_{parsed['course_number']}",
            "content": content,
            "metadata": {
                "source": "course",
                "course_number": parsed["course_number"],
                "title": parsed["title"],
                "products": ",".join(parsed.get("products", [])),
                "roles": ",".join(parsed.get("roles", [])),
                "levels": ",".join(parsed.get("levels", [])),
                "duration_days": parsed.get("duration_days", 0),
                "url": parsed.get("url", ""),
            },
        })

    # 清空旧集合后重建（保证幂等）
    clear_collection(settings.chroma_collection_course)
    n = add_chunks(chunks, collection_name=settings.chroma_collection_course)
    logger.info(f"课程知识库构建完成: {n} 门课程写入 {settings.chroma_collection_course}")
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="构建微软培训课程知识库 course_docs")
    parser.add_argument("courses", nargs="*", help="课程编号（可选）")
    parser.add_argument("--file", help="课程编号文件（每行一个）")
    parser.add_argument("--locale", default="en-us")
    args = parser.parse_args()

    if args.courses:
        course_numbers = args.courses
    elif args.file:
        course_numbers = _read_course_list(args.file)
    else:
        course_numbers = DEFAULT_COURSES

    if not course_numbers:
        logger.error("没有要构建的课程编号，终止")
        return

    n = asyncio.run(run_build(course_numbers, locale=args.locale))
    if n == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
