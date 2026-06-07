"""Azure OpenAI 客户端封装（适配推理模型 gpt-5-nano）"""

from openai import AsyncAzureOpenAI
from src.utils.config import settings
from src.utils.logger import logger

# gpt-5-nano 模型特性（推理模型）：
# - 仅支持 max_completion_tokens（不支持 max_tokens）
# - 不支持 temperature 参数
# - reasoning_tokens 计入 completion_tokens，需足够的 token 预算
# - 输出约 3-5% 用于可见文本，95%+ 为推理过程

REASONING_MODEL_TOKENS = 16000  # 默认 max_completion_tokens
REASONING_MODELS = ("gpt-5", "o1", "o3", "o4")


def _is_reasoning_model(model: str) -> bool:
    return any(prefix in model for prefix in REASONING_MODELS)


def get_azure_client() -> AsyncAzureOpenAI:
    """获取 AsyncAzureOpenAI 客户端"""
    return AsyncAzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
    )


async def chat_completion(
    messages: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """调用 Azure OpenAI 生成内容（异步）。

    自动适配推理模型（gpt-5-nano）的参数限制。
    """
    client = get_azure_client()
    model = settings.azure_openai_deployment
    is_reasoning = _is_reasoning_model(model)

    try:
        # 构建通用参数
        params: dict = {
            "model": model,
            "messages": messages,
        }

        if is_reasoning:
            # 推理模型：max_completion_tokens 替代 max_tokens，不支持 temperature
            params["max_completion_tokens"] = max_tokens or REASONING_MODEL_TOKENS
            logger.debug(f"推理模型模式: max_completion_tokens={params['max_completion_tokens']}")
        else:
            params["max_tokens"] = max_tokens or 2000
            if temperature is not None:
                params["temperature"] = temperature

        response = await client.chat.completions.create(**params)
        content = response.choices[0].message.content

        # 记录 token 用量
        usage = response.usage
        if usage:
            logger.debug(
                f"Token: prompt={usage.prompt_tokens}, "
                f"completion={usage.completion_tokens}"
            )

        return content or ""

    except Exception as e:
        logger.error(f"Azure OpenAI 调用失败: {e}", exc_info=True)
        raise


async def get_embedding(text: str) -> list[float]:
    """获取文本向量嵌入（异步）"""
    client = get_azure_client()
    try:
        response = await client.embeddings.create(
            model=settings.azure_embedding_deployment,
            input=text,
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Embedding 生成失败: {e}", exc_info=True)
        raise
