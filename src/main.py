"""培训智策 Agent — Phase 2 主入口"""

# ── ChromaDB SQLite 兼容性修复（Azure App Service 系统 sqlite3 过旧）──
__import__("pysqlite3")
import sys
sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")

import asyncio
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

import dotenv
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.bot.feishu_handler import router as feishu_router
from src.rag.vector_store import _get_collection, collection_count
from src.utils.config import settings
from src.utils.logger import logger

# 索引 schema 版本：修改 build_bitable_index.py 后递增此值
# 用于 _auto_build_on_startup 检测是否需要强制重建
_CHROMA_SCHEMA_VERSION = 4  # 5e0a53f+: 外部集合 URL schema 版本对齐

# 外部数据集合 schema 版本：当 job_crawler/news_crawler 的 chunk 格式变化时递增
# 用于 _auto_build_on_startup 检测是否需要清空外部集合并重新爬取
_CHROMA_EXTERNAL_SCHEMA_VERSION = 1  # Wave 9: external_jobs/external_news chunks now include source URLs

# 根目录 .env 加载（本地开发用；App Service 从环境变量读取）
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(env_path):
    dotenv.load_dotenv(env_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("=" * 60)
    logger.info("🚀 培训智策 Agent v0.2 启动")
    logger.info(f"   环境: {settings.log_level}")
    logger.info(f"   飞书 App ID: {settings.feishu_app_id or '未配置'}")
    logger.info(f"   Azure Endpoint: {'已配置' if settings.azure_openai_endpoint else '未配置'}")
    logger.info(f"   Azure Chat: {settings.azure_openai_deployment}")
    logger.info(f"   Azure Embedding: {settings.azure_embedding_deployment}")
    logger.info(f"   Wiki Space ID: {settings.feishu_wiki_space_id or '未配置'}")
    logger.info(f"   Bitable: {'已配置' if settings.feishu_bitable_base_token else '未配置'}")
    logger.info(f"   OAuth 用户授权: {'已配置' if settings.feishu_user_refresh_token else '未配置'}")
    logger.info(f"   ChromaDB: {settings.chroma_persist_dir}")
    logger.info("=" * 60)

    # ── 启动时自动检测并重建 Bitable RAG 索引 ──
    # 每次部署后 ChromaDB 内存状态清空，需要重新构建。
    # 延迟 30s 执行，确保 Azure App Service 完全就绪。
    _auto_build_on_startup()

    yield
    logger.info("培训智策 Agent 已停止")


app = FastAPI(
    title="培训智策 Agent",
    version="0.2.0",
    description="基于飞书机器人 + RAG + LLM 的销售策略报告系统",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由注册
app.include_router(feishu_router, prefix="/webhook")


@app.get("/", tags=["Health"])
async def root():
    return {
        "service": "培训智策 Agent",
        "version": "0.2.0",
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"])
async def health():
    return {
        "status": "ok",
        "version": "0.2.0",
        "feishu_configured": bool(settings.feishu_app_id),
        "azure_configured": bool(settings.azure_openai_endpoint),
        "wiki_configured": bool(settings.feishu_wiki_space_id),
        "bitable_configured": bool(settings.feishu_bitable_base_token or settings.feishu_bitable_tables),
        "oauth_configured": bool(settings.feishu_user_refresh_token),
        "embedding_configured": bool(settings.azure_embedding_deployment),
        "chroma_docs":          _chroma_status(),
        "chroma_docs_external": _chroma_status(collection_name=settings.chroma_collection_external),
        "chroma_persist_dir":  settings.chroma_persist_dir,
        "bitable_build_state": _bitable_build_state.get("status"),
    }


def _chroma_status(collection_name: str = "") -> int:
    """获取 ChromaDB 文档数（安全获取，不抛异常）

    Args:
        collection_name: 集合名称，空字符串表示默认（internal_docs）
    """
    try:
        from src.rag.vector_store import collection_count
        return collection_count(collection_name=collection_name)
    except Exception:
        return -1


@app.post("/admin/rebuild-index", tags=["Admin"])
async def rebuild_index(secret: str = Query(..., description="验证密钥")):
    """定时重建 Wiki 索引（受密钥保护）

    GitHub Actions 每天定时调用此端点，重新从飞书 Wiki
    拉取文档 → 分块 → embedding → 写入 ChromaDB。
    """
    # 密钥校验
    if not settings.rebuild_index_secret:
        raise HTTPException(status_code=501, detail="管理员未配置 REBUILD_INDEX_SECRET")
    if secret != settings.rebuild_index_secret:
        raise HTTPException(status_code=403, detail="密钥错误")

    logger.info("📥 收到定时重建索引请求")

    try:
        from scripts.build_wiki_index import build_index

        space_id = settings.feishu_wiki_space_id
        if not space_id:
            raise HTTPException(status_code=400, detail="未配置 FEISHU_WIKI_SPACE_ID")

        # 在后台执行（不阻塞响应），返回 202 Accepted
        import asyncio
        asyncio.create_task(_run_rebuild(space_id))
        return JSONResponse(
            status_code=202,
            content={"status": "accepted", "message": f"开始重建索引，空间: {space_id}"},
        )

    except Exception as e:
        logger.error(f"重建索引启动失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def _run_rebuild(space_id: str):
    """后台异步执行索引重建"""
    from scripts.build_wiki_index import build_index
    import time

    start = time.time()
    logger.info(f"🔄 定时索引重建开始，空间: {space_id}")
    try:
        build_index(space_id, rebuild=True)
        elapsed = time.time() - start
        logger.info(f"✅ 定时索引重建完成（耗时 {elapsed:.1f}s）")
    except BaseException as e:
        elapsed = time.time() - start
        logger.error(f"❌ 定时索引重建失败（耗时 {elapsed:.1f}s）: {e}", exc_info=True)


@app.post("/admin/build-bitable-index", tags=["Admin"])
async def build_bitable_index(
    secret: str = Query(..., description="验证密钥"),
    background_tasks: BackgroundTasks = None,
):
    """构建 Bitable 知识库 RAG 索引（受密钥保护）

    从飞书多维表格拉取客户商机数据 → 生成 embedding → 写入 ChromaDB。
    GitHub Actions 或手动 curl 调用此端点。
    """
    if not settings.rebuild_index_secret:
        raise HTTPException(status_code=501, detail="管理员未配置 REBUILD_INDEX_SECRET")
    if secret != settings.rebuild_index_secret:
        raise HTTPException(status_code=403, detail="密钥错误")

    logger.info("📥 收到 Bitable 索引构建请求")

    # 使用 BackgroundTasks 确保后台任务可靠执行
    if background_tasks is None:
        # fallback：直接运行（非生产推荐，仅用于调试）
        logger.warning("⚠️ 无 BackgroundTasks，直接运行构建")
        _bitable_build_state["status"] = "building"
        _bitable_build_state["started_at"] = datetime.now(timezone.utc).isoformat()
        try:
            from scripts.build_bitable_index import build_index
            build_index(True)
            _bitable_build_state["status"] = "success"
            _bitable_build_state["doc_count"] = collection_count()
        except BaseException as e:
            _bitable_build_state["status"] = "failed"
            _bitable_build_state["error"] = f"{type(e).__name__}: {str(e)[:800]}"
            logger.error(f"❌ Bitable 同步构建失败: {e}", exc_info=True)
        finally:
            _bitable_build_state["finished_at"] = datetime.now(timezone.utc).isoformat()
        return JSONResponse(
            status_code=200,
            content={"status": "done", "build_state": _bitable_build_state},
        )

    # 将状态提前设为 building，确保健康检查立即可见
    _bitable_build_state["status"] = "building"
    _bitable_build_state["started_at"] = datetime.now(timezone.utc).isoformat()
    background_tasks.add_task(_run_bitable_build)
    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "message": "开始构建 Bitable 索引（后台任务）"},
    )


# ─── 索引构建状态追踪 ──────────────────────────────────────
_bitable_build_state: dict[str, Any] = {
    "status": "idle",  # idle | building | success | failed
    "started_at": None,
    "finished_at": None,
    "error": None,
    "doc_count": 0,
}

# 构建锁：防止并发运行多个 build_index
_build_lock = threading.Lock()

# ─── 批量爬取状态追踪 ──────────────────────────────────────
_batch_crawl_state: dict[str, Any] = {
    "status": "idle",  # idle | running | completed | failed
    "started_at": None,
    "finished_at": None,
    "error": None,
    "progress": "",  # "5/26 家公司已处理"
    "summary": {},
}


def _run_bitable_build():
    """后台同步执行 Bitable 索引构建

    作为 BackgroundTasks 的 sync 任务在线程池中运行，
    避免 async/await + run_in_executor 双层调度带来的
    启动延迟或静默失败风险。
    """
    # 并发保护：只允许一个构建任务运行
    if not _build_lock.acquire(blocking=False):
        logger.warning("⚠️ 已有索引构建任务运行中，跳过本次构建")
        return

    try:
        # state 已在调用方设为 building，这里只更新 started_at
        _bitable_build_state["started_at"] = datetime.now(timezone.utc).isoformat()
        _bitable_build_state["error"] = None
        _bitable_build_state["doc_count"] = 0

        logger.info("🔄 Bitable 索引构建开始...")
        logger.info(f"   ChromaDB 持久化目录: {settings.chroma_persist_dir}")
        logger.info(f"   Bitable Base: {settings.feishu_bitable_base_token[:8]}...")
        logger.info(f"   Table ID: {settings.feishu_bitable_table_id}")

        # 导入脚本（可能失败，提前捕获）
        try:
            from scripts.build_bitable_index import build_index
        except BaseException as e:
            _bitable_build_state["status"] = "failed"
            _bitable_build_state["error"] = f"导入失败 {type(e).__name__}: {str(e)[:800]}"
            _bitable_build_state["finished_at"] = datetime.now(timezone.utc).isoformat()
            logger.error(f"❌ build_bitable_index 导入失败: {e}", exc_info=True)
            return

        start = time.time()
        try:
            build_index(True)
            elapsed = time.time() - start
            doc_count = collection_count()
            _bitable_build_state["status"] = "success"
            _bitable_build_state["doc_count"] = doc_count
            # 写入 schema 版本标记，下次启动时用于判断是否需要重建
            try:
                coll = _get_collection(settings.chroma_collection_internal)
                coll.modify(metadata={
                    **(coll.metadata if coll.metadata else {}),
                    "schema_version": str(_CHROMA_SCHEMA_VERSION),
                    "hnsw:space": "cosine",  # 保留原有配置
                })
            except Exception as ve:
                logger.warning(f"写入 schema_version 失败: {ve}")
            logger.info(f"✅ Bitable 索引构建完成（耗时 {elapsed:.1f}s，共 {doc_count} 条）")
        except BaseException as e:
            elapsed = time.time() - start
            _bitable_build_state["status"] = "failed"
            _bitable_build_state["error"] = f"{type(e).__name__}: {str(e)[:800]}"
            logger.error(f"❌ Bitable 索引构建失败（耗时 {elapsed:.1f}s）: {e}", exc_info=True)
    finally:
        _bitable_build_state["finished_at"] = datetime.now(timezone.utc).isoformat()
        _build_lock.release()


def _auto_build_on_startup():
    """启动时检查 ChromaDB 是否需要重建索引 / 清空外部集合。

    触发条件（任一满足即重建/清空）：
    1. 内部集合为空（docs == 0）
    2. 内部 schema_version 落后于 _CHROMA_SCHEMA_VERSION
    3. 外部 schema_version 落后于 _CHROMA_EXTERNAL_SCHEMA_VERSION

    内部集合：触发 _run_bitable_build() 30s 后重建。
    外部集合：直接清空（下次报告生成时爬虫自动重新填充）。
    """
    need_internal_rebuild = False
    need_external_clear = False

    # ── 内部集合检查 ──────────────────────────────────────────
    try:
        int_docs = collection_count()
    except Exception:
        int_docs = -1

    int_stored_version = 0
    if int_docs > 0:
        try:
            int_coll = _get_collection(settings.chroma_collection_internal)
            int_stored_version = int(
                int_coll.metadata.get("schema_version", "0") if int_coll.metadata else "0"
            )
        except Exception:
            int_stored_version = 0

    if int_docs <= 0:
        need_internal_rebuild = True
    elif int_stored_version < _CHROMA_SCHEMA_VERSION:
        need_internal_rebuild = True

    # ── 外部集合检查 ──────────────────────────────────────────
    try:
        ext_docs = collection_count(settings.chroma_collection_external)
    except Exception:
        ext_docs = -1

    ext_stored_version = 0
    if ext_docs > 0:
        try:
            ext_coll = _get_collection(settings.chroma_collection_external)
            ext_stored_version = int(
                ext_coll.metadata.get("schema_version", "0") if ext_coll.metadata else "0"
            )
        except Exception:
            ext_stored_version = 0

    if ext_docs > 0 and ext_stored_version < _CHROMA_EXTERNAL_SCHEMA_VERSION:
        need_external_clear = True

    # ── 内部集合重建 ──────────────────────────────────────────
    if need_internal_rebuild:
        reason = "集合为空" if int_docs <= 0 else (
            f"schema_version {int_stored_version} < {_CHROMA_SCHEMA_VERSION}"
        )
        logger.warning(
            f"⚠️ ChromaDB internal_docs 需重建: {reason}（当前 {int_docs} 条），"
            f"将在 30s 后自动重建..."
        )

        def _delayed_rebuild():
            import time as _time
            _time.sleep(30)
            _run_bitable_build()

        t = threading.Thread(target=_delayed_rebuild, daemon=True, name="auto-rebuild")
        t.start()
    else:
        logger.info(
            f"📊 ChromaDB internal_docs: {int_docs} 条, "
            f"schema_version={int_stored_version}（已就绪）"
        )

    # ── 外部集合清空 ──────────────────────────────────────────
    if need_external_clear:
        logger.warning(
            f"⚠️ ChromaDB external_docs schema 升级: "
            f"{ext_stored_version} → {_CHROMA_EXTERNAL_SCHEMA_VERSION}，"
            f"清空 {ext_docs} 条旧数据（下次爬虫自动重填）..."
        )
        try:
            from src.rag.vector_store import clear_collection
            clear_collection(settings.chroma_collection_external)
            # 写入新版 schema version
            ext_coll = _get_collection(settings.chroma_collection_external)
            ext_coll.modify(metadata={"schema_version": str(_CHROMA_EXTERNAL_SCHEMA_VERSION)})
            logger.info("✅ external_docs 已清空并更新 schema version")
        except Exception as e:
            logger.error(f"清空 external_docs 失败: {e}")
    elif ext_docs > 0:
        logger.info(
            f"📊 ChromaDB external_docs: {ext_docs} 条, "
            f"schema_version={ext_stored_version}（已就绪）"
        )


@app.get("/admin/bitable-status", tags=["Admin"])
async def bitable_build_status(secret: str = Query(..., description="验证密钥")):
    """查询 Bitable 索引构建状态 + ChromaDB 详情"""
    if not settings.rebuild_index_secret:
        raise HTTPException(status_code=501, detail="管理员未配置 REBUILD_INDEX_SECRET")
    if secret != settings.rebuild_index_secret:
        raise HTTPException(status_code=403, detail="密钥错误")

    # 安全获取 chroma_docs，避免 500
    try:
        doc_count = collection_count()
    except BaseException as e:
        doc_count = -1
        logger.warning(f"bitable-status 获取 collection_count 失败: {e}")

    return {
        "build_state": _bitable_build_state,
        "chroma_docs": doc_count,
        "chroma_persist_dir": settings.chroma_persist_dir,
        "embedding_configured": bool(settings.azure_embedding_deployment),
        "bitable_configured": bool(settings.feishu_bitable_base_token or settings.feishu_bitable_tables),
    }


# ─── External Docs 重建端点 ──────────────────────────────────

_external_build_state: dict[str, Any] = {"status": "idle"}


@app.post("/admin/rebuild-external-index", tags=["Admin"])
async def rebuild_external_index(
    secret: str = Query(..., description="验证密钥"),
    background_tasks: BackgroundTasks = None,
):
    """从飞书网络信息抓取表重建 external_docs 索引（受密钥保护）

    读取 tblnZiEhmSl6htGB 表中的爬虫数据 → 转 chunk → 写入 ChromaDB external_docs 集合。
    """
    if not settings.rebuild_index_secret:
        raise HTTPException(status_code=501, detail="管理员未配置 REBUILD_INDEX_SECRET")
    if secret != settings.rebuild_index_secret:
        raise HTTPException(status_code=403, detail="密钥错误")

    logger.info("📥 收到 external_docs 重建请求")

    _external_build_state["status"] = "building"
    _external_build_state["started_at"] = datetime.now(timezone.utc).isoformat()
    background_tasks.add_task(_run_external_build)
    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "message": "开始重建 external_docs 索引（后台任务）"},
    )


def _run_external_build() -> None:
    """后台执行 external_docs 重建（委托给 build_bitable_index）"""
    import time
    start = time.time()
    try:
        from scripts.build_bitable_index import build_index
        logger.info("📥 开始重建 external_docs（仅外部表）...")
        build_index(rebuild=True, table_filter="tblnZiEhmSl6htGB")
        elapsed = time.time() - start
        from src.rag.vector_store import collection_count
        total = collection_count(collection_name=settings.chroma_collection_external)
        _external_build_state["status"]      = "success"
        _external_build_state["doc_count"]    = total
        _external_build_state["finished_at"]  = datetime.now(timezone.utc).isoformat()
        logger.info(f"✅ external_docs 重建完成：{total} 条（耗时 {elapsed:.1f}s）")
    except BaseException as e:
        elapsed = time.time() - start
        _external_build_state["status"] = "failed"
        _external_build_state["error"] = f"{type(e).__name__}: {str(e)[:800]}"
        _external_build_state["finished_at"] = datetime.now(timezone.utc).isoformat()
        logger.error(f"❌ external_docs 重建失败（{elapsed:.1f}s）: {e}", exc_info=True)


@app.get("/admin/external-status", tags=["Admin"])
async def external_build_status(secret: str = Query(...)):
    """查询 external_docs 重建状态"""
    if not settings.rebuild_index_secret:
        raise HTTPException(status_code=501, detail="管理员未配置 REBUILD_INDEX_SECRET")
    if secret != settings.rebuild_index_secret:
        raise HTTPException(status_code=403, detail="密钥错误")
    try:
        from src.rag.vector_store import collection_count as ext_count
        from src.config import settings as cfg
        ext_docs = ext_count(collection_name=cfg.chroma_collection_external)
    except BaseException:
        ext_docs = -1
    return {**_external_build_state, "external_docs": ext_docs}


# ─── OAuth 授权端点 ──────────────────────────────────────────


@app.post("/admin/batch-crawl", tags=["Admin"])
async def batch_crawl_all(
    secret: str = Query(..., description="验证密钥"),
    background_tasks: BackgroundTasks = None,
    force: bool = Query(False, description="强制重新爬取"),
    limit: int = Query(0, description="限制公司数（0=全部）"),
):
    """批量爬取全部公司数据（受密钥保护）

    从飞书 Bitable 读取所有公司名 → 逐公司运行爬虫（招聘+官网+新闻）
    → 写入 ChromaDB + Bitable。

    GitHub Actions 每周定时调用，也可手动 curl。
    """
    if not settings.rebuild_index_secret:
        raise HTTPException(status_code=501, detail="管理员未配置 REBUILD_INDEX_SECRET")
    if secret != settings.rebuild_index_secret:
        raise HTTPException(status_code=403, detail="密钥错误")

    if _batch_crawl_state["status"] == "running":
        raise HTTPException(
            status_code=409,
            detail=f"已有批量爬取任务运行中（进度: {_batch_crawl_state.get('progress', 'unknown')}）",
        )

    logger.info(f"📥 收到批量爬取请求 (force={force}, limit={limit})")

    if background_tasks is None:
        logger.warning("⚠️ 无 BackgroundTasks，拒绝批量爬取（需要 FastAPI BackgroundTasks）")
        raise HTTPException(status_code=500, detail="服务器配置异常，不支持后台任务")

    background_tasks.add_task(_run_batch_crawl, force, limit)
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "message": f"批量爬取已启动（force={force}, limit={limit}）",
            "check_status": "/admin/batch-crawl-status",
        },
    )


@app.get("/admin/batch-crawl-status", tags=["Admin"])
async def batch_crawl_status(secret: str = Query(..., description="验证密钥")):
    """查询批量爬取任务状态"""
    if not settings.rebuild_index_secret:
        raise HTTPException(status_code=501, detail="管理员未配置 REBUILD_INDEX_SECRET")
    if secret != settings.rebuild_index_secret:
        raise HTTPException(status_code=403, detail="密钥错误")
    return _batch_crawl_state


async def _run_batch_crawl(force: bool, limit: int):
    """后台异步执行批量爬取"""
    import time

    _batch_crawl_state["status"] = "running"
    _batch_crawl_state["started_at"] = datetime.now(timezone.utc).isoformat()
    _batch_crawl_state["error"] = None
    _batch_crawl_state["progress"] = "正在读取公司列表..."
    _batch_crawl_state["summary"] = {}

    logger.info("🔄 批量爬取开始...")
    try:
        from scripts.batch_crawl_all import get_all_companies, batch_crawl

        companies = get_all_companies()
        if limit > 0:
            companies = companies[:limit]

        _batch_crawl_state["progress"] = f"读取到 {len(companies)} 家公司，开始爬取..."
        logger.info(f"   共 {len(companies)} 家公司")

        summary = await batch_crawl(
            companies=companies,
            force=force,
            delay_between=3.0,  # Azure 上稍快些
            timeout_per_company=60.0,
        )

        _batch_crawl_state["status"] = "completed"
        _batch_crawl_state["summary"] = summary
        _batch_crawl_state["progress"] = (
            f"完成！{summary['success']}/{summary['total']} 成功，"
            f"Bitable {summary['total_bitable']} 条写入"
        )
        elapsed = time.time() - time.monotonic() + time.monotonic()
        logger.info(
            f"✅ 批量爬取完成：{summary['success']}/{summary['total']} 家公司，"
            f"耗时 {summary['elapsed_secs']:.0f}s"
        )
    except BaseException as e:
        _batch_crawl_state["status"] = "failed"
        _batch_crawl_state["error"] = f"{type(e).__name__}: {str(e)[:800]}"
        logger.error(f"❌ 批量爬取失败: {e}", exc_info=True)
    finally:
        _batch_crawl_state["finished_at"] = datetime.now(timezone.utc).isoformat()


# ─── OAuth 授权端点 ──────────────────────────────────────────


@app.get("/oauth/callback", tags=["OAuth"])
async def oauth_callback(
    code: str = Query(..., description="飞书返回的 authorization code"),
    state: str = Query("", description="防 CSRF 的 state 参数"),
):
    """飞书 OAuth 回调端点（备用）

    通常是本地脚本接收回调。此端点作为备选方案，
    当 redirect_uri 配置为 Azure 地址时使用。
    """
    try:
        from src.rag.feishu_oauth import init_user_token_from_code
        refresh_token = init_user_token_from_code(code)
        logger.info("OAuth 回调：用户授权成功")
        return {
            "status": "ok",
            "message": "授权成功！请将 refresh_token 配置到 Azure 环境变量 FEISHU_USER_REFRESH_TOKEN",
            "refresh_token": refresh_token,
            "hint": "复制上面的 refresh_token，在 Azure Portal → App Service → Environment variables 中添加",
        }
    except Exception as e:
        logger.error(f"OAuth 回调失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── 命令行入口 ─────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", settings.app_port))
    uvicorn.run(
        "src.main:app",
        host=settings.app_host,
        port=port,
        log_level=settings.log_level.lower(),
        reload=os.environ.get("ENV", "") != "production",
    )
