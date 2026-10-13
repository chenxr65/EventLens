"""采集器：从 iFinD 新闻/公告服务抓取材料并规范化入库 source_documents。

用法（工程根目录）：
    .venv\\Scripts\\python.exe -m backend.pipeline.collector \\
        --name 江淮汽车 --codes 600418.SH \\
        --query "江淮汽车 尊界 制动踏板" --start 2026-10-01 --end 2026-10-13

说明：
- 新闻记录字段：资讯标题 / 资讯内容 / 日期 / URL
- 公告记录字段：公告标题 / 公告片段内容 / 日期（无 URL）
- 入库前按 title+content 哈希去重；重复材料仅刷新 last_fetched_at
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime

from backend.app.db import get_conn, init_db
from backend.pipeline.adapters.ifind import IfindClient


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _loads_records(text: str) -> list:
    """解析内层 JSON 字符串；空串、无结果说明文本（非 JSON）均返回空列表。"""
    text = (text or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def parse_flex(text: str) -> list[dict]:
    """兼容新闻/公告/热点三种包裹结构，统一返回记录列表。

    - search_news:   {data: {data: "<json list>"}}
    - search_notice: {data: "<json list>"}
    - trending:      {data: {result: [ ... ]}}

    空响应（工具无结果时可能返回空文本或说明文本）按"无记录"处理。
    """
    if not text or not text.strip():
        return []
    outer = json.loads(text)
    data = outer.get("data")
    if isinstance(data, str):
        records = _loads_records(data)
    elif isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, str):
            records = _loads_records(inner)
        elif inner is not None:
            records = inner
        else:
            records = data.get("result") or []
    else:
        records = data or []
    return [r for r in records if isinstance(r, dict)]


def extract_codes(*texts: str) -> list[str]:
    """从标题/正文中提取 6 位股票代码（形如 (301111)），并规范化为 thscode。"""
    codes: set[str] = set()
    for text in texts:
        for match in re.finditer(r"[（(](\d{6})[)）]", text or ""):
            digit = match.group(1)
            if digit.startswith(("6", "9")):
                codes.add(f"{digit}.SH")
            elif digit.startswith(("0", "3")):
                codes.add(f"{digit}.SZ")
            elif digit.startswith(("4", "8")):
                codes.add(f"{digit}.BJ")
            else:
                codes.add(digit)
    return sorted(codes)


class NewsCollector:
    """iFinD 新闻/公告采集器。"""

    def __init__(self) -> None:
        self.client = IfindClient()
        init_db()

    def close(self) -> None:
        self.client.close()

    def normalize(self, rec: dict, kind: str, target_codes: list[str]) -> dict | None:
        """记录 -> source_documents 行数据；工具说明类记录返回 None。"""
        title = (rec.get("资讯标题") or rec.get("公告标题") or "").strip()
        content = (rec.get("资讯内容") or rec.get("公告片段内容") or "").strip()
        if not title or not content:
            return None
        source_name = rec.get("信息来源") or ("公司公告" if kind == "notice" else "iFinD新闻")
        codes = sorted(set(target_codes) | set(extract_codes(title, content)))
        return {
            "source_type": "公告" if kind == "notice" else "新闻",
            "source_name": source_name,
            "publisher": rec.get("信息来源"),
            "title": title,
            "content": content,
            "source_url": rec.get("URL"),
            "stock_codes": json.dumps(codes, ensure_ascii=False),
            "published_at": rec.get("日期"),
        }

    def save(self, docs: list[dict]) -> tuple[int, int]:
        """入库并去重（title+content 哈希）。返回 (新增, 跳过重复)。"""
        inserted = skipped = 0
        stamp = now_iso()
        with get_conn() as conn:
            for doc in docs:
                content_hash = sha1_text(doc["title"] + "\n" + doc["content"])
                existed = conn.execute(
                    "SELECT id FROM source_documents WHERE content_hash = ?",
                    (content_hash,),
                ).fetchone()
                if existed:
                    conn.execute(
                        "UPDATE source_documents SET last_fetched_at = ? WHERE id = ?",
                        (stamp, existed["id"]),
                    )
                    skipped += 1
                    continue
                conn.execute(
                    """
                    INSERT INTO source_documents (
                        source_type, source_name, publisher, title, content,
                        source_url, stock_codes, published_at,
                        first_fetched_at, last_fetched_at, content_hash, data_origin
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'real')
                    """,
                    (
                        doc["source_type"], doc["source_name"], doc["publisher"],
                        doc["title"], doc["content"], doc["source_url"],
                        doc["stock_codes"], doc["published_at"],
                        stamp, stamp, content_hash,
                    ),
                )
                inserted += 1
        return inserted, skipped

    def collect(
        self, kind: str, query: str, start: str, end: str, size: int,
        target_codes: list[str],
    ) -> tuple[int, int]:
        """单次采集：kind 为 news / notice。"""
        tool = "search_notice" if kind == "notice" else "search_news"
        text = self.client.call_text(
            "news", tool,
            {"query": query, "time_start": start, "time_end": end, "size": size},
        )
        records = parse_flex(text)
        docs = [d for d in (self.normalize(r, kind, target_codes) for r in records) if d]
        return self.save(docs)


def main() -> None:
    parser = argparse.ArgumentParser(description="iFinD 新闻/公告采集")
    parser.add_argument("--name", required=True, help="主体名称（用于日志）")
    parser.add_argument("--codes", default="", help="关联标的，逗号分隔，如 600418.SH")
    parser.add_argument("--query", default="", help="新闻检索词，默认用主体名")
    parser.add_argument("--notice-query", default="", help="公告检索词，默认用主体名")
    parser.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--news-size", type=int, default=15)
    parser.add_argument("--notice-size", type=int, default=10)
    args = parser.parse_args()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    collector = NewsCollector()
    try:
        news_query = args.query or args.name
        notice_query = args.notice_query or args.name
        if args.news_size > 0:
            ni, ns = collector.collect(
                "news", news_query, args.start, args.end, args.news_size, codes
            )
            print(f"[{args.name}] 新闻: 新增 {ni} / 重复 {ns}（query={news_query}）")
        if args.notice_size > 0:
            oi, on = collector.collect(
                "notice", notice_query, args.start, args.end, args.notice_size, codes
            )
            print(f"[{args.name}] 公告: 新增 {oi} / 重复 {on}（query={notice_query}）")
    finally:
        collector.close()


if __name__ == "__main__":
    main()
