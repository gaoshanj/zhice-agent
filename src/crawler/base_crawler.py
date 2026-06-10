"""爬虫基础类 — Phase 3

提供通用的 HTTP 请求封装，兼容两种模式：
- httpx（Azure 部署，无浏览器环境）
- Playwright（本地开发，处理 JS 渲染页面）

设计原则：
- 优先 httpx（轻量、速度快）
- 仅当页面明确需要 JS 渲染时用 Playwright
- Azure 上 crawler_use_playwright=False，强制 httpx
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup

from src.utils.config import settings
from src.utils.logger import logger


# ── 通用请求头（模拟浏览器，降低被拦截概率）────────────────────
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    # 注意：不设置 Accept-Encoding，让 httpx 自动处理解压（避免手动解压乱码）
    "Connection": "keep-alive",
}


class BaseCrawler:
    """爬虫基类，封装 HTTP 请求和基础文本处理"""

    def __init__(self, timeout: int | None = None):
        self.timeout = timeout or settings.crawler_timeout
        self.delay = settings.crawler_request_delay
        self._last_request_time: float = 0.0

    # ── 速率控制 ─────────────────────────────────────────────────

    async def _wait_rate_limit(self) -> None:
        """确保两次请求之间有足够间隔，避免被封"""
        elapsed = time.monotonic() - self._last_request_time
        wait = self.delay - elapsed
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request_time = time.monotonic()

    # ── HTTP 请求（httpx，适用于 Azure 部署）────────────────────

    async def fetch_html(
        self,
        url: str,
        headers: dict | None = None,
        params: dict | None = None,
        retries: int | None = None,
    ) -> str | None:
        """使用 httpx 获取页面 HTML

        Args:
            url: 目标 URL
            headers: 额外请求头
            params: URL 查询参数
            retries: 重试次数（默认取配置值）

        Returns:
            页面 HTML 字符串，失败返回 None
        """
        await self._wait_rate_limit()
        max_retries = retries if retries is not None else settings.crawler_max_retries
        merged_headers = {**DEFAULT_HEADERS, **(headers or {})}

        for attempt in range(1, max_retries + 2):
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    follow_redirects=True,
                    verify=False,  # 部分企业官网证书有问题
                ) as client:
                    resp = await client.get(url, headers=merged_headers, params=params)
                    resp.raise_for_status()
                    # 尝试正确解码（优先 UTF-8，fallback GBK）
                    try:
                        return resp.text
                    except Exception:
                        return resp.content.decode("gbk", errors="replace")
            except httpx.HTTPStatusError as e:
                logger.warning(f"HTTP {e.response.status_code} → {url}")
                if e.response.status_code in (403, 429):
                    # 被限流，等更长时间
                    await asyncio.sleep(5 * attempt)
                if attempt > max_retries:
                    return None
            except Exception as e:
                logger.warning(f"请求失败（第{attempt}次）: {url} — {e}")
                if attempt > max_retries:
                    return None
                await asyncio.sleep(2 * attempt)

        return None

    async def fetch_html_playwright(
        self,
        url: str,
        wait_selector: str | None = None,
        timeout_ms: int = 15000,
    ) -> str | None:
        """使用 Playwright 获取 JS 渲染后的页面 HTML

        仅在 settings.crawler_use_playwright=True 时使用。

        Args:
            url: 目标 URL
            wait_selector: 等待某个 CSS 选择器出现（如 '.job-list'）
            timeout_ms: 超时毫秒

        Returns:
            页面 HTML 字符串，失败返回 None
        """
        if not settings.crawler_use_playwright:
            logger.debug("Playwright 未启用（Azure 模式），跳过")
            return None

        try:
            from playwright.async_api import async_playwright  # 懒导入
        except ImportError:
            logger.warning("playwright 未安装，回退到 httpx")
            return None

        await self._wait_rate_limit()
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=DEFAULT_HEADERS["User-Agent"]
                )
                page = await context.new_page()
                await page.goto(url, timeout=timeout_ms)
                if wait_selector:
                    await page.wait_for_selector(wait_selector, timeout=timeout_ms)
                html = await page.content()
                await browser.close()
                return html
        except Exception as e:
            logger.warning(f"Playwright 获取失败: {url} — {e}")
            return None

    # ── HTML → 文本 ──────────────────────────────────────────────

    @staticmethod
    def parse_html(html: str, selector: str = "body") -> BeautifulSoup:
        """将 HTML 解析为 BeautifulSoup 对象"""
        return BeautifulSoup(html, "lxml")

    @staticmethod
    def extract_text(html: str, remove_tags: list[str] | None = None) -> str:
        """从 HTML 提取干净的文本

        Args:
            html: 原始 HTML
            remove_tags: 需要移除的标签列表（默认移除 script/style/nav/footer 等）

        Returns:
            清洗后的纯文本
        """
        if not html:
            return ""

        soup = BeautifulSoup(html, "lxml")

        # 移除无用标签
        noise_tags = remove_tags or ["script", "style", "nav", "footer",
                                     "header", "aside", "iframe", "noscript"]
        for tag in noise_tags:
            for el in soup.find_all(tag):
                el.decompose()

        text = soup.get_text(separator="\n", strip=True)
        return clean_text(text)


# ── 工具函数 ─────────────────────────────────────────────────────


def clean_text(text: str) -> str:
    """清洗文本：去除多余空白、空行、无效字符"""
    if not text:
        return ""
    # 去除多余空行
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if len(line) > 1]  # 去空行和单字符行
    # 合并连续空行
    cleaned: list[str] = []
    prev_empty = False
    for line in lines:
        if not line:
            if not prev_empty:
                cleaned.append("")
            prev_empty = True
        else:
            cleaned.append(line)
            prev_empty = False
    return "\n".join(cleaned).strip()


def truncate_text(text: str, max_chars: int = 2000) -> str:
    """截断文本到指定长度"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...（截断）"


def deduplicate_texts(texts: list[str], min_len: int = 20) -> list[str]:
    """对文本列表去重（基于前 60 字）"""
    seen: set[str] = set()
    result: list[str] = []
    for t in texts:
        t = t.strip()
        if len(t) < min_len:
            continue
        key = re.sub(r"\s+", "", t[:60])
        if key not in seen:
            seen.add(key)
            result.append(t)
    return result
