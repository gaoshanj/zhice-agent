"""端到端测试：飞书消息 → 解析 → RAG检索 → LLM生成 → 卡片

用法: python scripts/test_e2e_rag.py [公司名]
"""

import asyncio
import json
import sys
import time
from pathlib import Path

# 确保在项目根目录
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(".env", override=False)

from src.bot.message_parser import parse_user_input
from src.rag.vector_store import collection_count
from src.rag.retriever import retrieve_for_report, format_rag_context
from src.report.generator import generate_report
from src.bot.card_builder import build_report_card
from src.utils.logger import logger


def print_separator(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


async def main():
    company_input = sys.argv[1] if len(sys.argv) > 1 else "联想集团有限公司"

    print_separator("📥 步骤1：模拟飞书消息 + 解析")
    user_message = (
        f"@培训智策Agent 请帮我生成针对以下客户的销售策略报告：\n"
        f"- 客户公司：{company_input}\n"
        f"- 拜访对象部门/职位：技术研发部/CTO\n"
        f"- 当前已知信息（选填）：客户正在推进AI转型，已上线M365\n"
        f"- 本次拜访主要目的：首次接触/挖掘培训需求\n"
        f"- 期望侧重方向（多选）：微软Agent培训 / MSP\n"
        f"- 特别要求（选填）：重点关注Copilot相关培训\n"
    )
    parsed = parse_user_input(user_message)
    print(f"   公司: {parsed['company']}")
    print(f"   拜访对象: {parsed['visit_target']}")
    print(f"   拜访目的: {parsed['visit_purpose']}")
    print(f"   侧重方向: {parsed['focus_areas']}")
    print(f"   已知信息: {parsed['known_info'][:50]}")

    print_separator("🔍 步骤2：向量库状态")
    count = collection_count()
    print(f"   ChromaDB 文档总量: {count}")
    if count == 0:
        print("   ❌ 向量库为空，请先运行: python scripts/build_bitable_index.py")
        return

    print_separator("🔍 步骤3：RAG 检索测试（6个章节）")
    all_contexts = {}
    for section_num in range(1, 7):
        contexts = retrieve_for_report(parsed["company"], section_num)
        all_contexts[section_num] = contexts
        if contexts:
            print(f"   第{section_num}节 → {len(contexts)}条匹配:")
            for c in contexts[:2]:
                print(f"      • {c[:100]}...")
        else:
            print(f"   第{section_num}节 → 无匹配")

    print_separator("🤖 步骤4：LLM 报告生成（逐节，含RAG上下文）")
    start_time = time.time()

    try:
        report_data = await generate_report(parsed)
        elapsed = time.time() - start_time

        print(f"\n   ✅ 报告生成完成！耗时 {elapsed:.1f}s\n")

        print_separator("📊 报告内容预览（每节前150字）")
        section_names = [
            "snapshot", "opportunity_scan", "cross_sell",
            "strategy", "talk_script", "action_plan",
        ]
        section_labels = [
            "客户360°快照", "培训商机扫描", "交叉销售",
            "销售策略", "销售话术", "行动建议",
        ]
        for i, (name, label) in enumerate(zip(section_names, section_labels), 1):
            content = report_data.get(name, "[未生成]")
            preview = content[:150].replace("\n", " ") + "..."
            print(f"   [{i}] {label}: {preview}")

        print_separator("🃏 步骤5：飞书卡片构建")
        card = build_report_card(report_data)

        # 保存卡片 JSON
        card_path = Path("test_card_output.json")
        card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   卡片 JSON 已保存至: {card_path}")
        print(f"   卡片元素数: {len(card.get('elements', []))}")
        print(f"   标题: {card.get('header', {}).get('title', {}).get('content', 'N/A')}")

        # 保存完整报告
        report_path = Path("test_report_output.json")
        report_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   完整报告已保存至: {report_path}")

        print_separator("✅ 端到端测试完成")
        print(f"   总耗时: {elapsed:.1f}s")
        print(f"   RAG 匹配: {sum(len(v) for v in all_contexts.values())} 条")
        print(f"   生成章节: 6/6")
        print(f"   卡片元素: {len(card.get('elements', []))}/13")

    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n   ❌ 报告生成失败（耗时 {elapsed:.1f}s）: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
