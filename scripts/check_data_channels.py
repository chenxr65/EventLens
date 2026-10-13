"""数据通道连通性检查（扶摇 REST + iFinD MCP）。

用法（工程根目录）：
    .venv\\Scripts\\python.exe scripts\\check_data_channels.py

用途：开发自检；也可作为"数据/接口异常"测试的基线脚本。
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

# 将工程根目录加入 sys.path，保证 backend 包可导入
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.pipeline.adapters.fuyao import FuyaoClient  # noqa: E402
from backend.pipeline.adapters.ifind import IfindClient  # noqa: E402


def check_fuyao() -> None:
    print("== 扶摇 REST ==")
    client = FuyaoClient()
    try:
        data = client.snapshot(["600519.SH"])
        item = (data.get("item") or [{}])[0]
        print(
            f"600519.SH 最新价: {item.get('last_price')} "
            f"涨跌幅: {item.get('price_change_ratio_pct')}%"
        )
    finally:
        client.close()


def check_ifind() -> None:
    print("== iFinD MCP ==")
    client = IfindClient()
    try:
        tools = client.list_tools("news")
        print(f"news 服务可用工具: {tools}")
        text = client.call_text(
            "news",
            "search_news",
            {
                "query": "人工智能",
                "time_start": "2026-09-01",
                "time_end": "2026-10-13",
                "size": 2,
            },
        )
        print("search_news 返回（截断）:", text[:300].replace("\n", " "))
    finally:
        client.close()


if __name__ == "__main__":
    for fn in (check_fuyao, check_ifind):
        try:
            fn()
        except Exception:  # 通道异常需完整暴露，便于测试记录
            print(f"[FAIL] {fn.__name__}")
            traceback.print_exc()
