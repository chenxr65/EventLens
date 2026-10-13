"""热点榜：采集（iFinD 热点事件资讯 → hot_topics 批次快照）与关键词派生。"""
from __future__ import annotations

import json
import re
from datetime import datetime

from backend.app.db import get_conn, init_db
from backend.pipeline.adapters.ifind import IfindClient
from backend.pipeline.collector import extract_codes, parse_flex

# 关键词过滤：过于泛化、无法定位事件的词
STOPWORDS = {
    "公司", "汽车", "记者", "报道", "消息", "今日", "近日", "昨日", "亿元", "万元",
    "发布", "回应", "市场", "股价", "涨停", "跌停", "中国", "全国", "表示", "称",
    "大消息", "重磅", "突发", "最新", "重要", "全球", "首个", "预计", "同比", "环比",
}


def derive_keywords(title: str, extra: list[str] | None = None, cap: int = 8) -> list[str]:
    """标题 → 事件范围关键词（去停用词、去重、限量）。"""
    tokens = re.split(r"[\s\W，。！？：；、“”（）【】《》\-—/|]+", title or "")
    result: list[str] = []
    for token in tokens:
        token = token.strip()
        if len(token) < 2 or token in STOPWORDS or token in result:
            continue
        result.append(token)
        if len(result) >= cap:
            break
    for item in extra or []:
        if item and item not in result:
            result.append(item)
    return result


def collect_hot_topics(size: int = 20) -> dict:
    """拉取一次热点榜并落库为新批次，返回批次信息。联网失败时抛异常，由调用方降级处理。"""
    init_db()
    client = IfindClient()
    try:
        text = client.call_text(
            "news", "search_trending_news", {"time_scope": "24小时", "size": size}
        )
    finally:
        client.close()
    records = parse_flex(text)

    conn = get_conn()
    watch_names = [
        r["stock_name"]
        for r in conn.execute(
            "SELECT stock_name FROM watchlist WHERE stock_name IS NOT NULL AND stock_name != ''"
        )
    ]
    batch_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    inserted = 0
    seen: set[str] = set()
    rank = 0
    for rec in records:
        title = (rec.get("资讯标题") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        rank += 1
        content = rec.get("资讯内容") or ""
        codes = extract_codes(title, content)
        names = [n for n in watch_names if n and n in (title + content)]
        conn.execute(
            """
            INSERT INTO hot_topics (batch_at, rank, title, heat, source, summary, url,
                                    related_codes, related_names)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_at, rank, title, rec.get("资讯热度"), rec.get("信息来源"),
                content[:300], rec.get("URL"),
                json.dumps(codes, ensure_ascii=False),
                json.dumps(names, ensure_ascii=False),
            ),
        )
        inserted += 1
    conn.commit()
    conn.close()
    return {"batch_at": batch_at, "count": inserted}


def latest_batch(conn) -> str | None:
    row = conn.execute("SELECT batch_at FROM hot_topics ORDER BY batch_at DESC LIMIT 1").fetchone()
    return row["batch_at"] if row else None
