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

import httpx
from openai import AsyncAzureOpenAI, AzureOpenAI
from openai import (
    AuthenticationError,
    NotFoundError,
    BadRequestError,
    RateLimitError,
    APITimeoutError,
    APIConnectionError,
    InternalServerError,
)
from src.utils.config import settings
from src.utils.logger import logger

REASONING_MODEL_TOKENS = 16000  # 默认 max_completion_tokens
REASONING_MIN_EFFECTIVE = 10000  # 推理模型最小有效输出阈值（低于此值可能返回空内容）
REASONING_MODELS = ("gpt-5", "o1", "o3", "o4")


def _is_reasoning_model(model: str) -> bool:
    return any(prefix in model for prefix in REASONING_MODELS)


# 公开别名，供外部模块（如 generator）使用
is_reasoning_model = _is_reasoning_model


def _get_endpoint(preferred: str, fallback: str) -> str:
    """返回去掉尾斜杠的端点，优先使用显式配置值"""
    ep = (preferred or fallback).rstrip("/")
    return ep


def get_chat_client() -> AsyncAzureOpenAI:
    """获取 Chat（LLM）用的 AsyncAzureOpenAI 客户端"""
    endpoint = _get_endpoint(settings.azure_openai_endpoint, "")
    # 显式设置 HTTP 超时，避免模型端点不可达时挂起 10 分钟
    http_client = httpx.AsyncClient(timeout=90.0)
    return AsyncAzureOpenAI(
        azure_endpoint=endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        http_client=http_client,
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
    推理模型关键行为：~95% completion_tokens 用于推理，仅 ~5% 为可见输出。
    若 max_completion_tokens 过低（如 4000），推理将耗尽所有预算，
    导致 content 为 None/空字符串。
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
            requested = max_tokens or REASONING_MODEL_TOKENS
            # 推理模型需要足够的 token 预算才能产生可见输出
            # 低于 REASONING_MIN_EFFECTIVE 时自动提升到安全值
            if requested < REASONING_MIN_EFFECTIVE:
                logger.info(
                    f"推理模型: max_completion_tokens {requested} → {REASONING_MIN_EFFECTIVE} "
                    f"(低于有效输出阈值)"
                )
                requested = REASONING_MIN_EFFECTIVE
            params["max_completion_tokens"] = requested
        else:
            params["max_tokens"] = max_tokens or 2000
            if temperature is not None:
                params["temperature"] = temperature

        response = await client.chat.completions.create(**params)
        content = response.choices[0].message.content
        finish_reason = response.choices[0].finish_reason

        usage = response.usage
        if usage:
            logger.debug(
                f"Token: prompt={usage.prompt_tokens}, "
                f"completion={usage.completion_tokens}, "
                f"finish_reason={finish_reason}"
            )

        # 推理模型返回空内容的常见原因：token 预算不足
        if not content and is_reasoning:
            logger.warning(
                f"推理模型返回空内容！finish_reason={finish_reason}, "
                f"completion_tokens={usage.completion_tokens if usage else 'N/A'}。"
                f"考虑增加 max_completion_tokens。"
            )

        return content or ""

    except Exception as e:
        logger.error(f"Azure AI Foundry Chat 调用失败: {e}", exc_info=True)
        raise


# ── 错误分类工具（供 generator 等调用方做重试决策）──────────────

RETRYABLE_ERRORS = (
    RateLimitError,
    APITimeoutError,
    APIConnectionError,
    InternalServerError,
)
NON_RETRYABLE_ERRORS = (
    AuthenticationError,
    NotFoundError,
    BadRequestError,
)


def is_retryable_error(exc: Exception) -> bool:
    """判断异常是否属于可重试的瞬态错误。"""
    return isinstance(exc, RETRYABLE_ERRORS)


def classify_llm_error(exc: Exception) -> tuple[str, bool]:
    """分类 LLM 调用异常，返回 (人类可读原因, 是否可重试)。

    Examples:
        >>> classify_llm_error(APITimeoutError("..."))
        ("模型服务响应超时", True)
        >>> classify_llm_error(AuthenticationError("..."))
        ("API Key 认证失败，请检查 Azure 配置", False)
    """
    if isinstance(exc, AuthenticationError):
        return "API Key 认证失败，请检查 Azure 配置", False
    if isinstance(exc, NotFoundError):
        return "模型部署不存在（请确认 deployment 名称正确）", False
    if isinstance(exc, BadRequestError):
        return f"请求参数错误: {exc}", False
    if isinstance(exc, RateLimitError):
        return "模型服务限流，请稍后再试", True
    if isinstance(exc, APITimeoutError):
        return "模型服务响应超时（Azure 端点可能暂时不可用）", True
    if isinstance(exc, APIConnectionError):
        return "网络连接失败，无法访问 Azure 端点", True
    if isinstance(exc, InternalServerError):
        return "Azure 服务端内部错误", True
    # 兜底：未知异常，保守重试一次
    return f"未知错误: {exc}", True


def _get_sync_embedding_client() -> AzureOpenAI:
    """获取 Embedding 用的同步 AzureOpenAI 客户端"""
    endpoint = _get_endpoint(
        settings.azure_embedding_endpoint,
        settings.azure_openai_endpoint,
    )
    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_embedding_api_version,
    )


def get_embedding(text: str) -> list[float]:
    """获取文本向量嵌入（同步，供 ChromaDB 等同步调用方使用）

    api_version=2024-06-01，与 chat 共用同一资源根端点。
    """
    client = _get_sync_embedding_client()
    try:
        response = client.embeddings.create(
            model=settings.azure_embedding_deployment,
            input=text,
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Embedding 生成失败: {e}", exc_info=True)
        raise
