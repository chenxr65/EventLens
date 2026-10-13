"""扶摇（同花顺金融数据 API）适配器。

- REST 接口，统一响应信封 ApiResponse：{code, message, request_id, data}
- 鉴权：X-api-key 请求头（config/secrets.env）
- 文档：https://fuyao.aicubes.cn/docs/
"""
from __future__ import annotations

from typing import Any

import httpx

from backend.app.config import settings


class FuyaoError(RuntimeError):
    """扶摇接口调用失败（网络/业务错误）。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"[code={code}] {message}")
        self.code = code
        self.message = message


class FuyaoClient:
    """扶摇 REST 客户端。"""

    def __init__(self, timeout: float = 30.0) -> None:
        self._base = settings.fuyao_base_url
        self._client = httpx.Client(
            timeout=timeout, headers={"X-api-key": settings.fuyao_api_key}
        )

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """调用 REST 接口并拆掉 ApiResponse 信封，返回 data 部分。

        业务错误（code != 0）与网络错误统一抛 FuyaoError，调用方按需捕获。
        """
        try:
            resp = self._client.get(f"{self._base}{path}", params=params)
        except httpx.HTTPError as exc:
            raise FuyaoError(-1, f"网络错误: {exc}") from exc
        try:
            body = resp.json()
        except ValueError as exc:
            raise FuyaoError(-1, f"响应非 JSON（HTTP {resp.status_code}）") from exc
        code = body.get("code")
        if code != 0:
            raise FuyaoError(int(code if code is not None else -1), str(body.get("message")))
        return body.get("data")

    # -- 已验证接口的便捷方法（其余按需扩充） ------------------------------
    def snapshot(self, thscodes: list[str]) -> Any:
        """行情快照（支持单只/多只，thscode 形如 600519.SH）。"""
        return self.get("/api/a-share/prices/snapshot", {"thscodes": ",".join(thscodes)})

    def close(self) -> None:
        self._client.close()
