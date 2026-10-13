"""行情采集器（扶摇）：历史 K 线与当日快照，用于事件影响验证。

用法（工程根目录）：
    .venv\\Scripts\\python.exe -m backend.pipeline.market \\
        --codes 600418.SH,601965.SH --start 2026-09-01 --end 2026-10-13

说明：
- historical 接口：start/end 为毫秒 Unix 时间戳，每次仅一只标的
- 涨跌幅由前收计算（接口不直接返回），入库到 market_snapshots
"""
from __future__ import annotations

import argparse
from datetime import datetime

from backend.app.db import get_conn, init_db
from backend.pipeline.adapters.fuyao import FuyaoClient


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def to_ms(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000)


class MarketCollector:
    """扶摇行情采集器。"""

    def __init__(self) -> None:
        self.client = FuyaoClient()
        init_db()

    def close(self) -> None:
        self.client.close()

    def fetch_daily(self, thscode: str, start: str, end: str) -> int:
        """抓取单只标的日 K 并入库，返回写入行数。"""
        data = self.client.get(
            "/api/a-share/prices/historical",
            {
                "thscode": thscode,
                "interval": "1d",
                "start": to_ms(start),
                "end": to_ms(end),
                "adjust": "forward",
            },
        )
        items = data.get("item") or []
        stamp = now_iso()
        rows = 0
        prev_close: float | None = None
        with get_conn() as conn:
            for bar in items:
                trade_date = datetime.fromtimestamp(bar["date_ms"] / 1000).strftime("%Y-%m-%d")
                close = bar.get("close_price")
                change_pct = None
                if prev_close not in (None, 0) and close is not None:
                    change_pct = round((close / prev_close - 1) * 100, 4)
                conn.execute(
                    """
                    INSERT INTO market_snapshots (
                        thscode, trade_date, open, high, low, close, prev_close,
                        change_pct, volume, turnover, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(thscode, trade_date) DO UPDATE SET
                        open=excluded.open, high=excluded.high, low=excluded.low,
                        close=excluded.close, prev_close=excluded.prev_close,
                        change_pct=excluded.change_pct,
                        volume=excluded.volume, turnover=excluded.turnover,
                        fetched_at=excluded.fetched_at
                    """,
                    (
                        thscode, trade_date,
                        bar.get("open_price"), bar.get("high_price"), bar.get("low_price"),
                        close, prev_close, change_pct,
                        bar.get("volume"), bar.get("turnover"), stamp,
                    ),
                )
                rows += 1
                prev_close = close
        return rows

    def fetch_today_snapshot(self, thscodes: list[str]) -> int:
        """抓取当日快照并写入今日行（最新价即当日收盘/实时价）。"""
        data = self.client.snapshot(thscodes)
        items = data.get("item") or []
        stamp = now_iso()
        today = datetime.now().strftime("%Y-%m-%d")
        with get_conn() as conn:
            for item in items:
                conn.execute(
                    """
                    INSERT INTO market_snapshots (
                        thscode, trade_date, open, high, low, close, prev_close,
                        change_pct, volume, turnover, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(thscode, trade_date) DO UPDATE SET
                        open=excluded.open, high=excluded.high, low=excluded.low,
                        close=excluded.close, prev_close=excluded.prev_close,
                        change_pct=excluded.change_pct,
                        volume=excluded.volume, turnover=excluded.turnover,
                        fetched_at=excluded.fetched_at
                    """,
                    (
                        item["thscode"], today,
                        item.get("open_price"), item.get("high_price"), item.get("low_price"),
                        item.get("last_price"), item.get("prev_price"),
                        item.get("price_change_ratio_pct"),
                        item.get("volume"), item.get("turnover"), stamp,
                    ),
                )
        return len(items)


def main() -> None:
    parser = argparse.ArgumentParser(description="扶摇行情采集（日 K + 当日快照）")
    parser.add_argument("--codes", required=True, help="逗号分隔 thscode，如 600418.SH,601965.SH")
    parser.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    args = parser.parse_args()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    collector = MarketCollector()
    try:
        for code in codes:
            try:
                rows = collector.fetch_daily(code, args.start, args.end)
                print(f"[{code}] 日K 写入 {rows} 行")
            except Exception as exc:  # noqa: BLE001 单标的失败不影响其他
                print(f"[{code}] 日K 失败: {type(exc).__name__} {exc}")
        try:
            count = collector.fetch_today_snapshot(codes)
            print(f"[快照] 写入 {count} 只标的今日快照")
        except Exception as exc:  # noqa: BLE001
            print(f"[快照] 失败: {type(exc).__name__} {exc}")
    finally:
        collector.close()


if __name__ == "__main__":
    main()
