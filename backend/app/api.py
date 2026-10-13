"""EventLens HTTP API：供前端消费的事件数据接口（读取 SQLite 预置数据）。

设计原则：
- 只读接口直接查库；通知已读、关注标的管理为唯一写操作
- 所有结论数据附证据引用与权重明细，前端可展开溯源
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app.db import get_conn, init_db
from backend.pipeline import runner
from backend.pipeline.adapters.ifind import IfindClient
from backend.pipeline.collector import extract_codes, parse_flex, sha1_text
from backend.pipeline.hot import collect_hot_topics, derive_keywords, latest_batch

router = APIRouter(prefix="/api")

# 沪深300 作为事件影响验证的基准
BENCHMARK = {"thscode": "000300.SH", "name": "沪深300"}


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


# --------------------------------------------------------------------------
# 轻量后台任务：材料导入 / 热点追踪（本地单机场景，内存态即可）
# --------------------------------------------------------------------------
JOBS: dict[str, dict] = {}


def _spawn_job(kind: str, fn, *args) -> str:
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "id": job_id, "kind": kind, "status": "running", "created_at": _now_iso(),
    }

    def _work() -> None:
        try:
            JOBS[job_id].update(status="done", result=fn(*args))
        except Exception as exc:  # noqa: BLE001 后台任务异常需可见（安全失败）
            JOBS[job_id].update(status="failed", error=f"{type(exc).__name__}: {exc}")

    threading.Thread(target=_work, daemon=True).start()
    return job_id


@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已结束")
    return job


def _rows(conn, sql: str, *args) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _latest_snapshot(conn, thscode: str) -> dict | None:
    row = conn.execute(
        "SELECT trade_date, close, change_pct FROM market_snapshots "
        "WHERE thscode = ? ORDER BY trade_date DESC LIMIT 1",
        (thscode,),
    ).fetchone()
    return dict(row) if row else None


def _doc_id_of(raw) -> int | None:
    try:
        return int(str(raw).strip().lstrip("#"))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# 概览
# --------------------------------------------------------------------------
@router.get("/overview")
def overview() -> dict:
    conn = get_conn()
    try:
        def scalar(sql: str) -> int:
            return conn.execute(sql).fetchone()[0]

        return {
            "events_total": scalar("SELECT COUNT(*) FROM events WHERE tracked = 1"),
            "untracked_total": scalar("SELECT COUNT(*) FROM events WHERE tracked = 0"),
            "disputed_total": scalar(
                "SELECT COUNT(*) FROM events WHERE current_status = 'disputed' AND tracked = 1"
            ),
            "unread_notifications": scalar(
                "SELECT COUNT(*) FROM notifications n JOIN events e ON n.event_id = e.id "
                "WHERE n.read = 0 AND e.tracked = 1"
            ),
            "watchlist_total": scalar("SELECT COUNT(*) FROM watchlist"),
            "documents_total": scalar("SELECT COUNT(*) FROM source_documents"),
            "relevant_documents": scalar(
                "SELECT COUNT(*) FROM document_analysis WHERE relevance = 'relevant'"
            ),
            "noise_documents": scalar(
                "SELECT COUNT(*) FROM document_analysis WHERE relevance = 'noise'"
            ),
            "claims_total": scalar("SELECT COUNT(*) FROM claims"),
            "evidence_total": scalar("SELECT COUNT(*) FROM evidence_links"),
            "quote_verified": scalar(
                "SELECT COUNT(*) FROM evidence_links "
                "WHERE json_extract(weight_detail, '$.quote_verified') = 1"
            ),
            "versions_total": scalar("SELECT COUNT(*) FROM event_versions"),
        }
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 事件
# --------------------------------------------------------------------------
@router.get("/events")
def list_events(
    status: str | None = None,
    q: str | None = None,
    thscode: str | None = None,
    include_untracked: int = 0,
) -> dict:
    conn = get_conn()
    try:
        events = conn.execute(
            "SELECT * FROM events ORDER BY datetime(last_substantive_update_at) DESC, id DESC"
        ).fetchall()
        keyword = (q or "").strip()
        code = (thscode or "").strip().upper() or None
        items = []
        for e in events:
            if not include_untracked and not e["tracked"]:
                continue
            if status and e["current_status"] != status:
                continue
            if keyword and keyword not in (
                f"{e['title']} {e['conclusion']} {e['subject_entities'] or ''}"
            ):
                continue
            if code:
                hit = conn.execute(
                    "SELECT 1 FROM security_relations WHERE event_id = ? AND thscode = ?",
                    (e["id"], code),
                ).fetchone()
                if hit is None:
                    continue
            eid = e["id"]
            counts = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM claims WHERE event_id = ?) AS claims_count,
                    (SELECT COUNT(*) FROM evidence_links l JOIN claims c ON l.claim_id = c.id
                     WHERE c.event_id = ?) AS evidence_count,
                    (SELECT COUNT(*) FROM event_versions WHERE event_id = ?) AS versions_count,
                    (SELECT COUNT(*) FROM notifications WHERE event_id = ?) AS notifications_count,
                    (SELECT COUNT(*) FROM notifications WHERE event_id = ? AND read = 0) AS unread_count
                """,
                (eid, eid, eid, eid, eid),
            ).fetchone()
            stocks = _rows(
                conn,
                "SELECT thscode, stock_name, relationship_type, relevance_level "
                "FROM security_relations WHERE event_id = ?",
                eid,
            )
            for stock in stocks:
                stock["snapshot"] = _latest_snapshot(conn, stock["thscode"])
            latest_notification = conn.execute(
                "SELECT id, type, severity, title, body, diff_summary, created_at, read "
                "FROM notifications WHERE event_id = ? ORDER BY id DESC LIMIT 1",
                (eid,),
            ).fetchone()
            documents = conn.execute(
                "SELECT COUNT(*) FROM source_documents d "
                "JOIN document_analysis a ON a.document_id = d.id "
                "WHERE a.scope = ? AND a.relevance = 'relevant'",
                (e["scope"],),
            ).fetchone()[0]
            items.append(
                {
                    "id": eid,
                    "title": e["title"],
                    "scope": e["scope"],
                    "event_type": e["event_type"],
                    "status": e["current_status"],
                    "tracked": e["tracked"],
                    "conclusion": e["conclusion"],
                    "uncertainty": e["uncertainty"],
                    "last_update": e["last_substantive_update_at"],
                    "documents_count": documents,
                    "stocks": stocks,
                    "latest_notification": dict(latest_notification)
                    if latest_notification
                    else None,
                    **dict(counts),
                }
            )
        return {"items": items}
    finally:
        conn.close()


@router.get("/events/{event_id}")
def get_event(event_id: int) -> dict:
    conn = get_conn()
    try:
        event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if event is None:
            raise HTTPException(status_code=404, detail="事件不存在")
        scope = event["scope"]

        claims_out = []
        for c in conn.execute(
            """
            SELECT * FROM claims WHERE event_id = ?
            ORDER BY CASE evidence_status
                WHEN 'contradicted' THEN 0 WHEN 'supported' THEN 1
                WHEN 'disputed' THEN 0 ELSE 2 END, id
            """,
            (event_id,),
        ).fetchall():
            links = conn.execute(
                """
                SELECT l.*, d.title AS doc_title, d.source_type, d.source_name,
                       d.published_at, d.source_url, d.publisher
                FROM evidence_links l
                JOIN source_documents d ON l.document_id = d.id
                WHERE l.claim_id = ?
                ORDER BY l.weight_score DESC
                """,
                (c["id"],),
            ).fetchall()
            claims_out.append(
                {
                    "id": c["id"],
                    "text": c["claim_text"],
                    "claim_type": c["claim_type"],
                    "evidence_status": c["evidence_status"],
                    "first_seen_at": c["first_seen_at"],
                    "evidences": [
                        {
                            "id": l["id"],
                            "document_id": l["document_id"],
                            "relation": l["relation"],
                            "quote": l["quote"],
                            "reason": l["reason"],
                            "weight": l["weight_score"],
                            "weight_detail": json.loads(l["weight_detail"])
                            if l["weight_detail"]
                            else None,
                            "document": {
                                "id": l["document_id"],
                                "title": l["doc_title"],
                                "source_type": l["source_type"],
                                "source_name": l["source_name"],
                                "published_at": l["published_at"],
                                "source_url": l["source_url"],
                                "publisher": l["publisher"],
                            },
                        }
                        for l in links
                    ],
                }
            )

        versions_out = []
        for v in conn.execute(
            "SELECT * FROM event_versions WHERE event_id = ? ORDER BY version_no",
            (event_id,),
        ).fetchall():
            trigger_docs = []
            for raw in json.loads(v["trigger_document_ids"] or "[]"):
                did = _doc_id_of(raw)
                if did is None:
                    continue
                doc = conn.execute(
                    "SELECT id, title, published_at, source_type FROM source_documents WHERE id = ?",
                    (did,),
                ).fetchone()
                if doc:
                    trigger_docs.append(dict(doc))
            versions_out.append(
                {
                    "id": v["id"],
                    "version_no": v["version_no"],
                    "created_at": v["created_at"],
                    "change_type": v["change_type"],
                    "status_before": v["status_before"],
                    "status_after": v["status_after"],
                    "reason": v["reason"],
                    "trigger_documents": trigger_docs,
                }
            )

        documents = _rows(
            conn,
            """
            SELECT d.id, d.title, d.source_type, d.source_name, d.publisher,
                   d.published_at, d.first_fetched_at, d.source_url,
                   d.duplicate_of, d.source_cluster_id, d.data_origin
            FROM source_documents d
            JOIN document_analysis a ON a.document_id = d.id
            WHERE a.scope = ? AND a.relevance = 'relevant'
            ORDER BY d.published_at, d.id
            """,
            scope,
        )
        noise_count = conn.execute(
            "SELECT COUNT(*) FROM document_analysis WHERE scope = ? AND relevance = 'noise'",
            (scope,),
        ).fetchone()[0]

        relations_out = []
        for r in conn.execute(
            "SELECT * FROM security_relations WHERE event_id = ?", (event_id,)
        ).fetchall():
            rel = dict(r)
            rel["market"] = _latest_snapshot(conn, r["thscode"])
            relations_out.append(rel)

        return {
            "event": dict(event),
            "claims": claims_out,
            "versions": versions_out,
            "documents": documents,
            "relations": relations_out,
            "noise_count": noise_count,
        }
    finally:
        conn.close()


@router.post("/events/{event_id}/untrack")
def untrack_event(event_id: int) -> dict:
    """停止追踪事件（历史数据保留，可随时重新追踪）。"""
    conn = get_conn()
    try:
        cur = conn.execute(
            "UPDATE events SET tracked = 0, updated_at = ? WHERE id = ?",
            (_now_iso(), event_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="事件不存在")
        return {"ok": True, "tracked": 0}
    finally:
        conn.close()


@router.post("/events/{event_id}/track")
def track_event(event_id: int) -> dict:
    """重新追踪已停止的事件。"""
    conn = get_conn()
    try:
        cur = conn.execute(
            "UPDATE events SET tracked = 1, updated_at = ? WHERE id = ?",
            (_now_iso(), event_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="事件不存在")
        return {"ok": True, "tracked": 1}
    finally:
        conn.close()


def _window_stats(bars: list[dict], bench: list[dict], v1_date: str, v_last_date: str) -> dict:
    """事件窗口表现：自 V1 与自最新版本两段区间收益（含相对沪深300 超额）。"""

    def window_return(series: list[dict], start_date: str) -> float | None:
        base = None
        latest = None
        for bar in series:
            if bar["trade_date"] < start_date:
                base = bar["close"]
            else:
                latest = bar["close"]
        if base in (None, 0) or latest is None:
            return None
        return round((latest / base - 1) * 100, 2)

    def pack(start_date: str) -> dict:
        ret = window_return(bars, start_date)
        bench_ret = window_return(bench, start_date)
        return {
            "date": start_date,
            "return_pct": ret,
            "benchmark_return_pct": bench_ret,
            "excess_pct": None
            if (ret is None or bench_ret is None)
            else round(ret - bench_ret, 2),
        }

    return {"since_v1": pack(v1_date), "since_latest_version": pack(v_last_date)}


@router.get("/events/{event_id}/market")
def event_market(event_id: int) -> dict:
    conn = get_conn()
    try:
        event = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
        if event is None:
            raise HTTPException(status_code=404, detail="事件不存在")
        markers = [
            {
                "date": v["created_at"][:10],
                "label": f"V{v['version_no']}",
                "change_type": v["change_type"],
                "detail": v["reason"],
            }
            for v in conn.execute(
                "SELECT * FROM event_versions WHERE event_id = ? ORDER BY version_no",
                (event_id,),
            ).fetchall()
        ]
        series = []
        for r in conn.execute(
            "SELECT thscode, stock_name FROM security_relations WHERE event_id = ?",
            (event_id,),
        ).fetchall():
            bars = _rows(
                conn,
                "SELECT trade_date, open, high, low, close, change_pct, volume, turnover "
                "FROM market_snapshots WHERE thscode = ? ORDER BY trade_date",
                r["thscode"],
            )
            series.append(
                {
                    "thscode": r["thscode"],
                    "stock_name": r["stock_name"],
                    "bars": bars,
                }
            )
        bench_bars = _rows(
            conn,
            "SELECT trade_date, close FROM market_snapshots WHERE thscode = ? ORDER BY trade_date",
            BENCHMARK["thscode"],
        )
        stats: dict[str, dict] = {}
        if markers and bench_bars:
            v1_date = markers[0]["date"]
            v_last_date = markers[-1]["date"]
            for s in series:
                stats[s["thscode"]] = _window_stats(s["bars"], bench_bars, v1_date, v_last_date)
        return {
            "markers": markers,
            "series": series,
            "stats": stats,
            "benchmark": BENCHMARK,
        }
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 原始材料（溯源）
# --------------------------------------------------------------------------
@router.get("/documents/{doc_id}")
def get_document(doc_id: int) -> dict:
    conn = get_conn()
    try:
        doc = conn.execute("SELECT * FROM source_documents WHERE id = ?", (doc_id,)).fetchone()
        if doc is None:
            raise HTTPException(status_code=404, detail="材料不存在")
        analysis = conn.execute(
            "SELECT * FROM document_analysis WHERE document_id = ?", (doc_id,)
        ).fetchone()
        return {"document": dict(doc), "analysis": dict(analysis) if analysis else None}
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 通知
# --------------------------------------------------------------------------
@router.get("/notifications")
def list_notifications() -> dict:
    conn = get_conn()
    try:
        rows = _rows(
            conn,
            """
            SELECT n.*, e.title AS event_title, e.current_status, e.scope
            FROM notifications n JOIN events e ON n.event_id = e.id
            WHERE e.tracked = 1
            ORDER BY n.id DESC
            """,
        )
        return {"items": rows}
    finally:
        conn.close()


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int) -> dict:
    conn = get_conn()
    try:
        cur = conn.execute(
            "UPDATE notifications SET read = 1 WHERE id = ?", (notification_id,)
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="通知不存在")
        return {"ok": True}
    finally:
        conn.close()


@router.post("/notifications/read-all")
def mark_all_read() -> dict:
    conn = get_conn()
    try:
        cur = conn.execute("UPDATE notifications SET read = 1 WHERE read = 0")
        conn.commit()
        return {"ok": True, "updated": cur.rowcount}
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 材料收件箱（新材料导入动线）
# --------------------------------------------------------------------------
@router.get("/inbox")
def inbox_list() -> dict:
    conn = get_conn()
    try:
        items = _rows(conn, "SELECT * FROM inbox_items ORDER BY id")
        pending = sum(1 for i in items if i["status"] == "pending")
        return {"items": items, "pending": pending}
    finally:
        conn.close()


class InboxImportRequest(BaseModel):
    item_ids: list[int] | None = None  # 为空表示导入全部待处理材料


def _import_inbox_items(item_ids: list[int] | None) -> dict:
    """导入收件箱材料 → 入库（哈希去重）→ 增量分析管线（对应事件范围）。"""
    init_db()
    conn = get_conn()
    if item_ids:
        placeholders = ",".join("?" * len(item_ids))
        rows = conn.execute(
            f"SELECT * FROM inbox_items WHERE id IN ({placeholders}) AND status = 'pending'",
            item_ids,
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM inbox_items WHERE status = 'pending'").fetchall()

    results = []
    for item in rows:
        content_hash = sha1_text((item["title"] or "") + "\n" + (item["content"] or ""))
        duplicate = conn.execute(
            "SELECT id, title FROM source_documents WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if duplicate:
            note = (
                f"与库内材料 #{duplicate['id']}（{(duplicate['title'] or '')[:26]}…）"
                "内容重复（哈希一致），未产生新增"
            )
            conn.execute(
                "UPDATE inbox_items SET status = 'duplicate', result_note = ? WHERE id = ?",
                (note, item["id"]),
            )
            results.append({"inbox_id": item["id"], "status": "duplicate", "note": note})
            continue
        stamp = _now_iso()
        cur = conn.execute(
            """
            INSERT INTO source_documents (
                source_type, source_name, publisher, title, content, source_url,
                stock_codes, published_at, first_fetched_at, last_fetched_at,
                content_hash, data_origin
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'real')
            """,
            (
                item["source_type"], item["source_name"], item["publisher"], item["title"],
                item["content"], item["source_url"], item["stock_codes"], item["published_at"],
                stamp, stamp, content_hash,
            ),
        )
        doc_id = cur.lastrowid
        note = f"已入库为材料 #{doc_id}，进入增量分析管线"
        conn.execute(
            "UPDATE inbox_items SET status = 'imported', result_note = ? WHERE id = ?",
            (note, item["id"]),
        )
        results.append(
            {"inbox_id": item["id"], "status": "imported", "document_id": doc_id, "note": note}
        )
    conn.commit()
    conn.close()

    # 归档新文档（相关性判定），确定需要增量处理的范围
    runner.preprocess_new_docs()
    conn = get_conn()
    scopes: set[str] = set()
    for r in results:
        if r.get("document_id"):
            row = conn.execute(
                "SELECT scope, relevance FROM document_analysis WHERE document_id = ?",
                (r["document_id"],),
            ).fetchone()
            if row and row["scope"]:
                scopes.add(row["scope"])
    conn.close()

    pipeline_stats = {scope: runner.run_incremental(scope) for scope in sorted(scopes)}
    return {
        "items": results,
        "pipeline": pipeline_stats,
        "imported_count": sum(1 for r in results if r["status"] == "imported"),
        "duplicate_count": sum(1 for r in results if r["status"] == "duplicate"),
    }


@router.post("/inbox/import")
def inbox_import(payload: InboxImportRequest) -> dict:
    job_id = _spawn_job("import-inbox", _import_inbox_items, payload.item_ids)
    return {"job_id": job_id}


# --------------------------------------------------------------------------
# 热点榜（定时刷新的榜单 + 一键追踪为事件）
# --------------------------------------------------------------------------
@router.get("/hot")
def hot_list() -> dict:
    conn = get_conn()
    try:
        batch = latest_batch(conn)
        if batch is None:
            return {"batch_at": None, "previous_batch": None, "items": []}
        prev = conn.execute(
            "SELECT batch_at FROM hot_topics WHERE batch_at < ? ORDER BY batch_at DESC LIMIT 1",
            (batch,),
        ).fetchone()
        prev_ranks: dict[str, int] = {}
        if prev:
            for row in conn.execute(
                "SELECT title, rank FROM hot_topics WHERE batch_at = ?", (prev["batch_at"],)
            ):
                prev_ranks[row["title"]] = row["rank"]
        items = _rows(
            conn, "SELECT * FROM hot_topics WHERE batch_at = ? ORDER BY rank", batch
        )
        for item in items:
            prev_rank = prev_ranks.get(item["title"])
            item["movement"] = None if prev_rank is None else prev_rank - item["rank"]
            item["related_codes"] = json.loads(item["related_codes"] or "[]")
            item["related_names"] = json.loads(item["related_names"] or "[]")
            item["summary"] = (item["summary"] or "")[:160]
        return {
            "batch_at": batch,
            "previous_batch": prev["batch_at"] if prev else None,
            "items": items,
        }
    finally:
        conn.close()


@router.post("/hot/refresh")
def hot_refresh() -> dict:
    try:
        info = collect_hot_topics()
    except Exception as exc:  # noqa: BLE001 数据源不可用时给出明确降级提示（不伪造数据）
        raise HTTPException(
            status_code=502, detail=f"刷新失败（数据源不可用）：{exc}"
        ) from exc
    return {"ok": True, **info}


def _track_topic(topic_id: int) -> dict:
    """把热点追踪为事件：创建范围 → 采集材料 → 增量管线 → 生成事件。"""
    init_db()
    conn = get_conn()
    topic = conn.execute("SELECT * FROM hot_topics WHERE id = ?", (topic_id,)).fetchone()
    if topic is None:
        raise ValueError("热点不存在")
    if topic["tracked_scope"]:
        return {
            "scope": topic["tracked_scope"],
            "event_id": topic["tracked_event_id"],
            "already": True,
        }

    scope_key = f"hot-{topic_id}"
    keywords = derive_keywords(topic["title"])
    # 检索词：优先使用不含数字的实词（前 3 个），避免整条标题作为检索词导致语义偏移
    query_tokens = [k for k in keywords if not any(ch.isdigit() for ch in k)][:3]
    query_news = " ".join(query_tokens) or topic["title"][:30]
    conn.execute(
        "INSERT OR REPLACE INTO scopes (key, title, keywords, source) VALUES (?, ?, ?, 'hot')",
        (scope_key, topic["title"][:60], json.dumps(keywords, ensure_ascii=False)),
    )
    conn.commit()

    # 采集材料：新闻 + 公告双通道
    client = IfindClient()
    try:
        news_text = client.call_text(
            "news", "search_news",
            {"query": query_news, "time_start": "2026-09-15", "time_end": "2026-10-13", "size": 10},
        )
        try:
            notice_text = client.call_text(
                "news", "search_notice",
                {"query": query_news, "time_start": "2026-09-15", "time_end": "2026-10-13", "size": 6},
            )
        except Exception:  # noqa: BLE001 公告通道失败不影响新闻通道
            notice_text = ""
    finally:
        client.close()

    now = _now_iso()
    collected = 0

    def _push_docs(records, kind: str) -> None:
        nonlocal collected
        for rec in records:
            doc_title = (rec.get("资讯标题") or rec.get("公告标题") or "").strip()
            doc_content = (rec.get("资讯内容") or rec.get("公告片段内容") or "").strip()
            if not doc_title or not doc_content:
                continue
            content_hash = sha1_text(doc_title + "\n" + doc_content)
            if conn.execute(
                "SELECT id FROM source_documents WHERE content_hash = ?", (content_hash,)
            ).fetchone():
                continue
            codes = extract_codes(doc_title, doc_content)
            cur = conn.execute(
                """
                INSERT INTO source_documents (
                    source_type, source_name, publisher, title, content, source_url,
                    stock_codes, published_at, first_fetched_at, last_fetched_at,
                    content_hash, data_origin
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'real')
                """,
                (
                    "公告" if kind == "notice" else "新闻",
                    rec.get("信息来源") or ("公司公告" if kind == "notice" else "iFinD新闻"),
                    rec.get("信息来源"), doc_title, doc_content, rec.get("URL"),
                    json.dumps(codes, ensure_ascii=False), rec.get("日期"),
                    now, now, content_hash,
                ),
            )
            doc_id = cur.lastrowid
            conn.execute(
                """
                INSERT INTO document_analysis (document_id, scope, relevance, extracted, updated_at)
                VALUES (?, ?, 'relevant', 0, ?)
                """,
                (doc_id, scope_key, now),
            )
            collected += 1

    _push_docs(parse_flex(news_text), "news")
    if notice_text:
        _push_docs(parse_flex(notice_text), "notice")
    conn.commit()
    conn.close()

    stats = runner.run_incremental(scope_key)

    conn = get_conn()
    conn.execute(
        "UPDATE hot_topics SET tracked_scope = ?, tracked_event_id = ? WHERE id = ?",
        (scope_key, stats["event_id"], topic_id),
    )
    conn.commit()
    conn.close()
    return {"scope": scope_key, "materials_collected": collected, **stats}


@router.post("/hot/{topic_id}/track")
def hot_track(topic_id: int) -> dict:
    conn = get_conn()
    try:
        exists = conn.execute("SELECT id FROM hot_topics WHERE id = ?", (topic_id,)).fetchone()
    finally:
        conn.close()
    if exists is None:
        raise HTTPException(status_code=404, detail="热点不存在")
    job_id = _spawn_job("track-hot", _track_topic, topic_id)
    return {"job_id": job_id}


@router.post("/hot/{topic_id}/untrack")
def hot_untrack(topic_id: int) -> dict:
    """取消热点追踪：解除榜单关联，并停止追踪由其生成的事件（历史保留）。"""
    conn = get_conn()
    try:
        topic = conn.execute("SELECT * FROM hot_topics WHERE id = ?", (topic_id,)).fetchone()
        if topic is None:
            raise HTTPException(status_code=404, detail="热点不存在")
        if topic["tracked_event_id"]:
            conn.execute(
                "UPDATE events SET tracked = 0, updated_at = ? WHERE id = ?",
                (_now_iso(), topic["tracked_event_id"]),
            )
        conn.execute(
            "UPDATE hot_topics SET tracked_scope = NULL, tracked_event_id = NULL WHERE id = ?",
            (topic_id,),
        )
        conn.commit()
        return {"ok": True, "event_id": topic["tracked_event_id"]}
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 关注标的
# --------------------------------------------------------------------------
class WatchlistItem(BaseModel):
    thscode: str
    stock_name: str | None = None


@router.get("/watchlist")
def get_watchlist() -> dict:
    conn = get_conn()
    try:
        items = []
        for w in conn.execute("SELECT * FROM watchlist ORDER BY added_at").fetchall():
            events_count = conn.execute(
                "SELECT COUNT(DISTINCT event_id) FROM security_relations WHERE thscode = ?",
                (w["thscode"],),
            ).fetchone()[0]
            items.append(
                {
                    **dict(w),
                    "snapshot": _latest_snapshot(conn, w["thscode"]),
                    "events_count": events_count,
                }
            )
        return {"items": items}
    finally:
        conn.close()


@router.post("/watchlist")
def add_watchlist(item: WatchlistItem) -> dict:
    code = item.thscode.strip().upper()
    if not re.match(r"^\d{6}\.(SH|SZ|BJ)$", code):
        raise HTTPException(status_code=400, detail="代码格式应为 600418.SH / 000001.SZ / 430001.BJ")
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO watchlist (thscode, stock_name) VALUES (?, ?)
            ON CONFLICT(thscode) DO UPDATE SET
                stock_name = COALESCE(excluded.stock_name, watchlist.stock_name)
            """,
            (code, (item.stock_name or "").strip() or None),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/watchlist/{thscode}")
def remove_watchlist(thscode: str) -> dict:
    conn = get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM watchlist WHERE thscode = ?", (thscode.strip().upper(),)
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="标的未在关注列表中")
        return {"ok": True}
    finally:
        conn.close()
