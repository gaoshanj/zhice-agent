# 培训智策 Agent — 项目规格文档

**版本**：v0.1  
**日期**：2026-06-07  
**状态**：草稿，待确认

---

## 1. 项目背景与目标

### 1.1 背景

IT 培训销售团队在拜访客户前需要花费大量时间手动查询：客户招聘动态、官网新闻、历史合作记录、适配的培训方案等。这些信息分散在招聘网站、客户官网、飞书知识库和 CRM 系统中，整理耗时且质量参差不齐。

### 1.2 目标

构建一个"培训智策 Agent"，让销售人员通过飞书机器人输入客户公司名称和拜访目的，系统自动生成结构化销售策略报告，显著降低拜访前准备时间，提升商机挖掘质量。

### 1.3 核心价值

- **效率**：报告生成时间从 2-3 小时缩短至 < 3 分钟
- **质量**：基于真实数据，避免遗漏关键信号（如客户正在大量招聘 AI 工程师）
- **一致性**：统一的报告模板，便于团队复盘和知识沉淀

---

## 2. 功能需求

### 2.1 飞书机器人交互

**触发方式**：在飞书群或私信中 `@培训智策Agent` 发送指令

**输入格式**：
```
@培训智策Agent
请帮我生成针对以下客户的销售策略报告：
- 客户公司：[公司全称]
- 拜访对象部门/职位：[如：技术研发部/CTO]
- 当前已知信息（选填）：[...]
- 本次拜访主要目的：[如：首次接触/挖掘培训需求]
- 期望侧重方向（多选）：微软Agent培训 / AWS培训 / MSP / 安服 / MA
- 特别要求（选填）：[...]
```

**输出格式**：飞书消息卡片（Card），分节展示：
1. 客户 360° 快照
2. 培训商机深度扫描（微软 Agent / AWS）
3. 交叉销售机会挖掘
4. 销售策略建议
5. 推荐销售话术
6. 行动建议

### 2.2 数据采集

#### 内部数据（飞书知识库）

| 数据类型 | 飞书来源 | 更新策略 |
|---------|---------|---------|
| 客户用户画像 | 飞书多维表格 | 每日同步 |
| 客户培训记录 | 飞书多维表格 | 每日同步 |
| 客户拜访记录 | 飞书多维表格 | 每日同步 |
| 微软 Agent 培训方案 & 大纲 | 飞书 Wiki | 变更时触发 |
| AWS 培训方案 & 大纲 | 飞书 Wiki | 变更时触发 |

#### 外部数据（爬虫抓取，近 6 个月）

| 数据类型 | 数据源 | 更新策略 |
|---------|---------|---------|
| 招聘信息 | Boss 直聘、智联招聘、猎聘、前程无忧 | 每日 02:00 |
| 官网重大事件 | 客户官网 News/Press/About 页 | 每周一次 |
| 招标公告 | 客户官网采购栏目 | 每周一次 |
| 行业资讯 | 36kr、钛媒体等（关键词过滤） | 每日 03:00 |

### 2.3 RAG 检索引擎

- 分块策略：Wiki 文档按章节（H2/H3）分块；招聘信息按岗位分块；新闻按文章分块
- 向量模型：`text-embedding-3-small`（测试阶段），后续迁移至问学系统 embedding 接口
- 向量数据库：Chroma（开发阶段），Weaviate（生产阶段）
- 检索策略：Hybrid（语义向量 + BM25），Top-10 召回 + Cross-Encoder 重排序
- 时间过滤：外部数据默认仅检索近 6 个月内容

### 2.4 报告生成

- 输出按固定模板逐节生成（详见 `docs/report-template.md`）
- LLM 测试阶段：Azure AI Foundry — GPT-4o（`gpt-4o-2024-11-20`）
- LLM 生产阶段：公司内部问学系统 OpenAI 兼容 API
- 支持异步流式输出，飞书卡片动态刷新

---

## 3. 技术架构

### 3.1 系统组件

```
[飞书机器人] ──接收消息──▶ [消息路由层]
                                  │
                                  ▼
                         [LLM 编排层（Python）]
                          ├── 意图解析
                          ├── 任务拆分
                          └── Prompt 管理
                                  │
                         ┌────────┴────────┐
                         ▼                 ▼
                  [内部 RAG]         [外部 RAG]
                  飞书 Wiki           爬虫数据
                  客户历史            招聘/官网/资讯
                         └────────┬────────┘
                                  ▼
                         [向量数据库 Chroma]
                                  │
                                  ▼
                         [报告生成器]
                         按模板逐节填充
                                  │
                                  ▼
                         [飞书卡片推送]
```

### 3.2 技术选型明细

| 层次 | 组件 | 版本/规格 |
|------|------|---------|
| 语言 | Python | 3.11+ |
| 飞书 SDK | lark-oapi | latest |
| LLM（测试） | Azure OpenAI GPT-4o | gpt-4o-2024-11-20 |
| LLM（生产） | 问学系统 OpenAI 兼容 API | TBD |
| 嵌入模型 | text-embedding-3-small | 1536 维 |
| 向量数据库 | chromadb | latest |
| 爬虫 | playwright + beautifulsoup4 | latest |
| 任务调度 | APScheduler | 3.x |
| Web 框架 | FastAPI | 0.115+ |
| 容器化 | Docker + Docker Compose | - |
| 云平台 | Azure VM (B2s 起步) | - |
| CI/CD | GitHub Actions | - |

### 3.3 目录结构

```
zhice-agent/
├── src/
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── feishu_handler.py    # 飞书 Webhook 处理
│   │   ├── message_parser.py    # 消息解析 & 意图提取
│   │   └── card_builder.py      # 飞书卡片构建
│   ├── rag/
│   │   ├── __init__.py
│   │   ├── embedder.py          # 文本向量化
│   │   ├── vectordb.py          # Chroma 操作封装
│   │   ├── retriever.py         # 混合检索
│   │   └── reranker.py          # 重排序
│   ├── crawler/
│   │   ├── __init__.py
│   │   ├── base_crawler.py      # 爬虫基类
│   │   ├── boss_crawler.py      # Boss 直聘
│   │   ├── zhilian_crawler.py   # 智联招聘
★   │   ├── official_site_crawler.py  # 客户官网
│   │   └── scheduler.py         # 定时任务
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── azure_client.py      # Azure OpenAI 客户端
│   │   ├── weixue_client.py     # 问学系统客户端（占位）
│   │   └── prompt_templates.py  # Prompt 模板
│   ├── report/
│   │   ├── __init__.py
│   │   ├── generator.py         # 报告生成主逻辑
│   │   └── sections/            # 各报告节生成器
│   └── utils/
│       ├── config.py            # 配置加载
│       ├── logger.py            # 日志
│       └── feishu_wiki_sync.py  # 飞书 Wiki 同步
├── tests/
├── scripts/
│   ├── init_vectordb.py         # 向量库初始化
│   └── sync_feishu_wiki.py      # 手动触发 Wiki 同步
├── deploy/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── azure/
│       └── vm-setup.sh
├── docs/
│   ├── report-template.md       # 报告输出模板
│   └── api-design.md            # 内部 API 设计
├── .env.example
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## 4. 安全与合规

| 风险 | 应对措施 |
|------|---------|
| 客户数据泄露 | 内部数据入库前脱敏；LLM 调用通过 Azure 私有端点 |
| 爬虫封禁 | 随机 UA、请求间隔、失败重试、代理池（可选） |
| API Key 泄露 | 使用 Azure Key Vault 管理密钥，不允许明文写入代码 |
| 飞书权限 | 申请最小权限范围，Wiki 读取需逐文档授权 |

---

## 5. 开发阶段计划

### Phase 1 — 链路打通（预计 2 周）
- [ ] 飞书机器人接入（Webhook 收发）
- [ ] 消息解析 & 意图提取
- [ ] Azure OpenAI 接入（GPT-4o）
- [ ] 固定模板报告生成（无 RAG）
- [ ] 飞书卡片推送

### Phase 2 — 内部 RAG（预计 1 周）
- [ ] 飞书 Wiki 文档同步
- [ ] 向量化 & Chroma 入库
- [ ] RAG 检索接入报告生成

### Phase 3 — 外部爬虫（预计 2 周）
- [ ] Boss / 智联 / 猎聘爬虫
- [ ] 客户官网爬虫
- [ ] 定时调度器
- [ ] 外部数据向量化入库

### Phase 4 — 生产优化（持续）
- [ ] 切换问学系统 LLM
- [ ] Azure VM 部署
- [ ] 性能优化 & 监控
- [ ] 用户反馈迭代

---

## 6. 待确认事项

- [ ] 飞书企业应用 AppID / AppSecret（需管理员创建）
- [ ] Azure AI Foundry 订阅信息和 Endpoint
- [ ] 问学系统 API 接口文档（生产 LLM）
- [ ] 飞书 Wiki 文档 Token 列表（需要同步的具体文档）
- [ ] 内部客户数据字段规范（多维表格结构）
