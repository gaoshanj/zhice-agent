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

    # Azure AI Foundry — Chat（资源根端点）
    # 格式：https://<resource>.services.ai.azure.com
    # 注意：无需 /api/projects/... 后缀，SDK 自动路由到 /openai/deployments/<model>/...
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = "gpt-5-nano"
    azure_openai_api_version: str = "2025-04-01-preview"

    # Azure AI Foundry — Embedding（同一资源根端点，不同 api_version）
    azure_embedding_endpoint: str = ""   # 留空则复用 azure_openai_endpoint
    azure_embedding_deployment: str = "text-embedding-3-small"
    azure_embedding_api_version: str = "2024-06-01"

    # 问学系统（生产 LLM，暂时留空）
    weixue_api_base: str = ""
    weixue_api_key: str = ""
    weixue_model: str = ""

    # 向量数据库
    chroma_persist_dir: str = "./chroma_data"
    chroma_collection_internal: str = "internal_docs"
    chroma_collection_external: str = "external_data"

    # 爬虫（Phase 3）
    crawler_request_delay: float = 2.0       # 请求间隔（秒）
    crawler_max_retries: int = 3             # 最大重试次数
    crawler_data_retention_days: int = 180   # 爬取数据缓存天数
    crawler_timeout: int = 20                # 单次请求超时（秒）
    crawler_job_pages: int = 3               # 招聘数据最多爬取页数
    crawler_use_playwright: bool = False     # Azure 部署时关闭 Playwright（无 GUI）
    # 搜索 API（可选）：使用 Bing Search API 定位官网，留空则用 httpx 直接搜索
    bing_search_api_key: str = ""
    bing_search_endpoint: str = "https://api.bing.microsoft.com/v7.0/search"

    # 服务
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    log_level: str = "INFO"

    # 飞书 OAuth 用户授权（用于以用户身份读取 Wiki）
    feishu_user_refresh_token: str = ""  # 从本地授权脚本获取
    feishu_oauth_redirect_uri: str = "http://localhost:8765/oauth/callback"

    # Bitable 知识库（飞书多维表格）
    feishu_bitable_base_token: str = "CeitbAhJGaHqD1s1EricZp9intf"  # 多维表格 base token
    feishu_bitable_table_id: str = "tblHp4aCxwHDJXKJ"  # 知识库表格 ID
    feishu_bitable_crawl_table_id: str = "tblnZiEhmSl6htGB"  # 爬虫数据存储表 ID

    # 管理接口（定时重建索引等）
    rebuild_index_secret: str = ""  # 用于保护 /admin/rebuild-index


settings = Settings()
