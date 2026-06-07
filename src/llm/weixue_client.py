"""
问学系统 LLM 客户端（占位，生产阶段补充实现）

问学系统提供 OpenAI 兼容的 API，切换时只需填入以下环境变量：
  WEIXUE_API_BASE=http://your-weixue-endpoint/v1
  WEIXUE_API_KEY=your-key
  WEIXUE_MODEL=your-model-name
"""

from openai import OpenAI
from src.utils.config import settings
from src.utils.logger import logger


def get_weixue_client() -> OpenAI:
    """获取问学系统客户端（OpenAI 兼容）"""
    if not settings.weixue_api_base:
        raise RuntimeError("问学系统 API 地址未配置，请设置 WEIXUE_API_BASE 环境变量")
    return OpenAI(
        base_url=settings.weixue_api_base,
        api_key=settings.weixue_api_key,
    )


async def chat_completion(messages: list[dict], temperature: float = 0.3) -> str:
    """调用问学系统 LLM 生成内容"""
    client = get_weixue_client()
    try:
        response = client.chat.completions.create(
            model=settings.weixue_model,
            messages=messages,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"问学系统 LLM 调用失败: {e}")
        raise
