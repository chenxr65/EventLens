"""可插拔 LLM 客户端（OpenAI 兼容接口）。

设计目标（可插拔 + 缓存兜底）：
- 配置有效时走真实调用（DeepSeek 或任意 OpenAI 兼容网关，改 Key 即可切换）
- 任何失败（无配置 / Key 失效 / 超时 / 响应异常）统一抛 LLMUnavailableError
- 上层管线捕获该异常后回退到 backend/data/analysis 预计算缓存，保证产品始终可用
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from backend.app.config import settings


class LLMUnavailableError(RuntimeError):
    """LLM 不可用（缺配置/网络失败/鉴权失败/响应异常），调用方应回退缓存。"""


class LLMClient:
    """OpenAI 兼容 Chat Completions 客户端。"""

    def __init__(self, timeout: float = 120.0) -> None:
        self._base = settings.llm_base_url
        self._key = settings.llm_api_key
        self._model = settings.llm_model
        self._client = httpx.Client(timeout=timeout)

    @property
    def available(self) -> bool:
        return bool(self._base and self._key)

    @property
    def model(self) -> str:
        return self._model

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int = 2000,
    ) -> str:
        """发起对话补全；失败统一抛 LLMUnavailableError。"""
        if not self.available:
            raise LLMUnavailableError("未配置 LLM_BASE_URL / LLM_API_KEY")
        try:
            resp = self._client.post(
                f"{self._base}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._key}"},
                json={
                    "model": self._model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"网络错误: {exc}") from exc
        if resp.status_code in (401, 403):
            raise LLMUnavailableError(f"鉴权失败 HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise LLMUnavailableError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMUnavailableError(f"响应结构异常: {resp.text[:200]}") from exc

    def chat_json(self, messages: list[dict], **kwargs: Any) -> Any:
        """对话补全并解析 JSON 输出（容忍 markdown 代码块包裹）。"""
        return extract_json(self.chat(messages, **kwargs))

    def close(self) -> None:
        self._client.close()


def extract_json(text: str) -> Any:
    """从模型输出中提取 JSON 对象/数组。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMUnavailableError(f"无法从模型输出解析 JSON: {text[:200]}")
