"""course_search 完整度优先重排单元测试

验证：在相关候选中，信息完整性好（受众+大纲都有）的课程优先输出；
同一完整度内按语义距离（越近越优）排序。
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

from src.rag.retriever import course_search, _course_completeness


def _chunk(course_number: str, distance: float, has_audience: bool, has_outline: bool) -> dict:
    """构造一条 similarity_search 返回结果"""
    audience = "面向 IT 管理员与开发者" if has_audience else "（无）"
    outline = "- 模块一：基础\n- 模块二：进阶" if has_outline else "（无）"
    content = (
        f"# {course_number} — 课程\n"
        f"课程编号: {course_number}\n"
        f"## 学员对象详述\n{audience}\n"
        f"## 课程大纲\n{outline}\n"
    )
    return {
        "content": content,
        "metadata": {"course_number": course_number, "title": course_number, "url": ""},
        "distance": distance,
    }


def test_completeness_priority_over_distance():
    """最相似但残缺的课程不应被选中；选完整度最高的候选"""
    candidates = [
        _chunk("A-100T00", distance=0.10, has_audience=False, has_outline=False),  # 最相似但残缺
        _chunk("B-200T00", distance=0.30, has_audience=True, has_outline=True),   # 完整
        _chunk("C-300T00", distance=0.50, has_audience=True, has_outline=False),  # 部分
    ]
    with patch("src.rag.retriever.collection_count", return_value=125), \
         patch("src.rag.retriever.similarity_search", return_value=candidates), \
         patch("src.rag.retriever.settings") as m_settings:
        m_settings.chroma_collection_course = "course_docs"
        result = course_search("Copilot Studio", top_k=1)

    assert len(result) == 1
    assert result[0]["course_number"] == "B-200T00"
    assert result[0]["completeness"] == 2


def test_distance_tiebreak_within_same_completeness():
    """两门都完整时，选距离更近（更相关）的一门"""
    candidates = [
        _chunk("X-900T00", distance=0.40, has_audience=True, has_outline=True),
        _chunk("Y-800T00", distance=0.20, has_audience=True, has_outline=True),
    ]
    with patch("src.rag.retriever.collection_count", return_value=125), \
         patch("src.rag.retriever.similarity_search", return_value=candidates), \
         patch("src.rag.retriever.settings") as m_settings:
        m_settings.chroma_collection_course = "course_docs"
        result = course_search("Azure AI", top_k=1)

    assert result[0]["course_number"] == "Y-800T00"


def test_fallback_to_similarity_when_all_incomplete():
    """所有候选都残缺时，回退为纯相似度排序（最相似优先）"""
    candidates = [
        _chunk("A-100T00", distance=0.10, has_audience=False, has_outline=False),
        _chunk("B-200T00", distance=0.30, has_audience=False, has_outline=False),
    ]
    with patch("src.rag.retriever.collection_count", return_value=125), \
         patch("src.rag.retriever.similarity_search", return_value=candidates), \
         patch("src.rag.retriever.settings") as m_settings:
        m_settings.chroma_collection_course = "course_docs"
        result = course_search("Power BI", top_k=1)

    assert result[0]["course_number"] == "A-100T00"


def test_empty_collection_returns_empty():
    with patch("src.rag.retriever.collection_count", return_value=0):
        assert course_search("anything") == []


def test_completeness_scoring_direct():
    complete = _chunk("Z-1", distance=0.1, has_audience=True, has_outline=True)["content"]
    partial = _chunk("Z-2", distance=0.1, has_audience=True, has_outline=False)["content"]
    none = _chunk("Z-3", distance=0.1, has_audience=False, has_outline=False)["content"]
    assert _course_completeness(complete) == 2
    assert _course_completeness(partial) == 1
    assert _course_completeness(none) == 0
