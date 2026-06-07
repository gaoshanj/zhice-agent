# 培训智策 Agent (ZhiCe Agent)

> 基于飞书机器人 + RAG + LLM 的销售策略智能报告系统

## 项目简介

培训智策 Agent 是一个面向 IT 培训销售团队的 AI 辅助工具。销售人员在飞书中输入客户公司名称和拜访目的，Agent 自动从内部知识库（飞书 Wiki）和外部互联网（招聘网站、客户官网、行业资讯）抓取近 6 个月的数据，经过 RAG 检索增强后，由 LLM 生成结构化的《客户培训与交叉销售智策报告》，并通过飞书消息卡片推送给销售人员。

## 核心功能

- **飞书机器人交互**：@机器人触发，按提示词模板输入客户信息
- **双路 RAG 检索**：内部飞书 Wiki 知识库 + 外部实时抓取数据
- **智能报告生成**：客户 360° 快照、培训商机扫描、交叉销售机会、销售话术
- **飞书卡片推送**：结构化消息卡片，支持分节折叠

## 技术栈

| 模块 | 技术选型 |
|------|---------|
| 飞书机器人 | Python `lark-oapi` SDK |
| LLM（测试阶段） | Azure AI Foundry — GPT-4o |
| LLM（生产阶段） | 公司内部问学系统 API |
| 向量嵌入 | `text-embedding-3-small` / `bge-m3` |
| 向量数据库 | Chroma（本地）/ Weaviate（云端） |
| 爬虫框架 | Playwright + Scrapy |
| 数据调度 | APScheduler |
| 部署 | Azure VM + Docker Compose |

## 项目结构

```
zhice-agent/
├── src/
│   ├── bot/          # 飞书机器人接入层
│   ├── rag/          # RAG 检索引擎
│   ├── crawler/      # 外部数据爬虫
│   ├── llm/          # LLM 调用封装
│   ├── report/       # 报告生成器
│   └── utils/        # 公共工具
├── tests/            # 单元测试 & 集成测试
├── scripts/          # 数据初始化 & 运维脚本
├── deploy/           # Docker & Azure 部署配置
├── docs/             # 设计文档
└── .env.example      # 环境变量模板
```

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/<your-org>/zhice-agent.git
cd zhice-agent

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 API Key 等配置

# 4. 初始化向量数据库
python scripts/init_vectordb.py

# 5. 启动服务
docker-compose up -d
```

## 开发阶段规划

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | 飞书机器人 + 固定模板输出（链路打通） | 🔄 进行中 |
| Phase 2 | 接入飞书 Wiki RAG（内部知识库） | 待开始 |
| Phase 3 | 接入外部爬虫（招聘 + 官网 + 资讯） | 待开始 |
| Phase 4 | 切换内部问学 LLM + 生产优化 | 待开始 |

## 环境要求

- Python 3.11+
- Docker & Docker Compose
- Azure 订阅（用于 AI Foundry 和 VM 部署）

## 贡献指南

详见 [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)

## 许可证

内部项目，仅限公司内部使用。
