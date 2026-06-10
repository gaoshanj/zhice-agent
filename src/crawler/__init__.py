"""外部数据爬虫包

Phase 3 新增：
- BaseCrawler: 通用爬虫基类（httpx/Playwright 双模式）
- JobCrawler: 招聘数据爬虫（BOSS直聘/搜索引擎）
- WebCrawler: 公司官网爬虫
- crawl_and_store: 统一调度入口（写入 external_docs ChromaDB 集合）
"""
