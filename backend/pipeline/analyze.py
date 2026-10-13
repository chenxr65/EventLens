"""AI 分析管线（P2）：语料清洗 → LLM 主张抽取 → 事件组装 → 校验入库。

用法（工程根目录）：
    .venv\\Scripts\\python.exe -m backend.pipeline.analyze clean     # 阶段1（确定性）
    .venv\\Scripts\\python.exe -m backend.pipeline.analyze extract   # 阶段2（LLM 抽取）
    .venv\\Scripts\\python.exe -m backend.pipeline.analyze load      # 阶段3（校验+入库）

设计原则（对应 EventLens 方案 §10.3）：
- 规则/代码负责：持久化、去重、版本管理、状态约束、校验
- LLM 负责：理解文本、抽取主张、提出判断（任何失败回退预计算缓存）
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
from datetime import datetime
from pathlib import Path

from backend.app.db import get_conn, init_db
from backend.pipeline.collector import extract_codes
from backend.pipeline.llm import LLMClient, LLMUnavailableError

# 事件关注范围：code 仅作提示（采集时附加），相关性以实体关键词判定
SCOPES = {
    "jianghuai": {
        "title": "江淮汽车·尊界V800 制动踏板事件",
        "keywords": [
            "江淮", "尊界", "V800", "中国汽研", "制动踏板", "刹车踏板",
            "懂车帝", "鸿蒙智行", "踏板支架",
        ],
    },
    "sanxia": {
        "title": "三峡新材·收购珠海赛纬",
        "keywords": ["三峡新材", "珠海赛纬", "赛纬"],
    },
    "keruisi": {
        "title": "科瑞思·收购宏钢光电",
        "keywords": ["科瑞思", "宏钢光电"],
    },
    "yuewannianqing": {
        "title": "粤万年青·董事长传闻澄清",
        "keywords": ["粤万年青"],
    },
}


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def match_scope(row) -> str | None:
    """按实体关键词判定文档所属事件范围；未命中返回 None（噪声）。"""
    text = f"{row['title'] or ''} {row['content'] or ''}"
    for scope, cfg in SCOPES.items():
        if any(keyword in text for keyword in cfg["keywords"]):
            return scope
    return None


def load_dynamic_scopes() -> None:
    """把 scopes 表中的动态范围（热点追踪创建）合并进 SCOPES。"""
    try:
        conn = get_conn()
    except Exception:  # noqa: BLE001 数据库尚未初始化时静默跳过
        return
    try:
        for row in conn.execute("SELECT key, title, keywords FROM scopes").fetchall():
            if row["key"] not in SCOPES:
                SCOPES[row["key"]] = {
                    "title": row["title"],
                    "keywords": json.loads(row["keywords"] or "[]"),
                }
    except Exception:  # noqa: BLE001 旧库无 scopes 表时静默跳过
        pass
    finally:
        conn.close()


def _norm_title(title: str) -> str:
    return re.sub(r"[\s\W]+", "", title or "")


# 清洗报告输出路径
REPORT_PATH = Path(__file__).resolve().parents[1] / "data" / "analysis" / "clean_report.md"
# 抽取结果缓存目录（兼作 LLM 不可用时的预计算兜底）
EXTRACT_DIR = Path(__file__).resolve().parents[1] / "data" / "analysis"

EXTRACT_SYSTEM = """你是金融事件证据分析助手。对输入材料做结构化抽取，只输出 JSON，不要任何多余文字。
要求：
1. claim_type 判定：fact=原文明确陈述的可核实事实；opinion=来源的观点/评论/判断；inference=基于现有信息的推测；rumor=未经证实或已被指出不实的传闻。
2. claims：提取 1-3 条核心主张，text 为规范化表述；quote 必须逐字引用材料原文（10-60 字），不得改写或拼接。
3. entities：材料涉及的公司/机构/品牌/产品名称列表。
4. action：一句话概括材料的事件动作（如"发布第三方检测结果"）。
5. role：original=最初来源；reprint=转载复述；official=当事方官方声明；commentary=媒体评论分析；third_party=第三方机构数据/检测；netizen=网络传闻。
6. confidence：0-1 的抽取置信度。
7. relevant：材料是否直接涉及用户在【跟踪事件范围】中声明的事件。仅在顺带提及相关公司名、或属于无关文件时判为 false；不确定时判 true。
输出 JSON：{"documents":[{"id":整数,"entities":["..."],"action":"...","role":"...","claim_type":"...","claims":[{"text":"...","quote":"..."}],"confidence":0.9,"relevant":true}]}"""


def _build_extract_messages(batch, scope: str) -> list[dict]:
    parts = []
    for doc in batch:
        content = (doc["content"] or "")[:1200]
        parts.append(
            f"【材料 id={doc['id']}｜{doc['source_type']}｜{doc['published_at']}】"
            f"{doc['title']}\n{content}"
        )
    header = f"【跟踪事件范围】{SCOPES[scope]['title']}"
    return [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": f"{header}\n\n请逐条抽取以下材料：\n\n" + "\n\n".join(parts)},
    ]


# --------------------------------------------------------------------------
# 阶段 1：语料清洗（确定性，不依赖 LLM）
# --------------------------------------------------------------------------
def stage_clean() -> None:
    """相关性过滤 + 标题聚簇。

    - 相关性：实体关键词匹配（未命中判定为噪声，保留原文但退出事件链路）
    - 聚簇：同一范围内标题相似度 >= 0.82 归为同簇（source_cluster_id）
    - 重复：簇内标题归一化后完全一致/互相包含时，非最完整条目 duplicate_of 指向最完整条目
    """
    init_db()
    conn = get_conn()
    conn.execute("DELETE FROM document_analysis")
    conn.execute("UPDATE source_documents SET source_cluster_id = NULL, duplicate_of = NULL")

    docs = conn.execute(
        "SELECT id, title, content, published_at FROM source_documents "
        "ORDER BY published_at, id"
    ).fetchall()

    scope_docs: dict[str, list] = {scope: [] for scope in SCOPES}
    n_relevant = n_noise = 0
    for doc in docs:
        scope = match_scope(doc)
        reason = None if scope else "未匹配任何关注范围的实体关键词"
        conn.execute(
            """
            INSERT INTO document_analysis (document_id, scope, relevance, noise_reason, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                scope = excluded.scope,
                relevance = excluded.relevance,
                noise_reason = excluded.noise_reason,
                updated_at = excluded.updated_at
            """,
            (doc["id"], scope, "relevant" if scope else "noise", reason, now_iso()),
        )
        if scope:
            scope_docs[scope].append(doc)
            n_relevant += 1
        else:
            n_noise += 1

    report: list[str] = [f"# 清洗报告 {now_iso()}", f"相关 {n_relevant} / 噪声 {n_noise}", ""]
    for scope, items in scope_docs.items():
        clusters: list[list] = []
        for doc in items:
            norm = _norm_title(doc["title"])
            placed = False
            for cluster in clusters:
                base = _norm_title(cluster[0]["title"])
                if difflib.SequenceMatcher(None, norm, base).ratio() >= 0.82:
                    cluster.append(doc)
                    placed = True
                    break
            if not placed:
                clusters.append([doc])

        dup_count = 0
        for idx, cluster in enumerate(clusters, start=1):
            cluster_id = f"{scope}-C{idx:02d}" if len(cluster) > 1 else None
            primary = max(cluster, key=lambda r: len(r["content"] or ""))
            primary_norm = _norm_title(primary["title"])
            for doc in cluster:
                dup = None
                if len(cluster) > 1 and doc["id"] != primary["id"]:
                    norm = _norm_title(doc["title"])
                    if norm == primary_norm or norm in primary_norm or primary_norm in norm:
                        dup = primary["id"]
                        dup_count += 1
                conn.execute(
                    "UPDATE source_documents SET source_cluster_id = ?, duplicate_of = ? WHERE id = ?",
                    (cluster_id, dup, doc["id"]),
                )

        multi = [c for c in clusters if len(c) > 1]
        report.append(f"## [{scope}] {SCOPES[scope]['title']}")
        report.append(f"材料 {len(items)} 条，聚簇 {len(multi)} 组，重复指向 {dup_count} 条")
        for cluster in multi:
            report.append(f"  - {cluster[0]['title'][:60]}（{len(cluster)} 条）")
        report.append("")

    conn.commit()
    conn.close()

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(report), encoding="utf-8")
    print(f"清洗完成：相关 {n_relevant} / 噪声 {n_noise}，报告 -> {REPORT_PATH}")
    for scope, items in scope_docs.items():
        print(f"  [{scope}] {SCOPES[scope]['title']}：{len(items)} 条材料")


# --------------------------------------------------------------------------
# 阶段 2：LLM 主张抽取（带缓存兜底）
# --------------------------------------------------------------------------
def stage_extract(scope: str, limit: int | None = None) -> None:
    """对指定范围的材料做 LLM 主张抽取（增量：仅处理未抽取文档）；失败回退缓存。"""
    conn = get_conn()
    docs = conn.execute(
        """
        SELECT d.id, d.title, d.content, d.published_at, d.source_type
        FROM source_documents d
        JOIN document_analysis a ON a.document_id = d.id
        WHERE a.scope = ? AND a.relevance = 'relevant' AND d.duplicate_of IS NULL
          AND a.extracted = 0
        ORDER BY d.published_at, d.id
        """,
        (scope,),
    ).fetchall()
    if limit:
        docs = docs[:limit]
    print(f"[extract:{scope}] 待抽取 {len(docs)} 条（增量，已排除重复指向与已抽取材料）")

    cache_file = EXTRACT_DIR / f"extract_{scope}.json"
    previous: dict[str, dict] = {}
    if cache_file.exists():
        try:
            previous = (
                json.loads(cache_file.read_text(encoding="utf-8")).get("documents", {}) or {}
            )
        except (json.JSONDecodeError, OSError):
            previous = {}

    client = LLMClient()
    fresh: dict[str, dict] = {}
    model_name = "cache"

    if client.available and docs:
        try:
            batch_size = 8
            for start in range(0, len(docs), batch_size):
                batch = docs[start : start + batch_size]
                out = client.chat_json(_build_extract_messages(batch, scope), max_tokens=4000)
                for item in out.get("documents", []):
                    fresh[str(item.get("id"))] = item
                print(f"  批 {start // batch_size + 1}: 抽取 {len(batch)} 条")
            model_name = client.model
        except LLMUnavailableError as exc:
            print(f"  LLM 不可用（{exc}），回退预计算缓存")
            fresh = {}

    results = {**previous, **fresh}
    if not results:
        raise SystemExit("LLM 不可用且无缓存，无法继续（请检查 Key 或先离线生成缓存）")

    payload = {
        "scope": scope,
        "generated_at": now_iso(),
        "model": model_name,
        "documents": results,
    }
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    marked = 0
    demoted = 0
    for doc in docs:
        item = results.get(str(doc["id"]))
        if not item:
            continue
        if item.get("relevant") is False:
            # AI 语义判定为无关：从事件链路降级（原文保留）
            conn.execute(
                "UPDATE document_analysis SET relevance = 'noise', noise_reason = ?, "
                "extracted = 1, updated_at = ? WHERE document_id = ?",
                ("AI 语义判定与事件无直接关联", now_iso(), doc["id"]),
            )
            demoted += 1
        else:
            conn.execute(
                "UPDATE document_analysis SET extracted = 1, updated_at = ? WHERE document_id = ?",
                (now_iso(), doc["id"]),
            )
        marked += 1
    conn.commit()
    conn.close()
    print(
        f"[extract:{scope}] 完成 {len(results)} 条（标记 {marked}，语义无关降级 {demoted}）"
        f"，缓存 -> {cache_file}"
    )


# --------------------------------------------------------------------------
# 阶段 3：LLM 事件组装（主张归并 / 版本链 / 结论），失败回退缓存
# --------------------------------------------------------------------------
ASSEMBLY_SYSTEM = """你是金融事件分析主编。基于给定的"跟踪事件范围"与"材料摘要"，完成事件组装，只输出 JSON，不要任何多余文字。
必须遵守：
1. 严格基于给定材料，不得引入外部信息；不确定处如实标注。
2. 事实、观点、推测、传闻必须区分；转载与官方声明必须区分。
3. 主张归并：把材料摘要中的主张归并为 3-8 条核心主张：
   - text：规范化主张表述（一句话）
   - claim_type：fact=可核实事实 / opinion=观点评论 / inference=推测 / rumor=未经证实或已被否认的传闻
   - evidence_status：supported=有证据支持 / contradicted=存在相反证据 / unverified=证据不足 / superseded=已被更新材料替代 / stale=证据较旧
   - note：一句话说明证据状态判定依据
   - evidences：每项 {"doc_id": 材料编号, "relation": "supports|contradicts|qualifies|supersedes", "quote": "原文引用：必须从材料摘要中该材料【原文引用】里逐字选取连续一段（10-80字），不得改写、不得跨材料拼接"}
     官方否认应作为被否认主张的 contradicts 证据；同一主张给出 1-3 条最有力证据；每条主张都必须给出至少 1 条 evidences。
4. 版本链：按时间顺序给出 2-6 个版本，每个版本：
   - version_no（从 1 递增）、date（YYYY-MM-DD）、trigger_doc_ids（触发材料编号数组）
   - change_type：initial（首次记录）/update/denial/correction/expiry/cancellation
   - status_before（首个版本为 null）、status_after：unverified/supported/disputed/confirmed/completed/denied/corrected/stale/cancelled
   - reason：基于材料事实的一句话变化原因
5. status：当前状态，取最后一个版本的 status_after。
6. conclusion：当前结论（一段话，须区分"已确认"与"仍不确定"部分）；uncertainty：待确认问题数组（1-3 条）。
7. relations：事件与上市标的的关联：[{"thscode":"600418.SH","stock_name":"江淮汽车","relationship_type":"主体|对象|检测机构|关联方","relevance_level":"high|medium|low","impact_hypothesis":"影响假设（分析判断，不得断言涨跌）"}]
输出 JSON：{"title":"...","event_type":"...","subjects":["..."],"objects":["..."],"claims":[...],"versions":[...],"status":"...","conclusion":"...","uncertainty":[...],"relations":[...]}"""


def _event_file(scope: str) -> Path:
    return EXTRACT_DIR / f"event_{scope}.json"


def _scope_docs(conn, scope: str) -> list:
    """范围内的相关材料（含重复指向条目，供权重计算参考）。"""
    return conn.execute(
        """
        SELECT d.* FROM source_documents d
        JOIN document_analysis a ON a.document_id = d.id
        WHERE a.scope = ? AND a.relevance = 'relevant'
        ORDER BY d.published_at, d.id
        """,
        (scope,),
    ).fetchall()


def stage_assemble(scope: str) -> None:
    """生成事件组装文件 event_<scope>.json（LLM 优先，失败回退缓存）。"""
    extract_file = EXTRACT_DIR / f"extract_{scope}.json"
    if not extract_file.exists():
        raise SystemExit(f"缺少抽取缓存 {extract_file}，请先运行 extract --scope {scope}")
    extraction = json.loads(extract_file.read_text(encoding="utf-8"))["documents"]

    conn = get_conn()
    docs = _scope_docs(conn, scope)
    conn.close()
    kept = [d for d in docs if extraction.get(str(d["id"]), {}).get("relevant") is not False]
    if not kept:
        raise RuntimeError(f"[assemble:{scope}] 范围内无有效材料（均被判定为无关），拒绝生成空心事件")

    digest_parts = []
    for doc in kept:
        item = extraction.get(str(doc["id"]), {})
        claims = "；".join(c.get("text", "") for c in item.get("claims", [])[:2])
        quotes = "；".join(
            f"「{(c.get('quote') or '')[:60]}」"
            for c in item.get("claims", [])[:3]
            if c.get("quote")
        )
        digest_parts.append(
            f"#{doc['id']} [{doc['published_at']}] ({item.get('role', '?')}/"
            f"{item.get('claim_type', '?')}){doc['title'][:48]}｜主张：{claims[:90]}｜原文引用：{quotes[:200]}"
        )
    user = f"【跟踪事件范围】{SCOPES[scope]['title']}\n【材料摘要】\n" + "\n".join(digest_parts)

    event_file = _event_file(scope)
    client = LLMClient()
    data = None
    model_name = "cache"
    if client.available:
        try:
            data = client.chat_json(
                [
                    {"role": "system", "content": ASSEMBLY_SYSTEM},
                    {"role": "user", "content": user},
                ],
                max_tokens=6000,
            )
            model_name = client.model
        except LLMUnavailableError as exc:
            print(f"  LLM 不可用（{exc}），回退缓存")
    if data is None:
        if not event_file.exists():
            raise SystemExit("LLM 不可用且无事件缓存，无法继续")
        data = json.loads(event_file.read_text(encoding="utf-8"))["event"]
        print(f"  已加载缓存 {event_file.name}")

    payload = {"scope": scope, "generated_at": now_iso(), "model": model_name, "event": data}
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    event_file.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        f"[assemble:{scope}] 完成：主张 {len(data.get('claims', []))} 条 / "
        f"版本 {len(data.get('versions', []))} 个 / 状态 {data.get('status')} -> {event_file}"
    )


# --------------------------------------------------------------------------
# 阶段 4：校验与入库（引用回溯校验 + 权重计算 + 通知生成，全部确定性）
# --------------------------------------------------------------------------
# 来源权威性 R：按材料角色取值
ROLE_AUTHORITY = {
    "official": 0.95,
    "third_party": 0.85,
    "original": 0.70,
    "commentary": 0.60,
    "reprint": 0.40,
    "netizen": 0.20,
}


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def compute_weight(doc, item, relation: str, quote_ok: bool, latest_date: str) -> tuple[float, dict]:
    """证据权重 W = 0.30R + 0.20D + 0.20I + 0.15F + 0.15Q（启发式评分，非真实性概率）。"""
    role = (item or {}).get("role") or "reprint"
    authority = ROLE_AUTHORITY.get(role, 0.5)
    directness = 0.85 if relation in ("supports", "contradicts") else 0.6
    if doc["duplicate_of"] is not None:
        independence = 0.30
    elif doc["source_cluster_id"]:
        independence = 0.55
    else:
        independence = 0.75
    try:
        days = (
            datetime.strptime(latest_date, "%Y-%m-%d")
            - datetime.strptime(doc["published_at"], "%Y-%m-%d")
        ).days
    except (TypeError, ValueError):
        days = 0
    freshness = max(0.30, 1 - 0.05 * max(0, days))
    completeness = 0.85 if len(doc["content"] or "") >= 400 else 0.60
    weight = round(
        0.30 * authority + 0.20 * directness + 0.20 * independence
        + 0.15 * freshness + 0.15 * completeness,
        4,
    )
    detail = {
        "R": authority, "D": directness, "I": independence, "F": freshness, "Q": completeness,
        "role": role, "quote_verified": quote_ok,
        "note": "启发式评分，非真实性概率；引用校验为空则不得作为确认依据",
    }
    return weight, detail


def stage_load(scope: str) -> None:
    """读取事件组装文件 → 引用校验 → 写入 events/claims/evidence_links/versions/notifications。"""
    init_db()
    event_file = _event_file(scope)
    if not event_file.exists():
        raise SystemExit(f"缺少事件文件 {event_file}，请先运行 assemble --scope {scope}")
    event = json.loads(event_file.read_text(encoding="utf-8"))["event"]
    extraction = json.loads(
        (EXTRACT_DIR / f"extract_{scope}.json").read_text(encoding="utf-8")
    )["documents"]

    conn = get_conn()
    # 幂等：清除该范围旧数据
    for row in conn.execute("SELECT id FROM events WHERE scope = ?", (scope,)).fetchall():
        eid = row["id"]
        conn.execute("DELETE FROM notifications WHERE event_id = ?", (eid,))
        conn.execute("DELETE FROM security_relations WHERE event_id = ?", (eid,))
        conn.execute("DELETE FROM event_versions WHERE event_id = ?", (eid,))
        conn.execute(
            "DELETE FROM evidence_links WHERE claim_id IN (SELECT id FROM claims WHERE event_id = ?)",
            (eid,),
        )
        conn.execute("DELETE FROM claims WHERE event_id = ?", (eid,))
        conn.execute("DELETE FROM events WHERE id = ?", (eid,))

    docs = {r["id"]: r for r in conn.execute("SELECT * FROM source_documents")}
    scope_docs = _scope_docs(conn, scope)
    latest_date = max(
        (d["published_at"] for d in scope_docs if d["published_at"]),
        default=datetime.now().strftime("%Y-%m-%d"),
    )

    versions = event.get("versions", [])
    status = event.get("status") or (
        versions[-1].get("status_after") if versions else "unverified"
    )
    last_update = versions[-1].get("date") if versions else None
    cur = conn.execute(
        """
        INSERT INTO events (title, event_type, subject_entities, object_entities,
                            current_status, conclusion, uncertainty,
                            last_substantive_update_at, scope)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.get("title", "(未命名事件)"),
            event.get("event_type"),
            json.dumps(event.get("subjects", []), ensure_ascii=False),
            json.dumps(event.get("objects", []), ensure_ascii=False),
            status,
            event.get("conclusion"),
            "\n".join(event.get("uncertainty", [])),
            last_update,
            scope,
        ),
    )
    event_id = cur.lastrowid

    stats = {"claims": 0, "evidence": 0, "ok": 0, "failed": 0, "demoted": 0}
    for claim in event.get("claims", []):
        rows_for_insert = []
        for ev in claim.get("evidences", []):
            try:
                doc_id = int(str(ev.get("doc_id")).strip().lstrip("#"))
            except (TypeError, ValueError):
                continue
            doc = docs.get(doc_id)
            if doc is None:
                continue
            quote = (ev.get("quote") or "").strip()
            ok = bool(quote) and _norm_text(quote) in _norm_text(doc["content"])
            stats["evidence"] += 1
            stats["ok" if ok else "failed"] += 1
            rows_for_insert.append((doc, ev, quote, ok))

        claim_status = claim.get("evidence_status", "unverified")
        supports = [row for row in rows_for_insert if row[1].get("relation") == "supports"]
        if (
            supports
            and all(not row[3] for row in supports)
            and claim_status in ("supported", "confirmed")
        ):
            claim_status = "unverified"
            stats["demoted"] += 1
        first_seen = min(
            (row[0]["published_at"] for row in rows_for_insert if row[0]["published_at"]),
            default=None,
        )
        cur2 = conn.execute(
            """
            INSERT INTO claims (event_id, claim_text, claim_type, evidence_status, first_seen_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_id,
                claim.get("text", ""),
                claim.get("claim_type", "fact"),
                claim_status,
                first_seen,
            ),
        )
        claim_id = cur2.lastrowid
        stats["claims"] += 1

        for doc, ev, quote, ok in rows_for_insert:
            item = extraction.get(str(doc["id"]), {})
            relation = ev.get("relation", "supports")
            weight, detail = compute_weight(doc, item, relation, ok, latest_date)
            reason = (claim.get("note") or "").strip()
            if not ok:
                reason = (reason + "；引用校验失败：该证据不得作为确认依据").strip("；")
            conn.execute(
                """
                INSERT INTO evidence_links (claim_id, document_id, relation, quote, reason,
                                            weight_score, weight_detail)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim_id, doc["id"], relation, quote, reason,
                    weight, json.dumps(detail, ensure_ascii=False),
                ),
            )

    prev_version_id = None
    last_version_id = None
    n_notifications = 0
    for v in versions:
        changed = json.dumps(
            {
                "status": f"{v.get('status_before') or '—'} → {v.get('status_after')}",
                "change_type": v.get("change_type"),
            },
            ensure_ascii=False,
        )
        cur3 = conn.execute(
            """
            INSERT INTO event_versions (event_id, version_no, created_at, trigger_document_ids,
                                        previous_version_id, change_type, changed_fields,
                                        status_before, status_after, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                v.get("version_no"),
                f"{v.get('date')}T09:00:00" if v.get("date") else now_iso(),
                json.dumps(v.get("trigger_doc_ids", [])),
                prev_version_id,
                v.get("change_type"),
                changed,
                v.get("status_before"),
                v.get("status_after"),
                v.get("reason"),
            ),
        )
        vid = cur3.lastrowid
        prev_version_id = vid
        last_version_id = vid

        change_type = v.get("change_type")
        if change_type and change_type != "duplicate":
            important = change_type in ("denial", "correction", "cancellation") or (
                v.get("status_after") != v.get("status_before")
                and v.get("status_after")
                in ("confirmed", "disputed", "denied", "corrected", "cancelled")
            )
            conn.execute(
                """
                INSERT INTO notifications (event_id, version_id, type, severity, title, body,
                                           diff_summary, created_at, read)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    event_id,
                    vid,
                    change_type,
                    "important" if important else "normal",
                    f"事件状态变化：{event.get('title', '')}"
                    if change_type != "initial"
                    else f"新事件进入监控：{event.get('title', '')}",
                    (v.get("reason") or "")[:300],
                    f"{v.get('status_before') or '初始'} → {v.get('status_after')}",
                    f"{v.get('date')}T09:00:00" if v.get("date") else now_iso(),
                ),
            )
            n_notifications += 1

    conn.execute(
        "UPDATE events SET current_version_id = ? WHERE id = ?", (last_version_id, event_id)
    )

    # 主体标的兜底：材料中出现频率最高的股票代码若未被组装命中且无高相关标的，自动补充
    relations = list(event.get("relations", []))
    freq: dict[str, int] = {}
    for row in conn.execute(
        """
        SELECT d.title, d.content, d.stock_codes FROM source_documents d
        JOIN document_analysis a ON a.document_id = d.id
        WHERE a.scope = ? AND a.relevance = 'relevant' AND d.duplicate_of IS NULL
        """,
        (scope,),
    ).fetchall():
        codes: set[str] = set()
        try:
            codes |= set(json.loads(row["stock_codes"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            pass
        codes |= set(extract_codes(row["title"] or "", row["content"] or ""))
        if len(codes) > 8:  # 行业梳理类文档代码过多，不参与主体兜底统计
            continue
        for code in codes:
            freq[code] = freq.get(code, 0) + 1
    if freq:
        top_code, _top_n = max(freq.items(), key=lambda kv: kv[1])
        known = {r.get("thscode") for r in relations}
        has_main = any(r.get("relevance_level") in ("high", "medium") for r in relations)
        if top_code not in known and not has_main:
            name_row = conn.execute(
                "SELECT stock_name FROM watchlist WHERE thscode = ?", (top_code,)
            ).fetchone()
            relations.append(
                {
                    "thscode": top_code,
                    "stock_name": (name_row["stock_name"] if name_row else None) or top_code,
                    "relationship_type": "主体",
                    "relevance_level": "medium",
                    "impact_hypothesis": "按材料中出现频次自动补充的主体标的关联（组装未显式给出，待人工复核）",
                }
            )

    for rel in relations:
        conn.execute(
            """
            INSERT OR REPLACE INTO security_relations
            (event_id, thscode, stock_name, relationship_type,
             relationship_evidence, relevance_level, impact_hypothesis, impact_evidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                rel.get("thscode"),
                rel.get("stock_name"),
                rel.get("relationship_type"),
                rel.get("relationship_evidence"),
                rel.get("relevance_level"),
                rel.get("impact_hypothesis"),
                None,
            ),
        )

    conn.commit()
    conn.close()

    log_line = (
        f"- [{now_iso()}] {scope}: 事件#{event_id} 主张{stats['claims']} 证据{stats['evidence']} "
        f"（校验通过{stats['ok']}/失败{stats['failed']}/降级{stats['demoted']}） "
        f"版本{len(versions)} 通知{n_notifications}\n"
    )
    log_file = EXTRACT_DIR / "load_log.md"
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(log_line)
    print(
        f"[load:{scope}] 事件 #{event_id} 入库：主张 {stats['claims']} / 证据 {stats['evidence']}"
        f"（引用校验 {stats['ok']} 通过、{stats['failed']} 失败、主张降级 {stats['demoted']}）"
        f" / 版本 {len(versions)} / 通知 {n_notifications}"
    )
    return {
        "event_id": event_id,
        "claims": stats["claims"],
        "evidence": stats["evidence"],
        "evidence_verified": stats["ok"],
        "evidence_failed": stats["failed"],
        "claims_demoted": stats["demoted"],
        "versions": len(versions),
        "notifications": n_notifications,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="EventLens AI 分析管线")
    parser.add_argument(
        "stage", choices=["clean", "extract", "assemble", "load"], help="要执行的阶段"
    )
    parser.add_argument("--scope", default="jianghuai", choices=list(SCOPES))
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 条（调试用）")
    args = parser.parse_args()

    if args.stage == "clean":
        stage_clean()
    elif args.stage == "extract":
        stage_extract(args.scope, args.limit)
    elif args.stage == "assemble":
        stage_assemble(args.scope)
    elif args.stage == "load":
        stage_load(args.scope)


if __name__ == "__main__":
    main()
