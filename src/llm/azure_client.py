"""Azure OpenAI 客户端封装"""

from openai import AzureOpenAI
from src.utils.config import settings
from src.utils.logger import logger


def get_azure_client() -> AzureOpenAI:
    """获取 Azure OpenAI 客户端"""
    return AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
    )


async def chat_completion(messages: list[dict], temperature: float = 0.3) -> str:
    """调用 GPT-4o 生成内容"""
    client = get_azure_client()
    try:
        response = client.chat.completions.create(
            model=settings.azure_openai_deployment,
            messages=messages,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"Azure OpenAI 调用失败: {e}")
        raise


async def get_embedding(text: str) -> list[float]:
    """获取文本向量嵌入"""
    client = get_azure_client()
    try:
        response = client.embeddings.create(
            model=settings.azure_embedding_deployment,
            input=text,
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Embedding 生成失败: {e}")
        raise
