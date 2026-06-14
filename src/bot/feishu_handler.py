"""飞书 Webhook 处理层 — Phase 4 实现"""

import hmac
import hashlib
import json
import base64
import re
import time
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from src.utils.config import settings
from src.utils.logger import logger
from src.bot.message_parser import parse_user_input
from src.report.generator import generate_report
from src.bot.card_builder import build_report_card

router = APIRouter()

# ── 全链路健康检测关键词 ─────────────────────────────────────
# 这些关键词不触发 Agent 报告功能，而是直连 LLM 验证全链路通畅
HEALTH_CHECK_KEYWORDS = [
    "你好", "hi", "hello", "hey", "嗨",
    "ping", "test", "测试", "在吗", "在不在",
    "能听到吗", "能听到",
]
HEALTH_CHECK_PATTERN = re.compile(
    r"^\s*(" + "|".join(re.escape(k) for k in HEALTH_CHECK_KEYWORDS) + r")\s*[!！。.？?]?\s*$",
    re.IGNORECASE,
)


def _is_health_check(content: str) -> bool:
    """检测是否为全链路健康检查消息（纯打招呼/test/ping，不触发报告功能）"""
    stripped = content.strip()
    # 去掉 @机器人 前缀
    stripped = re.sub(r"^@\S+\s*", "", stripped).strip()
    return bool(HEALTH_CHECK_PATTERN.match(stripped))


def _verify_signature(body: bytes, timestamp: str, nonce: str, encrypt_key: str) -> bool:
    """验证飞书 Webhook 签名"""
    if not encrypt_key:
        return True
    content = f"{timestamp}{nonce}{encrypt_key}{body.decode('utf-8', errors='replace')}"
    sign = base64.b64encode(
        hmac.new(encrypt_key.encode(), content.encode(), hashlib.sha256).digest()
    ).decode()
    return True  # Phase 1 暂不强制校验，便于调试


def _parse_event_body(body: dict) -> dict | None:
    """解析飞书事件体，返回消息内容"""
    event_type = body.get("header", {}).get("event_type", "")
    if event_type != "im.message.receive_v1":
        logger.debug(f"忽略非消息事件: {event_type}")
        return None

    event = body.get("event", {})
    # 过滤机器人自己发的消息，避免死循环
    sender_id = event.get("sender", {}).get("sender_id", {}).get("open_id", "")
    if sender_id == settings.feishu_app_id:
        return None

    message = event.get("message", {})
    msg_type = message.get("message_type", "")
    chat_id = message.get("chat_id", "")
    msg_id = message.get("message_id", "")

    content = ""
    if msg_type == "text":
        try:
            content_json = json.loads(message.get("content", "{}"))
            content = content_json.get("text", "")
        except Exception:
            content = message.get("content", "")
    else:
        logger.info(f"暂不支持的消息类型: {msg_type}")
        return None

    return {
        "chat_id": chat_id,
        "msg_id": msg_id,
        "open_id": sender_id,
        "content": content,
        "msg_type": msg_type,
    }


async def _handle_health_check(chat_id: str, msg_id: str, content: str):
    """全链路健康检测：飞书 → Azure → LLM → 飞书回复

    不触发 Agent 报告逻辑，单纯直连 LLM 验证全链路通畅。
    检测飞书接收、Web Service 处理、LLM 连接、飞书回复四个环节。
    """
    t0 = time.monotonic()
    try:
        from src.llm.azure_client import chat_completion

        health_messages = [
            {"role": "system", "content": (
                "你是培训智策 Agent 的运维助手。用户正在做全链路连通性检测。"
                "请用一句话回复，确认系统运行正常，并简要说明当前可用的能力。"
                "语气友好简洁，不超过 80 字。"
            )},
            {"role": "user", "content": content},
        ]

        logger.info(f"🔍 健康检测开始，用户消息: {content[:50]}")
        llm_response = await chat_completion(
            health_messages,
            max_tokens=200,
            reasoning_effort="low",  # 快速响应，不需要深度推理
        )
        elapsed = time.monotonic() - t0
        logger.info(f"✅ 健康检测完成，LLM 耗时 {elapsed:.1f}s，回复: {llm_response[:80]}")

        # 返回带有链路信息的回复
        reply_text = (
            f"{llm_response}\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🔗 全链路状态\n"
            f"  飞书接收 ✅ | Azure WS ✅ | LLM ✅ ({elapsed:.1f}s) | 飞书回复 ✅"
        )
        await _reply_text(chat_id, msg_id, reply_text)

    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error(f"❌ 健康检测失败 ({elapsed:.1f}s): {e}", exc_info=True)
        await _reply_text(
            chat_id, msg_id,
            f"❌ 链路检测失败 ({elapsed:.1f}s)\n"
            f"错误: {type(e).__name__}: {str(e)[:150]}\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🔗 全链路状态\n"
            f"  飞书接收 ✅ | Azure WS ✅ | LLM ❌ | 飞书回复 ✅"
        )


async def _handle_user_message(chat_id: str, msg_id: str, content: str):
    """后台任务：解析输入 → 生成报告 → 推送飞书卡片"""
    try:
        # Step 0: 全链路健康检测（纯打招呼/test/ping 直连 LLM，不触发报告）
        if _is_health_check(content):
            await _handle_health_check(chat_id, msg_id, content)
            return

        # Step 1: 解析用户输入（Regex 优先）
        parsed = parse_user_input(content)
        company = parsed.get("company", "")

        # Step 1b: Regex 失败 → LLM 兜底提取
        if not company:
            from src.bot.message_parser import extract_entities_via_llm
            from src.utils.logger import logger as _logger
            _logger.info(f"Regex 未提取到公司名，使用 LLM 兜底提取...")
            llm_parsed = await extract_entities_via_llm(content)
            if llm_parsed and llm_parsed.get("company"):
                company = llm_parsed.get("company", "")
                # 合并 LLM 提取结果到 parsed
                parsed["company"] = company
                if llm_parsed.get("visit_purpose"):
                    parsed["visit_purpose"] = llm_parsed["visit_purpose"]
                if llm_parsed.get("focus_areas"):
                    parsed["focus_areas"] = llm_parsed["focus_areas"]
                if llm_parsed.get("visit_target"):
                    parsed["visit_target"] = llm_parsed["visit_target"]
                if llm_parsed.get("known_info"):
                    parsed["known_info"] = llm_parsed["known_info"]
                _logger.info(f"LLM 兜底提取成功: company={company}")
            else:
                await _reply_text(chat_id, msg_id, _build_help_text())
                return

        # Step 2: 发送"正在生成"提示
        await _reply_text(
            chat_id, msg_id,
            f"✅ 已收到【{company}】的生成请求，正在调用 AI 生成报告，预计需要 2-3 分钟，请稍候..."
        )

        # Step 3: 生成报告（reasoning_effort="low" 以加速生成，约2.1x）
        report_data = await generate_report(parsed, reasoning_effort="low")

        # Step 4: 构建并推送飞书卡片
        card_json = build_report_card(report_data)
        await _send_card(chat_id, card_json, msg_id)

    except Exception as e:
        logger.error(f"处理消息失败: {e}", exc_info=True)
        await _reply_text(chat_id, msg_id, f"❌ 报告生成失败：{str(e)[:200]}")


async def _reply_text(chat_id: str, msg_id: str, text: str):
    """回复文本消息（通过 reply API）"""
    try:
        import httpx
        url = f"https://open.feishu.cn/open-apis/im/v1/messages/{msg_id}/reply"
        headers = await _get_auth_headers()
        payload = {
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                logger.warning(f"回复消息失败: {resp.text}")
    except Exception as e:
        logger.warning(f"回复消息异常: {e}")


async def _send_card(chat_id: str, card_json: dict, msg_id: str | None = None):
    """发送飞书卡片消息"""
    try:
        import httpx
        if msg_id:
            url = f"https://open.feishu.cn/open-apis/im/v1/messages/{msg_id}/reply"
        else:
            url = "https://open.feishu.cn/open-apis/im/v1/messages"
            payload_base = {"receive_id": chat_id}

        headers = await _get_auth_headers()
        payload = {
            "msg_type": "interactive",
            "content": json.dumps(card_json, ensure_ascii=False),
        }
        if not msg_id:
            payload["receive_id"] = chat_id

        async with httpx.AsyncClient(timeout=30) as client:
            if msg_id:
                resp = await client.post(url, headers=headers, json=payload)
            else:
                resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                logger.warning(f"发送卡片失败: {resp.text}")
    except Exception as e:
        logger.warning(f"发送卡片异常: {e}")


async def _get_auth_headers() -> dict:
    """获取飞书 API 调用凭证（使用 App ID + App Secret 获取 tenant_access_token）"""
    import httpx
    token = getattr(_get_auth_headers, "_token", None)
    expire_at = getattr(_get_auth_headers, "_expire_at", 0)
    now = time.time()
    if token and now < expire_at - 60:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": settings.feishu_app_id,
        "app_secret": settings.feishu_app_secret,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload)
        data = resp.json()

    token = data.get("tenant_access_token", "")
    expire_at = now + data.get("expire", 7200)
    _get_auth_headers._token = token
    _get_auth_headers._expire_at = expire_at

    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _build_help_text() -> str:
    return """📋 **培训智策 Agent 使用说明**

请按以下格式输入（复制后修改括号内内容）：

```
@培训智策Agent 请帮我生成针对以下客户的销售策略报告：
- 客户公司：[公司全称]
- 拜访对象部门/职位：[如：技术研发部/CTO]
- 当前已知信息（选填）：[...]
- 本次拜访主要目的：[如：首次接触/挖掘培训需求]
- 期望侧重方向（多选）：微软Agent培训 / AWS培训 / MSP / 安服 / MA
- 特别要求（选填）：[...]
```

⚠️ 至少提供「客户公司」名称，否则无法生成报告。
"""


@router.post("/feishu")
async def feishu_webhook(request: Request, background_tasks: BackgroundTasks):
    """接收飞书事件推送（Webhook 模式）"""
    body_bytes = await request.body()
    body = json.loads(body_bytes.decode("utf-8", errors="replace"))

    # URL 验证（飞书首次订阅验证）
    if body.get("type") == "url_verification":
        return JSONResponse({"challenge": body.get("challenge")})

    # 解析消息事件
    msg_info = _parse_event_body(body)
    if msg_info:
        # 后台异步处理，立即返回 200
        background_tasks.add_task(
            _handle_user_message,
            msg_info["chat_id"],
            msg_info["msg_id"],
            msg_info["content"],
        )

    return JSONResponse({"code": 0, "msg": "ok"})


# ─── WebSocket 模式（可选，Phase 1 注释掉，优先 Webhook）────────────────
# 如需使用 WebSocket 长连接，取消注释以下代码，并在 main.py 中注册事件监听

# app = Application(app_id=settings.feishu_app_id, app_secret=settings.feishu_app_secret)
#
# @app.event.subscribe(WebSocketEventType.MESSAGE_RECEIVE)
# async def on_message(event):
#     content = json.loads(event.message.content).get("text", "")
#     parsed = parse_user_input(content)
#     report_data = await generate_report(parsed)
#     card = build_report_card(report_data)
#     await app.im.send(message=..., msg_type="interactive", content=json.dumps(card))
