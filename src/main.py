"""培训智策 Agent — Phase 1 主入口"""

import os
import sys
from contextlib import asynccontextmanager

import dotenv
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.bot.feishu_handler import router as feishu_router
from src.utils.config import settings
from src.utils.logger import logger

# 根目录 .env 加载
dotenv.load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("=" * 60)
    logger.info("🚀 培训智策 Agent v0.1 启动")
    logger.info(f"   环境: {settings.log_level}")
    logger.info(f"   飞书 App ID: {settings.feishu_app_id or '未配置'}")
    logger.info(f"   Azure Endpoint: {'已配置' if settings.azure_openai_endpoint else '未配置'}")
    logger.info(f"   Azure Deployment: {settings.azure_openai_deployment}")
    logger.info("=" * 60)
    yield
    logger.info("培训智策 Agent 已停止")


app = FastAPI(
    title="培训智策 Agent",
    version="0.1.0",
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
        "version": "0.1.0",
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"])
async def health():
    return {
        "status": "ok",
        "version": "0.1.0",
        "feishu_configured": bool(settings.feishu_app_id),
        "azure_configured": bool(settings.azure_openai_endpoint),
    }


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
