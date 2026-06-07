"""
LLM 调用统一入口
根据配置自动选择 Azure OpenAI（测试）或问学系统（生产）
"""

from src.utils.config import settings
from src.utils.logger import logger


async def chat(messages: list[dict], temperature: float = 0.3) -> str:
    """统一 LLM 调用接口，自动路由到当前激活的后端"""
    if settings.weixue_api_base:
        logger.info("使用问学系统 LLM")
        from src.llm.weixue_client import chat_completion
    else:
        logger.info("使用 Azure OpenAI GPT-4o")
        from src.llm.azure_client import chat_completion

    return await chat_completion(messages, temperature)
