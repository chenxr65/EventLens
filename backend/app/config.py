"""配置加载：从工程根目录的 config/secrets.env 读取密钥与基础地址。

密钥文件不进入 git（见 .gitignore），模板见 config/secrets.example.env。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 工程根目录（backend/app/config.py -> 上溯 2 级）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRETS_FILE = PROJECT_ROOT / "config" / "secrets.env"

load_dotenv(SECRETS_FILE, override=False)


class Settings:
    """集中读取环境变量；缺失关键密钥时给出明确报错。"""

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    @property
    def fuyao_base_url(self) -> str:
        return os.getenv("FUYAO_BASE_URL", "https://fuyao.aicubes.cn").rstrip("/")

    @property
    def fuyao_api_key(self) -> str:
        key = os.getenv("FUYAO_API_KEY", "").strip()
        if not key:
            raise RuntimeError("缺少 FUYAO_API_KEY，请在 config/secrets.env 中配置")
        return key

    @property
    def ifind_base_url(self) -> str:
        return os.getenv(
            "IFIND_BASE_URL", "https://api-mcp.51ifind.com:8643/ds-mcp-servers"
        ).rstrip("/")

    @property
    def ifind_auth_token(self) -> str:
        token = os.getenv("IFIND_AUTH_TOKEN", "").strip()
        if not token:
            raise RuntimeError("缺少 IFIND_AUTH_TOKEN，请在 config/secrets.env 中配置")
        return token

    # LLM 为可选项：未配置或失效时，管线自动回退到预计算缓存
    @property
    def llm_base_url(self) -> str:
        return os.getenv("LLM_BASE_URL", "").rstrip("/")

    @property
    def llm_api_key(self) -> str:
        return os.getenv("LLM_API_KEY", "").strip()

    @property
    def llm_model(self) -> str:
        return os.getenv("LLM_MODEL", "deepseek-chat")


settings = Settings()
