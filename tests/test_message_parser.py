"""消息解析模块单元测试"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.bot.message_parser import parse_user_input


class TestParseUserInput:
    """测试 parse_user_input 的各个场景"""

    def test_structured_full_input(self):
        """完整结构化输入"""
        text = """@培训智策Agent 请帮我生成针对以下客户的销售策略报告：
- 客户公司：华云数据科技股份有限公司
- 拜访对象部门/职位：技术研发部/CTO
- 当前已知信息（选填）：该客户去年采购过AWS基础培训，反馈良好
- 本次拜访主要目的：挖掘AI培训需求，推动第二期合作
- 期望侧重方向（多选）：微软Agent培训 / AWS培训 / MSP
- 特别要求（选填）：希望报告中包含竞品对比分析"""
        result = parse_user_input(text)
        assert result["company"] == "华云数据科技股份有限公司"
        assert "技术研发部" in result["visit_target"]
        assert "AWS基础培训" in result["known_info"]
        assert "AI培训" in result["visit_purpose"]
        assert "微软Agent培训" in result["focus_areas"]
        assert "AWS培训" in result["focus_areas"]
        assert "MSP" in result["focus_areas"]
        assert "竞品对比" in result["special_req"]

    def test_minimal_input_only_company(self):
        """仅提供公司名称"""
        text = """@培训智策Agent 请帮我生成针对以下客户的销售策略报告：
- 客户公司：北京智创科技有限公司
- 拜访对象部门/职位：
- 当前已知信息（选填）：
- 本次拜访主要目的：
- 期望侧重方向（多选）：
- 特别要求（选填）："""
        result = parse_user_input(text)
        assert result["company"] == "北京智创科技有限公司"
        assert result["visit_target"] == ""
        assert result["focus_areas"] == []

    def test_free_text_company(self):
        """自由文本输入公司名"""
        text = "帮我生成上海数科信息的报告，想做微软AI培训"
        result = parse_user_input(text)
        assert "上海数科" in result["company"] or "上海数科信息" in result["company"]

    def test_with_at_mention(self):
        """包含 @ 提及"""
        text = "@培训智策Agent 深圳云创科技有限公司"
        result = parse_user_input(text)
        assert "深圳云创" in result["company"]

    def test_empty_input(self):
        """空输入"""
        result = parse_user_input("")
        assert result["company"] == ""

    def test_focus_areas_multiple_delimiters(self):
        """多用分隔符的侧重方向"""
        text = """期望侧重方向（多选）：微软Agent培训、AWS培训/MSP/安服"""
        result = parse_user_input(text)
        assert "微软Agent培训" in result["focus_areas"]
        assert "AWS培训" in result["focus_areas"]
        assert "MSP" in result["focus_areas"]
        assert "安服" in result["focus_areas"]

    def test_raw_text_preserved(self):
        """原始文本保留"""
        text = "测试内容" * 100
        result = parse_user_input(text)
        assert len(result["raw_text"]) <= 500


if __name__ == "__main__":
    print("运行消息解析测试...")
    test = TestParseUserInput()
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
