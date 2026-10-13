"""EventLens FastAPI 服务入口。

- 业务路由：/api/events、/api/notifications、/api/watchlist 等（见 api.py）
- 前端静态文件：frontend/dist（构建产物存在时挂载到根路径）
- 后台任务：热点榜定时刷新（联网可用时；断网自动降级为最近一次快照）
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import os
import time
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from backend.app.api import router as api_router
from backend.app.config import settings
from backend.app.db import get_conn, init_db
from backend.pipeline.hot import collect_hot_topics, latest_batch

# 热点榜自动刷新间隔（分钟）
HOT_REFRESH_INTERVAL_MIN = 30


def _maybe_refresh_hot() -> None:
    """批次超过刷新间隔时拉取新热点榜；失败由调用方静默降级。"""
    conn = get_conn()
    try:
        batch = latest_batch(conn)
    finally:
        conn.close()
    if batch:
        try:
            age = datetime.now() - datetime.strptime(batch, "%Y-%m-%dT%H:%M:%S")
            if age < timedelta(minutes=HOT_REFRESH_INTERVAL_MIN):
                return
        except ValueError:
            pass
    collect_hot_topics()


async def _hot_refresh_loop() -> None:
    while True:
        with contextlib.suppress(Exception):  # 断网/限流时静默降级，不影响服务
            await asyncio.to_thread(_maybe_refresh_hot)
        await asyncio.sleep(60)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    task = asyncio.create_task(_hot_refresh_loop())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="EventLens — 投资事件证据追踪台", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)


# --------------------------------------------------------------------------
# 可选 Basic Auth：仅当环境变量 BASIC_AUTH_USER 与 BASIC_AUTH_PASS 同时设置时启用。
# 本地 start.bat 不设置 -> 完全无感；公网演示版在服务器 secrets.env 配置 -> 启用。
# /api/health 始终豁免，用于探活与端口连通性检测。
# --------------------------------------------------------------------------
_AUTH_EXEMPT_PATHS = {"/api/health"}


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, username: str, password: str) -> None:
        super().__init__(app)
        self._expected = base64.b64encode(
            f"{username}:{password}".encode("utf-8")
        ).decode("ascii")

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)
        header = request.headers.get("authorization", "")
        parts = header.split(" ", 1)
        provided = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "basic" else ""
        if provided and hmac.compare_digest(provided, self._expected):
            return await call_next(request)
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="EventLens"'},
            content="需要访问口令（Basic Auth）。",
            media_type="text/plain; charset=utf-8",
        )


_auth_user = os.getenv("BASIC_AUTH_USER", "").strip()
_auth_pass = os.getenv("BASIC_AUTH_PASS", "").strip()
if _auth_user and _auth_pass:
    app.add_middleware(BasicAuthMiddleware, username=_auth_user, password=_auth_pass)



@app.get("/api/health")
def health() -> dict:
    """健康检查：用于本地运行确认服务存活与后续脚本探活。"""
    return {"status": "ok", "ts": int(time.time() * 1000)}


# 前端构建产物（存在时托管；开发模式用 vite dev server 的代理）
_frontend_dist = settings.project_root / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
