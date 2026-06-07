"""飞书 Webhook 处理层"""

from fastapi import APIRouter, Request, HTTPException
from src.utils.logger import logger

router = APIRouter()


@router.post("/feishu")
async def feishu_webhook(request: Request):
    """接收飞书事件推送"""
    body = await request.json()
    logger.info(f"收到飞书事件: {body.get('type', 'unknown')}")

    # URL 验证（飞书首次验证）
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}

    # TODO: Phase 1 — 解析消息事件，触发报告生成
    return {"code": 0, "msg": "ok"}
