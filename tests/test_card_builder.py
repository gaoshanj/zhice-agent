"""卡片构建器单元测试"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.bot.card_builder import build_report_card, build_error_card, build_help_card


class TestCardBuilder:
    """测试卡片构建"""

    def test_full_report_card(self):
        """完整报告卡片"""
        report = {
            "company": "华云数据科技",
            "snapshot": "华云数据科技是一家专注于云计算...",
            "opportunity_scan": "🎯 高潜力：微软 Agent 培训...",
            "cross_sell": "🔗 培训+咨询捆绑...",
            "strategy": "1. 建立高层关系...",
            "talk_script": "> 很高兴见到您...",
            "action_plan": "⏰ 24小时内：发送感谢信...",
            "generated_at": "2026-06-07 12:00 UTC",
        }
        card = build_report_card(report)

        # 验证必需字段
        assert "header" in card
        assert "elements" in card
        assert "config" in card

        # 验证标题包含公司名
        assert "华云数据科技" in card["header"]["title"]["content"]

        # 验证元素包含报告内容
        elements = card["elements"]
        assert len(elements) >= 7  # 6 节 + 尾部信息

    def test_empty_report(self):
        """空报告（兜底）"""
        card = build_report_card({"company": "测试"})
        assert "header" in card
        assert "elements" in card

    def test_error_card(self):
        """错误卡片"""
        card = build_error_card("测试错误", "测试公司")
        assert "测试公司" in card["header"]["title"]["content"]
        assert "测试错误" in card["elements"][0]["text"]["content"]

    def test_help_card(self):
        """帮助卡片"""
        card = build_help_card()
        assert "使用说明" in card["header"]["title"]["content"]

    def test_card_json_serializable(self):
        """卡片 JSON 可序列化"""
        import json
        card = build_report_card({"company": "测试"})
        text = json.dumps(card, ensure_ascii=False)
        assert len(text) > 0
        parsed = json.loads(text)
        assert parsed == card


if __name__ == "__main__":
    print("运行卡片构建测试...")
    test = TestCardBuilder()
    for name in dir(test):
        if name.startswith("test_"):
            try:
                getattr(test, name)()
                print(f"  ✅ {name}")
            except AssertionError as e:
                print(f"  ❌ {name}: {e}")
            except Exception as e:
                print(f"  💥 {name}: {e}")
    print("测试完成！")
