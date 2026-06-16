# 培训智策 Agent (ZhiCe Agent)

> 基于飞书机器人 + RAG + LLM 的销售策略智能报告系统

## 项目简介

培训智策 Agent 是一个面向 IT 培训销售团队的 AI 辅助工具。销售人员在飞书中输入客户公司名称和拜访目的，Agent 自动从内部知识库（飞书 Bitable）和外部互联网（招聘网站、客户官网、行业资讯）抓取近期数据，经过双路 RAG 检索增强后，由 LLM 生成结构化的《客户培训与交叉销售智策报告》，并通过飞书消息卡片推送给销售人员。

## 核心功能

- **飞书机器人交互**：@机器人触发，按提示词模板输入客户信息
- **智能实体提取**：三层策略（拜访格式 → 公司后缀 → LLM NER），精准识别客户名
- **双路 RAG 检索**：内部 Bitable 知识库 + 外部实时爬虫数据
- **六节智策报告**：客户 360° 快照、近期动态、培训商机扫描、交叉销售机会、实施路径、销售话术
- **飞书卡片推送**：结构化消息卡片，来源可点击溯源
- **全链路健康检测**：12 种触发词覆盖飞书→Azure→LLM 全链路

## 技术栈

| 模块 | 技术选型 |
|------|---------|
| 框架 | Python 3.13 + FastAPI |
| LLM | Azure AI Foundry — gpt-5-nano（推理模型） |
| 向量嵌入 | Azure text-embedding-3-small |
| 向量数据库 | ChromaDB 持久化（双集合：internal_docs + external_docs） |
| 爬虫 | httpx + BeautifulSoup4（Azure）/ Playwright（本地可选） |
| 部署 | Azure App Service East Asia + GitHub Actions CI/CD |
| 飞书 SDK | `lark-oapi` |

## 项目结构

```
zhice-agent/
├── src/
│   ├── bot/              # 飞书机器人接入层
│   │   ├── feishu_handler.py   # Webhook 处理 + 消息路由
│   │   ├── card_builder.py     # 飞书消息卡片构建
│   │   └── message_parser.py   # 用户输入解析 + 实体提取
│   ├── rag/              # RAG 检索引擎
│   │   ├── retriever.py        # 双路检索 + source_map + URL 溯源
│   │   ├── vector_store.py     # ChromaDB 封装（增删查）
│   │   └── feishu_wiki.py      # 飞书 Wiki 访问（备用）
│   ├── crawler/          # 外部数据爬虫
│   │   ├── crawler_dispatcher.py  # 爬虫调度器（返回 CompanyContext）
│   │   ├── web_crawler.py         # 官网爬虫（域名试探 + Bing + 百度）
│   │   ├── job_crawler.py         # 招聘爬虫（Boss直聘 + 猎聘）
│   │   ├── news_crawler.py        # 行业技术新闻爬虫
│   │   ├── llm_verifier.py        # LLM 内容验证（官网确认/职位提取/新闻去噪）
│   │   └── bitable_writer.py      # 爬虫结果回写飞书 Bitable
│   ├── models/           # 数据模型（路线B）
│   │   └── company_context.py     # CompanyContext 统一信息模型
│   ├── llm/              # LLM 调用封装
│   │   ├── azure_client.py        # Azure AI Foundry chat completion
│   │   └── prompt_templates.py    # 六节报告 Prompt 模板
│   ├── report/           # 报告生成器
│   │   └── generator.py           # 六节生成 + 智能重试 + source_map
│   ├── utils/            # 公共工具
│   │   ├── config.py             # 环境变量配置（pydantic-settings）
│   │   └── logger.py             # 结构化日志
│   └── main.py           # FastAPI 应用入口 + lifespan 自愈
├── scripts/              # 运维脚本
│   ├── build_bitable_index.py    # Bitable → ChromaDB 索引构建
│   ├── fetch_bitable.py          # Bitable 数据拉取
│   ├── build_wiki_index.py       # Wiki 索引构建（备用）
│   └── test_e2e_*.py             # 端到端测试脚本
├── tests/                # 单元测试（12 个用例）
├── .github/workflows/    # CI/CD
│   ├── ci.yml                    # 主部署流水线（含索引就绪轮询）
│   ├── build-bitable-index.yml   # 索引重建（手动触发）
│   ├── daily-reindex.yml         # 每日重建（定时）
│   └── weekly-crawl.yml          # 每周全量爬取（定时）
├── requirements.txt
└── .env.example
```

## 开发阶段

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | 飞书机器人 + 固定模板输出（链路打通） | ✅ 完成 |
| Phase 2 | 接入飞书 Bitable RAG（内部知识库） | ✅ 完成 |
| Phase 3 | 接入外部爬虫（招聘 + 官网 + 资讯） | ✅ 完成 |
| Phase 4 | 生产优化 + 路线B 架构重构 | ✅ 完成 |

## Phase 4 生产优化详情（2026-06-11 ~ 2026-06-16）

### 性能优化
- 实体提取三层策略（拜访格式 → 公司后缀 → LLM NER），消除误识别
- LLM 超时保护 90s + 瞬态/非瞬态错误分类 + 智能重试
- 推理模型 token 预算自适应（空输出降级重试）
- `reasoning_effort="low"` 全链路，2.1x 加速（210s → 100s）
- 全链路健康检测（12 个触发词）

### 生产 Bug 修复（10 轮 Wave）
| # | 问题 | 根因 | 修复 |
|---|------|------|------|
| 1 | CI/CD 后 RAG 失效 | ChromaDB 内存状态清空 | App lifespan 自愈：启动时自动检测并重建索引 |
| 2 | 外部来源链接缺失 | source 字段不一致 | source_type 归一化 + URL 字段名兼容 |
| 3 | 报告耗时 150s | 未传 reasoning_effort | feishu_handler 传递 `reasoning_effort="low"` |
| 4 | CI 索引重建不稳定 | Azure 进程回收丢失 BackgroundTask | 外部 trigger → 纯轮询，重建内聚到 App lifespan |
| 5 | 多表未索引 + 来源不可点击 | 第二张表未配置 + URL 丢失 | _TABLE_SCHEMAS + _linkify_sources() |
| 6 | option_id 泄露到报告 | Lookup 字段 type=19 | 三级映射：追踪源表 → 拉取选项 → 替换 ID |
| 7 | 旧索引不更新 | ChromaDB 持久化 + 无版本 | _CHROMA_SCHEMA_VERSION 版本检测 + 强制重建 |
| 8 | 官网爬虫失败 + 招聘未入报告 | 短英文名搜索困难 + fire-and-forget | 域名试探 + 同步等待爬虫 |
| 9 | 来源 URL 缺失 + 公司概况无官网 | metadata 丢 URL + prompt 忽略外部数据 | metadata 补 URL + prompt 新增外部数据指令 |
| 10 | Wave 1-9 治标不治本 | 数据流 6 环节任一断链即丢 URL | **路线B：CompanyContext 重构** |

### 路线B — CompanyContext 统一信息模型（commit `b320007`）

核心变革：将 URL 从 ChromaDB metadata 链路中剥离，存活于 Python 内存。

```
旧链路（6 环节，任一断链即丢 URL）：
爬虫 → ChromaDB metadata → RAG 检索 → format_rag_context → source_map → linkify

新链路（2 环节，URL 100% 传递）：
爬虫 → CompanyContext (Python 内存) → 直接注入 Prompt
```

- **新增** `src/models/company_context.py`：聚合官网/招聘/新闻 + 完整 URL 溯源
- **改造** `crawler_dispatcher.py`：爬取完成后构建并返回 CompanyContext
- **改造** `generator.py`：CompanyContext 作为独立 user message 注入每节 Prompt
- source_map 合并 CompanyContext URL（编号 201+），linkify 生成可点击链接
- 增量改动，不破坏原有 ChromaDB RAG 链路

## 部署详情

| 项目 | 值 |
|------|-----|
| **URL** | https://zhice-agent-c5ewakkbshbfudp.eastasia-01.azurewebsites.net |
| **Webhook** | `/webhook/feishu` |
| **健康检查** | `/health` |
| **API 文档** | `/docs` |
| **启动命令** | `uvicorn src.main:app --host 0.0.0.0 --port $PORT` |
| **ChromaDB** | `/home/data/chroma_data`（部署不覆盖，含版本检测） |
| **双集合** | `internal_docs`（Bitable）+ `external_docs`（爬虫） |

## 环境变量

必需配置（详见 `.env.example`）：

```bash
# 飞书
FEISHU_APP_ID=           # 飞书应用 ID
FEISHU_APP_SECRET=       # 飞书应用密钥
FEISHU_WEBHOOK_VERIFY_TOKEN=  # Webhook 验证 Token

# Azure AI Foundry
AZURE_OPENAI_ENDPOINT=   # https://<resource>.services.ai.azure.com
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_DEPLOYMENT= # gpt-5-nano 部署名
AZURE_EMBEDDING_DEPLOYMENT=  # text-embedding-3-small 部署名

# Bitable
FEISHU_BITABLE_BASE_TOKEN=   # CeitbAhJGaHqD1s1EricZp9intf（示例）
FEISHU_BITABLE_TABLE_ID=     # tblHp4aCxwHDJXKJ（示例）
FEISHU_BITABLE_TABLES=       # JSON 数组，多表配置（优先级高于单表）

# 爬虫
BING_SEARCH_API_KEY=     # Bing Search API Key（可选但推荐）
CRAWLER_USE_PLAYWRIGHT=false  # Azure 部署设为 false

# 安全
REBUILD_INDEX_SECRET=    # 索引重建密钥（GitHub Secrets + Azure App Settings 同步配置）
```

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/gaoshanj/zhice-agent.git
cd zhice-agent

# 2. 创建虚拟环境并安装依赖
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，填入各 API Key

# 4. 构建 Bitable 索引
python -m scripts.build_bitable_index

# 5. 启动服务
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

## 已知注意事项

- Azure 域名含区域前缀：`...eastasia-01.azurewebsites.net`
- gpt-5-nano 不支持 `temperature`，用 `max_completion_tokens`；95% token 被推理消耗需预算自适应
- Azure AI Foundry：资源根 `https://<resource>.services.ai.azure.com`，不加 `/api/projects/...`
- Chat `api_version=2025-04-01-preview`，Embedding `api_version=2024-06-01`
- f-string 内字符串字面量用与外层不同的引号类型
- FastAPI 后台任务用 `BackgroundTasks.add_task()`，不用 `asyncio.create_task()`
- `scripts/__init__.py` 必须存在才能 `from scripts.xxx import ...`
- 模块级共享函数必须模块级导入，不可仅在内部函数中导入
- Bing 中文搜索不要用引号 — 引号破坏中文分词
- 本地 git push 需 `-c http.version=HTTP/1.1`

## 测试

```bash
pytest tests/ -v
```

- 12/12 单元测试全部通过，零回归
- 报告端到端耗时 ~100s（reasoning_effort=low）

## 许可证

内部项目，仅限公司内部使用。
