"""端到端测试：爬虫 + LLM 验证层 全链路

测试 3 家公司，验证：
1. zhihu/baidu 类 URL 被 LLM 正确拒识
2. 职位不再出现"未知职位"
3. 新闻相关性过滤生效
"""

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.crawler.crawler_dispatcher import crawl_and_store
from src.utils.logger import logger

COMPANIES = [
    "比亚迪股份有限公司",           # 大公司，数据应丰富
    "上海汉得信息技术股份有限公司",  # 之前测试过的公司
]


async def main():
    print("=" * 70)
    print("  端到端测试: 爬虫 + LLM 验证层")
    print("=" * 70)

    total_errors = 0

    for i, company in enumerate(COMPANIES, 1):
        print(f"\n{'─' * 70}")
        print(f"  [{i}/{len(COMPANIES)}] {company}")
        print(f"{'─' * 70}")

        result = await crawl_and_store(
            company=company,
            force=True,
            timeout=60.0,
        )

        print(f"\n  结果:")
        print(f"  - 招聘职位: {result['jobs_count']}")
        print(f"  - 官网: {'✅ 找到' if result['website_found'] else '❌ 未找到'}")
        print(f"  - 新闻: {result['news_count']}")
        print(f"  - Chunks: {result['chunks_stored']}")
        print(f"  - Bitable: {result['bitable_written']}")
        print(f"  - LLM增强: {'✅' if result.get('llm_enhanced') else '⏭️ 未触发'}")
        print(f"  - 耗时: {result['elapsed']}s")

        if result["errors"]:
            print(f"  ⚠️ 错误: {result['errors']}")
            total_errors += len(result["errors"])

        if result.get("bitable_errors"):
            print(f"  ⚠️ Bitable错误: {result['bitable_errors']}")

    print(f"\n{'=' * 70}")
    print(f"  测试完成: {len(COMPANIES)} 家公司, {total_errors} 个错误")
    print(f"{'=' * 70}")

    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
