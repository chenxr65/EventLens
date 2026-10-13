"""EventLens FastAPI 服务入口。

- 业务路由：/api/events、/api/notifications、/api/watchlist 等（见 api.py）
- 前端静态文件：frontend/dist（构建产物存在时挂载到根路径）
- 后台任务：热点榜定时刷新（联网可用时；断网自动降级为最近一次快照）
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

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


@app.get("/api/health")
def health() -> dict:
    """健康检查：用于本地运行确认服务存活与后续脚本探活。"""
    return {"status": "ok", "ts": int(time.time() * 1000)}


# 前端构建产物（存在时托管；开发模式用 vite dev server 的代理）
_frontend_dist = settings.project_root / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
