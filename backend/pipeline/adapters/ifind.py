"""iFinD MCP 数据服务适配器。

调用机制（与官方技能包 call.py 一致）：
- 8 个子服务挂载在 https://api-mcp.51ifind.com:8643/ds-mcp-servers/<service>-mcp
- JSON-RPC：initialize（回包头 Mcp-Session-Id）→ notifications/initialized → tools/list / tools/call
- 鉴权：Authorization 请求头携带用户密钥（config/secrets.env）
- 免费版权限每秒最多 2 个并发请求，调用方需控制并发
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from backend.app.config import settings

# 子服务枚举（与官方技能包保持一致）
SERVICES = {
    "stock": "hexin-ifind-ds-stock-mcp",
    "fund": "hexin-ifind-ds-fund-mcp",
    "edb": "hexin-ifind-ds-edb-mcp",
    "news": "hexin-ifind-ds-news-mcp",
    "bond": "hexin-ifind-ds-bond-mcp",
    "global_stock": "hexin-ifind-ds-global-stock-mcp",
    "index": "hexin-ifind-ds-index-mcp",
    "future": "hexin-ifind-ds-futures-mcp",
}


class IfindError(RuntimeError):
    """iFinD MCP 调用失败（网络/协议/业务错误）。"""


class IfindClient:
    """iFinD MCP 客户端（同步，单实例串行调用）。"""

    def __init__(self, timeout: float = 60.0) -> None:
        self._base = settings.ifind_base_url
        self._token = settings.ifind_auth_token
        self._client = httpx.Client(timeout=timeout)
        self._sessions: dict[str, str] = {}
        self._req_id = 0

    # -- 基础协议 ---------------------------------------------------------
    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _endpoint(self, service: str) -> str:
        if service not in SERVICES:
            raise IfindError(f"未知服务: {service}")
        return f"{self._base}/{SERVICES[service]}"

    def _headers(self, service: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": self._token,
        }
        if session := self._sessions.get(service):
            headers["Mcp-Session-Id"] = session
        return headers

    def _post(self, service: str, payload: dict[str, Any]) -> tuple[httpx.Response, Any]:
        try:
            resp = self._client.post(
                self._endpoint(service), json=payload, headers=self._headers(service)
            )
        except httpx.HTTPError as exc:
            raise IfindError(f"请求失败 {service}: {exc}") from exc
        data: Any = None
        text = resp.text.strip()
        if text:
            try:
                data = resp.json()
            except json.JSONDecodeError:
                data = self._parse_sse(text)
        return resp, data

    @staticmethod
    def _parse_sse(text: str) -> Any:
        """兼容 SSE（text/event-stream）形态：从 data: 行解析 JSON。"""
        for line in text.splitlines():
            if line.startswith("data:"):
                chunk = line[len("data:"):].strip()
                try:
                    return json.loads(chunk)
                except json.JSONDecodeError:
                    continue
        return text

    def _ensure_session(self, service: str) -> None:
        if service in self._sessions:
            return
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "eventlens-pipeline", "version": "0.1.0"},
            },
        }
        resp, _ = self._post(service, payload)
        if resp.status_code >= 400:
            raise IfindError(f"initialize 失败 {service}: HTTP {resp.status_code}")
        session_id = resp.headers.get("Mcp-Session-Id")
        if not session_id:
            raise IfindError(f"initialize 未返回 Mcp-Session-Id（{service}）")
        self._sessions[service] = session_id
        self._post(service, {"jsonrpc": "2.0", "method": "notifications/initialized"})

    # -- 工具调用 ---------------------------------------------------------
    def list_tools(self, service: str) -> list[str]:
        """获取服务当前真实可用工具清单（不消耗调用额度）。"""
        self._ensure_session(service)
        payload = {"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": {}}
        resp, data = self._post(service, payload)
        if resp.status_code >= 400 or not isinstance(data, dict):
            raise IfindError(f"tools/list 失败 {service}: HTTP {resp.status_code}")
        tools = (data.get("result") or {}).get("tools")
        if not isinstance(tools, list):
            raise IfindError(f"tools/list 响应异常 {service}: {str(data)[:200]}")
        return [t.get("name") for t in tools if t.get("name")]

    def call(self, service: str, tool: str, params: dict[str, Any]) -> Any:
        """调用工具，返回 MCP result 对象。"""
        self._ensure_session(service)
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": tool, "arguments": params},
        }
        resp, data = self._post(service, payload)
        if isinstance(data, dict) and "error" in data:
            raise IfindError(f"工具调用错误 {service}/{tool}: {data['error']}")
        if resp.status_code >= 400:
            raise IfindError(f"工具调用失败 {service}/{tool}: HTTP {resp.status_code}")
        if not isinstance(data, dict) or "result" not in data:
            raise IfindError(f"工具调用响应异常 {service}/{tool}: {str(data)[:200]}")
        return data["result"]

    def call_text(self, service: str, tool: str, params: dict[str, Any]) -> str:
        """调用工具并抽取文本内容（MCP content 数组拼接）。"""
        result = self.call(service, tool, params)
        parts: list[str] = []
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts)

    def close(self) -> None:
        self._client.close()
