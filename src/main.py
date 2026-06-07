"""
培训智策 Agent
飞书机器人 + RAG + LLM 驱动的销售策略报告生成系统
"""

from fastapi import FastAPI
from src.bot.feishu_handler import router as feishu_router
from src.utils.config import settings
from src.utils.logger import logger

app = FastAPI(
    title="培训智策 Agent",
    version="0.1.0",
    description="基于飞书机器人 + RAG + LLM 的销售策略报告系统",
)

app.include_router(feishu_router, prefix="/webhook")


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.on_event("startup")
async def startup_event():
    logger.info("培训智策 Agent 启动中...")
