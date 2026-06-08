"""Azure AI Foundry 客户端封装

端点说明（Foundry 资源端点，非经典 Azure OpenAI）：
- 两者共用同一资源根端点：https://<resource>.services.ai.azure.com
- Chat / LLM：api_version = 2025-04-01-preview
- Embedding：  api_version = 2024-06-01
- 注意：不需要 /api/projects/... 后缀，SDK 自动构建路径

gpt-5-nano 推理模型特性：
- 仅支持 max_completion_tokens（不支持 max_tokens）
- 不支持 temperature 参数
- reasoning_tokens 计入 completion_tokens，需足够的 token 预算
- 输出约 3-5% 用于可见文本，95%+ 为推理过程
"""

from __future__ import annotations

from openai import AsyncAzureOpenAI
from src.utils.config import settings
from src.utils.logger import logger

REASONING_MODEL_TOKENS = 16000  # 默认 max_completion_tokens
REASONING_MODELS = ("gpt-5", "o1", "o3", "o4")


def _is_reasoning_model(model: str) -> bool:
    return any(prefix in model for prefix in REASONING_MODELS)


def _get_endpoint(preferred: str, fallback: str) -> str:
    """返回去掉尾斜杠的端点，优先使用显式配置值"""
    ep = (preferred or fallback).rstrip("/")
    return ep


def get_chat_client() -> AsyncAzureOpenAI:
    """获取 Chat（LLM）用的 AsyncAzureOpenAI 客户端"""
    endpoint = _get_endpoint(settings.azure_openai_endpoint, "")
    return AsyncAzureOpenAI(
        azure_endpoint=endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
    )


def get_embedding_client() -> AsyncAzureOpenAI:
    """获取 Embedding 用的 AsyncAzureOpenAI 客户端（同根端点，不同 api_version）"""
    endpoint = _get_endpoint(
        settings.azure_embedding_endpoint,
        settings.azure_openai_endpoint,
    )
    return AsyncAzureOpenAI(
        azure_endpoint=endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_embedding_api_version,
    )


# 向后兼容：保留旧名称
def get_azure_client() -> AsyncAzureOpenAI:
    return get_chat_client()


async def chat_completion(
    messages: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """调用 Azure AI Foundry Chat 接口生成内容（异步）。

    自动适配推理模型（gpt-5-nano）的参数限制。
    """
    client = get_chat_client()
    model = settings.azure_openai_deployment
    is_reasoning = _is_reasoning_model(model)

    try:
        params: dict = {
            "model": model,
            "messages": messages,
        }

        if is_reasoning:
            # 推理模型：用 max_completion_tokens，不传 temperature
            params["max_completion_tokens"] = max_tokens or REASONING_MODEL_TOKENS
            logger.debug(f"推理模型模式: max_completion_tokens={params['max_completion_tokens']}")
        else:
            params["max_tokens"] = max_tokens or 2000
            if temperature is not None:
                params["temperature"] = temperature

        response = await client.chat.completions.create(**params)
        content = response.choices[0].message.content

        usage = response.usage
        if usage:
            logger.debug(
                f"Token: prompt={usage.prompt_tokens}, "
                f"completion={usage.completion_tokens}"
            )

        return content or ""

    except Exception as e:
        logger.error(f"Azure AI Foundry Chat 调用失败: {e}", exc_info=True)
        raise


async def get_embedding(text: str) -> list[float]:
    """获取文本向量嵌入（同资源根端点，api_version=2024-06-01）"""
    client = get_embedding_client()
    try:
        response = await client.embeddings.create(
            model=settings.azure_embedding_deployment,
            input=text,
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Embedding 生成失败: {e}", exc_info=True)
        raise
