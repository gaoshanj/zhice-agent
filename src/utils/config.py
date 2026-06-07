"""全局配置管理（基于 pydantic-settings）"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # 飞书
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_encrypt_key: str = ""
    feishu_verification_token: str = ""
    feishu_wiki_space_id: str = ""  # Wiki 空间 ID（Phase 2）

    # Azure OpenAI
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = "gpt-4o-2024-11-20"
    azure_openai_api_version: str = "2024-08-01-preview"
    azure_embedding_deployment: str = "text-embedding-3-small"

    # 问学系统（生产 LLM，暂时留空）
    weixue_api_base: str = ""
    weixue_api_key: str = ""
    weixue_model: str = ""

    # 向量数据库
    chroma_persist_dir: str = "./chroma_data"
    chroma_collection_internal: str = "internal_docs"
    chroma_collection_external: str = "external_data"

    # 爬虫
    crawler_request_delay: float = 2.0
    crawler_max_retries: int = 3
    crawler_data_retention_days: int = 180

    # 服务
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    log_level: str = "INFO"

    # 管理接口（定时重建索引等）
    rebuild_index_secret: str = ""  # 用于保护 /admin/rebuild-index


settings = Settings()
