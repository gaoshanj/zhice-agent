"""培训智策 Agent — Phase 2 主入口"""

# ── ChromaDB SQLite 兼容性修复（Azure App Service 系统 sqlite3 过旧）──
__import__("pysqlite3")
import sys
sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")

import os
from contextlib import asynccontextmanager
from typing import Optional

import dotenv
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.bot.feishu_handler import router as feishu_router
from src.utils.config import settings
from src.utils.logger import logger

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
        "bitable_configured": bool(settings.feishu_bitable_base_token),
        "oauth_configured": bool(settings.feishu_user_refresh_token),
        "embedding_configured": bool(settings.azure_embedding_deployment),
        "chroma_docs": _chroma_status(),
    }


def _chroma_status() -> int:
    """获取 ChromaDB 文档数（安全获取，不抛异常）"""
    try:
        from src.rag.vector_store import collection_count
        return collection_count()
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
async def build_bitable_index(secret: str = Query(..., description="验证密钥")):
    """构建 Bitable 知识库 RAG 索引（受密钥保护）

    从飞书多维表格拉取客户商机数据 → 生成 embedding → 写入 ChromaDB。
    GitHub Actions 或手动 curl 调用此端点。
    """
    if not settings.rebuild_index_secret:
        raise HTTPException(status_code=501, detail="管理员未配置 REBUILD_INDEX_SECRET")
    if secret != settings.rebuild_index_secret:
        raise HTTPException(status_code=403, detail="密钥错误")

    logger.info("📥 收到 Bitable 索引构建请求")

    try:
        import asyncio
        asyncio.create_task(_run_bitable_build())
        return JSONResponse(
            status_code=202,
            content={"status": "accepted", "message": "开始构建 Bitable 索引"},
        )
    except Exception as e:
        logger.error(f"Bitable 索引构建启动失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def _run_bitable_build():
    """后台异步执行 Bitable 索引构建"""
    from scripts.build_bitable_index import build_index
    import time

    start = time.time()
    logger.info("🔄 Bitable 索引构建开始...")
    try:
        build_index(rebuild=True)
        elapsed = time.time() - start
        logger.info(f"✅ Bitable 索引构建完成（耗时 {elapsed:.1f}s）")
    except BaseException as e:
        elapsed = time.time() - start
        logger.error(f"❌ Bitable 索引构建失败（耗时 {elapsed:.1f}s）: {e}", exc_info=True)


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
