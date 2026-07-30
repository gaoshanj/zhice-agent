# AI 开发记忆 (Agent Memory — AI_MEMORY.md)

> **用途**：本文件是 zhice-agent 项目的「可移植开发记忆」，专门写给**未来的 AI 开发工具 / 换电脑后的自己**看。
> 把它当成一个「接手这个项目前必读」的速查本：项目是什么、架构怎么跑、踩过哪些坑、微软课程功能怎么实现、以及怎么本地验证与上线。
> 本文件随仓库提交到 GitHub（`https://github.com/gaoshanj/zhice-agent`），换工具/换电脑 `git clone` 后即可读取。
>
> 最后更新：2026-07-30｜覆盖到 commit `1232621`

---

## 0. 一句话定位

**培训智策 Agent** = 飞书机器人 + RAG(内部知识库+外部爬虫) + LLM 销售策略报告系统。
用户（高老师，微软/AWS/Google 授权培训师）在飞书发「公司名/拜访记录」，Agent 自动检索知识并生成一份结构化《销售策略报告》卡片，其中**第 4 节「微软培训课程方案」**会融入最相关的一门微软官方培训课程（名称/链接/受众/大纲），用于交叉销售培训。

- 仓库：`https://github.com/gaoshanj/zhice-agent`
- 线上：`https://zhice-agent-c5ewakbkcshbfudp.eastasia-01.azurewebsites.net`
- 端点：`/health` · `/webhook/feishu` · `/docs`(Swagger) · `/admin/build-course-index`
- 主分支：`main`｜部署：Azure App Service East Asia + GitHub Actions CI/CD

---

## 1. 技术栈（务必先看，避免版本坑）

| 维度 | 选型 | 注意事项 |
|---|---|---|
| LLM | Azure AI Foundry `gpt-5-nano`（推理模型） | **不支持 `temperature`**；用 `max_completion_tokens`；支持 `reasoning_effort`（全链路用 `"low"` 提速 2.1x）。95% token 被推理消耗，需 token 预算自适应 |
| 语言/框架 | Python 3.13 + FastAPI + httpx + openai | `uvicorn src.main:app` |
| 向量库 | **ChromaDB 持久化**（`/home/data/chroma_data` 线上，本地 `./chroma_data`） | 见 §4 的 ChromaDB 专属坑 |
| Embedding | Azure `text-embedding-3-small` | — |
| 爬虫 | httpx + BeautifulSoup4（Azure 用，无 Playwright）；本地可选 Playwright | `CRAWLER_USE_PLAYWRIGHT=false`（Azure） |
| 课程源 | **Microsoft Learn Catalog API** + `data/course_catalog.xlsx`（125 门） | 单页全量无分页；部分新课 Learn 查无，以 xlsx 为准 |
| 部署 | Azure App Service + GitHub Actions | `REBUILD_INDEX_SECRET` 需在 Portal 与 GitHub Secrets 都配 |

**Azure AI Foundry 关键事实**：
- 资源根地址：`https://<resource>.services.ai.azure.com`（**不要**加 `/api/projects/...`）
- Chat `api_version="2025-04-01-preview"`｜Embedding `api_version="2024-06-01"`

---

## 2. 仓库结构与关键文件

```
src/main.py            # FastAPI 主入口：/health(含 course_docs 计数)、/webhook/feishu、/admin/*
src/bot/               # 飞书机器人：Webhook 处理、消息解析、card_builder(卡片渲染)
src/crawler/           # 爬虫：招聘/官网/新闻 + 调度器 + Bitable 写入
src/llm/               # LLM 客户端(azure_openai)、learn_search(课程)、prompt_templates
src/models/            # CompanyContext 统一信息模型（路线B 重构产物）
src/rag/               # vector_store / retriever(含 course_search) / document_loader / 飞书 Wiki+OAuth
src/report/generator.py# 报告生成：7 节结构，第 4 节 course_plan
src/utils/             # config.py(pydantic-settings) / logger.py
scripts/build_course_index.py  # 课程索引构建（支持 --xlsx）
data/course_catalog.xlsx       # 125 门课源表（必 deploy，否则线上只建 5 门默认课）
tests/                 # pytest 套件（12→17 用例）
```

**报告 7 节顺序**（generator.py `SECTION_NAMES/LABELS`）：
1. 公司概况 → 2. 培训与认证 → 3. 交叉销售机会 → **4. 微软培训课程方案** → 5. 销售策略建议 → 6. 沟通话术 → 7. 行动建议
> 第 4 节刻意跳过内部/外部 RAG 检索，只用 course_search，避免来源编号污染。

---

## 3. 微软培训课程功能（2026-07 新增，本记忆重点）

### 3.1 数据流
```
data/course_catalog.xlsx (125门, 字段优先级最高)
        │  scripts/build_course_index.py --xlsx
        ▼
course_docs (ChromaDB 集合, text-embedding-3-small 向量化)
        │  retriever.course_search(query, top_k=1, candidate_k=8)
        ▼
generator.py 第4节 → prompt_templates.SECTION4_COURSE_PLAN
        │
        ▼
card_builder 飞书卡片「🎓 微软培训课程方案」块（位于交叉销售与销售策略之间）
```
- **表格字段优先**：xlsx 的 Course Number/Title/Duration/Detail Page Url/Solution Area/Credential/State 直接入库；`learn_search.build_course_from_rows` 仅用 Learn API **补充缺失的受众/大纲**。
- **单门输出**：`top_k=1` 避免卡片被多门课截断（曾因 `top_k=2` 截断）。

### 3.2 匹配逻辑（commit `1232621`，用户已验收达标）
`course_search` 排序策略 = **信息完整性优先重排**：
1. 先按语义相似度取 `candidate_k=8` 门候选；
2. 用 `_course_completeness()` 解析每门课文本里的 `## 学员对象详述` 和 `## 课程大纲` 段（占位符「（无）」视为缺失），打 0~2 分；
3. **重排：完整度高的优先，同分再比距离（越近越相关）**，取 1 门输出。
- 完整度在**查询期从已存文本判定，无需重建索引** → 部署零额外成本。
- 验证：真实 125 门数据上，对 7 门已知残缺课（PL-7008/DP-603T00/AZ-2007/AI-3003/AI-3016/DP-3020/MS-4021），重排把 5 门改选为更完整的课（如 `PL-7008`→`MS-4022`），2 门已完整保持不变，无回归。
- 测试：`tests/test_course_search.py`（5 用例，全量 17 passed）。

### 3.3 当前课程库状态（2026-07-30 实测）
- 本地 `course_docs = 125` 条 ✅
- **约 30 门（24%）信息不全**：缺受众和/或大纲（Learn 查无或 xlsx 无此列）。本重排让它们「不优先被推」，但根本改善需补全。
- 之前图片比对 53 门中：**43 完整 / 7 不全 / 3 不在库**（DP-3022、DP-7001、DP-7003 不在 xlsx）。
- 待补清单尚未落库（用户未提供官方链接/受众大纲）。

### 3.4 如何重建/核对课程库
```bash
# 本地构建（xlsx 优先）
.venv/Scripts/python.exe scripts/build_course_index.py --xlsx

# 线上触发（需 REBUILD_INDEX_SECRET）
curl -X POST "https://<app>/admin/build-course-index?secret=$REBUILD_INDEX_SECRET"

# 核对线上是否真有 125 条
curl "https://<app>/health"   # 看 course_docs 字段
```
> ⚠️ **线上 course_docs 没变成 125 的常见原因**：`data/` 被 `.gitignore` 忽略导致 xlsx 没部署 + 触发条件没算 xlsx 行数。修复见 §4。

---

## 4. 踩坑记录（血泪经验，照抄能省几天）

### ChromaDB 专属
1. **`modify` 带 `hnsw:space` 报「距离函数不可改」** → modify 时剔除所有 `hnsw:*` 键，只更新 `schema_version`。（commit `fd8ee25`，本地复现验证过）
2. **持久化索引旧了新代码不生效** → 引入 `_CHROMA_SCHEMA_VERSION` / `_CHROMA_EXTERNAL_SCHEMA_VERSION` 版本标记，不匹配则启动强制重建。
3. **CI/CD 部署后 ChromaDB 内存状态清空**（内部 RAG 失效，chroma_docs=0）→ `main.py` lifespan 加 `_auto_build_on_startup()` + `threading.Lock` 并发保护；App 启动自动检测重建，不依赖外部触发。
4. **Azure 进程回收导致 BackgroundTask 丢失** → 索引重建从「外部 trigger」改为「纯轮询」，逻辑内聚到 App lifespan。

### Git / 部署
5. **`.gitignore` 忽略 `data/` → xlsx 未部署** → `git add -f data/course_catalog.xlsx` 强制纳入；触发条件加「现有条数 != count_xlsx_rows()」行数校验。（commit `08b6d17`）
6. **本地 git push 需** `git push -c http.version=HTTP/1.1`（HTTP/2 在某些网络下握手失败）。
7. **`scripts/__init__.py` 必须存在** 才能 `from scripts.xxx import ...`。

### 报告 / 飞书
8. **飞书卡片字段顺序固定** → `card_builder.build_report_card` 必须**手动插入**「🎓 微软培训课程方案」块到交叉销售与销售策略之间，不能靠自动排序。
9. **课程方案截断** → `course_search` 的 `top_k` 从 2 改 1。
10. **跨公司数据泄漏** → `retriever.external_search()` 必须 `filter_dict={"company": company}`。
11. **来源链接不可点击** → `format_rag_context()` 返回 source_map，`_linkify_sources()` 后处理成 Markdown 链接；`[来源N]` 编号编号到 201+ 来自 CompanyContext。
12. **Lookup 字段 option_id 泄露到报告** → `fetch_option_map()` 新增 Pass 2/3：追踪源表→拉选项→映射到 Lookup 字段名。（commit `dc91f15`）
13. **报告耗时过长（149s）** → `feishu_handler` 漏传 `reasoning_effort`，补 `generate_report(parsed, reasoning_effort="low")`。
14. **🐛 已知未修复**：报告个别**句子被截断**（非课程节，疑似 LLM 输出被 token 上限截断）。尚未立项排查。

### LLM / 爬虫
15. **gpt-5-nano 不支持 temperature**；用 `max_completion_tokens`；`reasoning_effort="low"` 全链路提速。
16. **Bing 中文搜索不要用引号**！引号破坏中文分词。
17. **f-string 内字符串字面量**用与外层不同的引号类型，避免转义地狱。
18. **FastAPI 后台任务**用 `BackgroundTasks.add_task()`，别用 `asyncio.create_task()`。
19. **模块级共享函数必须模块级导入**，不可仅在内部函数里 import。
20. **Azure 域名含区域前缀**：`...eastasia-01.azurewebsites.net`。
21. **URL 存活问题（路线B 重构）** → 新增 `CompanyContext` 数据类在 Python 内存聚合官网/招聘/新闻+URL，不再仅依赖 ChromaDB metadata 传 URL（2 环节替代旧 6 环节）。（commit `b320007`）

---

## 5. 本地验证 & 测试

```bash
cd zhice-agent
.venv/Scripts/python.exe -m pytest tests/ -q        # 期望 17 passed
.venv/Scripts/python.exe -c "from src.rag.vector_store import collection_count, settings; \
  print('course_docs =', collection_count(settings.chroma_collection_course))"   # 期望 125
```
- 虚拟环境：`.venv/`（仓库内）；依赖见 `requirements.txt`（含 `openpyxl>=3.1.0` 解析 xlsx）。
- 改 `course_search` 后务必跑 `tests/test_course_search.py` 防回归。

---

## 6. 上线 Checklist（给未来 AI 的发布 SOP）

1. 本地 `.venv` 跑 pytest 全绿（17 passed）。
2. 确认 `data/course_catalog.xlsx` 已 `git add -f` 纳入（否则线上只建 5 门默认课）。
3. `git commit` → `git push -c http.version=HTTP/1.1`（推到 GitHub `main`）。
4. GitHub Actions 自动部署到 Azure；等启动后 `curl /health` 看 `course_docs` 是否为 125。
5. 飞书发一条真实公司名测试，确认卡片第 4 节有课程且信息完整。

---

## 7. 给未来 AI 的接手提示（必读）

- **改课程匹配逻辑** → 只看 `src/rag/retriever.py` 的 `course_search` + `_course_completeness` + `_field_has_content`，调用方只有 `generator.py`。
- **改报告结构/文案** → `src/report/generator.py`（节定义）+ `src/llm/prompt_templates.py`（SECTION* 提示词）+ `src/bot/card_builder.py`（飞书卡片渲染顺序）。
- **加/改课程** → 编辑 `data/course_catalog.xlsx` 后 `scripts/build_course_index.py --xlsx` 重建，并确认 `data/` 已 `git add -f`。
- **不要**动 ChromaDB 的 `hnsw:*` metadata；**不要**给 gpt-5-nano 传 `temperature`；**不要**在飞书卡片里靠自动排序放第 4 节。
- **遇到「线上没变化」先查**：`.gitignore` 是否漏了资源 + `/health` 计数 + Azure 日志的自动构建触发。
- 完整项目级记忆另见本仓库 `README.md`（覆盖到 Phase 4）与 WorkBuddy 工作区 memory。

---

## 8. 待办 / 开放问题（截至 2026-07-30）

- [ ] 补全约 30 门信息不全课程的受众/大纲（从 Learn 链接或课程表加列入库），可整理 `data/course_incomplete.txt` 追踪。
- [ ] 把 DP-3022 / DP-7001 / DP-7003 补入 xlsx 重新入库（图片比对发现不在库）。
- [ ] 修复报告句子截断（疑似 token 上限，未立项）。
- [ ] 用户需把最新提交 push 到 Azure 使 course_docs=125 在生产生效（本地已验证 125）。
