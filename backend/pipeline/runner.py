"""增量管线编排：新材料导入与热点追踪共用。

流程：归档新文档（相关性判定）→ 增量 LLM 抽取 → 全文重组装 → 校验入库。
供 API 后台任务调用；所有步骤复用 analyze.py 的实现与缓存兜底。
"""
from __future__ import annotations

from backend.app.db import get_conn, init_db
from backend.pipeline import analyze
from backend.pipeline.analyze import SCOPES, load_dynamic_scopes, match_scope, now_iso


def preprocess_new_docs() -> list[int]:
    """为尚未归档的文档（无 document_analysis 行）建立相关性归档，返回处理的文档 id 列表。"""
    init_db()
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT d.id, d.title, d.content
        FROM source_documents d
        LEFT JOIN document_analysis a ON a.document_id = d.id
        WHERE a.document_id IS NULL
        """
    ).fetchall()
    handled: list[int] = []
    for doc in rows:
        scope = match_scope(doc)
        conn.execute(
            """
            INSERT INTO document_analysis (document_id, scope, relevance, noise_reason, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                doc["id"],
                scope,
                "relevant" if scope else "noise",
                None if scope else "未匹配任何关注范围的实体关键词",
                now_iso(),
            ),
        )
        handled.append(doc["id"])
    conn.commit()
    conn.close()
    return handled


def run_incremental(scope: str) -> dict:
    """对指定范围执行增量管线，返回 load 阶段统计（含 event_id）。"""
    preprocess_new_docs()
    load_dynamic_scopes()
    if scope not in SCOPES:
        raise ValueError(f"未知事件范围: {scope}")
    analyze.stage_extract(scope)
    analyze.stage_assemble(scope)
    return analyze.stage_load(scope)
