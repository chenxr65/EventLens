"""SQLite 数据层：建表与连接管理。

表结构对应《EventLens 产品方案》第 11 节数据模型：
source_documents / events / claims / evidence_links / event_versions /
security_relations / notifications（另含 watchlist、行情缓存与同步状态表）。

时间约定：统一使用 Asia/Shanghai（UTC+8）本地时间，ISO 8601 字符串。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.app.config import settings

DB_PATH: Path = settings.project_root / "backend" / "data" / "eventlens.db"

SCHEMA = """
-- 原始材料表：保存原文、来源、链接、四类时间、正文哈希与来源链
CREATE TABLE IF NOT EXISTS source_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,             -- 公告/新闻/研报/互动/传闻/数据
    source_name TEXT,                      -- 渠道名（如 公司公告、同花顺新闻）
    publisher TEXT,                        -- 发布方（公司/媒体/机构）
    title TEXT NOT NULL,
    content TEXT,                          -- 原文或相关段落
    source_url TEXT,
    stock_codes TEXT,                      -- JSON 数组：关联标的
    event_time TEXT,                       -- 事件发生时间（未知则 NULL，不得用披露时间代替）
    event_time_precision TEXT,             -- day/hour/minute/unknown
    published_at TEXT,                     -- 披露时间
    source_updated_at TEXT,                -- 来源侧更新时间
    first_fetched_at TEXT NOT NULL,        -- 首次抓取时间
    last_fetched_at TEXT,                  -- 最近抓取时间
    processed_at TEXT,                     -- 系统处理时间
    content_hash TEXT,                     -- 正文哈希（重复检测）
    duplicate_of INTEGER REFERENCES source_documents(id),      -- 指向重复的原材料
    original_source_id INTEGER REFERENCES source_documents(id),-- 可识别的最初来源
    source_cluster_id TEXT,                -- 疑似同源传播分组
    independent_confirmation INTEGER DEFAULT 0, -- 是否存在独立确认
    data_origin TEXT NOT NULL DEFAULT 'real',   -- real/sample/mock（明确标注数据性质）
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_docs_hash ON source_documents(content_hash);
CREATE INDEX IF NOT EXISTS idx_docs_published ON source_documents(published_at);

-- 事件主表
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    event_type TEXT,                       -- 并购/签约/融资/诉讼/业绩/回购...
    scope TEXT,                            -- 所属事件范围（如 jianghuai）
    tracked INTEGER NOT NULL DEFAULT 1,    -- 是否处于追踪中（0=已停止追踪，历史保留）
    subject_entities TEXT,                 -- JSON：主体实体
    object_entities TEXT,                  -- JSON：对象实体
    current_status TEXT NOT NULL DEFAULT 'unverified',
    current_version_id INTEGER,
    conclusion TEXT,                       -- 当前结论（含确定/不确定两部分）
    uncertainty TEXT,                      -- 待确认问题
    last_substantive_update_at TEXT,       -- 最后实质更新时间
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 主张表：结构化主张 + 内容性质 + 证据状态
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id),
    claim_text TEXT NOT NULL,
    claim_type TEXT NOT NULL,              -- fact/opinion/inference/rumor
    evidence_status TEXT NOT NULL DEFAULT 'unverified',
                                           -- supported/contradicted/unverified/superseded/stale
    first_seen_at TEXT,                    -- 该主张首次出现时间
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 证据关系表：主张 <-> 材料
CREATE TABLE IF NOT EXISTS evidence_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    document_id INTEGER NOT NULL REFERENCES source_documents(id),
    relation TEXT NOT NULL,                -- supports/contradicts/qualifies/supersedes
    quote TEXT,                            -- 原文引用片段（引用校验）
    reason TEXT,                           -- 判断理由
    weight_score REAL,                     -- 权重总分 W
    weight_detail TEXT,                    -- JSON：R/D/I/F/Q 分项与说明
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 事件版本表：每次实质变化的快照与原因
CREATE TABLE IF NOT EXISTS event_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id),
    version_no INTEGER NOT NULL,
    created_at TEXT NOT NULL,              -- 版本生成时间
    trigger_document_ids TEXT,             -- JSON：触发材料 IDs
    previous_version_id INTEGER,
    change_type TEXT,                      -- update/denial/correction/expiry/cancellation/duplicate
    changed_fields TEXT,                   -- JSON：变更字段
    status_before TEXT,
    status_after TEXT,
    reason TEXT,                           -- 变化原因说明
    UNIQUE(event_id, version_no)
);

-- 事件与标的关系表
CREATE TABLE IF NOT EXISTS security_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id),
    thscode TEXT NOT NULL,
    stock_name TEXT,
    relationship_type TEXT,                -- 主体/对手方/子公司/客户/供应商/同业
    relationship_evidence TEXT,
    relevance_level TEXT,                  -- high/medium/low
    impact_hypothesis TEXT,                -- 影响假设（明确标注为分析判断）
    impact_evidence TEXT,                  -- JSON：行情/财务背景数据引用
    UNIQUE(event_id, thscode)
);

-- 通知表
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id),
    version_id INTEGER REFERENCES event_versions(id),
    type TEXT NOT NULL,                    -- update/denial/correction/expiry
    severity TEXT DEFAULT 'normal',        -- important/normal
    title TEXT NOT NULL,
    body TEXT,
    diff_summary TEXT,                     -- 新旧版本差异摘要
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    read INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read);

-- 关注标的表
CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thscode TEXT NOT NULL UNIQUE,
    stock_name TEXT,
    notify_level TEXT DEFAULT 'normal',
    added_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 行情缓存表（扶摇）：事件影响验证的日线数据
CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thscode TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, prev_close REAL,
    change_pct REAL, volume REAL, turnover REAL,
    fetched_at TEXT NOT NULL,
    UNIQUE(thscode, trade_date)
);

-- 文档分析工作表（P2 管线维护）：相关性、所属范围与抽取进度
CREATE TABLE IF NOT EXISTS document_analysis (
    document_id INTEGER PRIMARY KEY REFERENCES source_documents(id),
    scope TEXT,                            -- 所属事件范围（如 jianghuai）
    relevance TEXT NOT NULL DEFAULT 'uncertain',  -- relevant/noise/uncertain
    noise_reason TEXT,
    extracted INTEGER NOT NULL DEFAULT 0,  -- 是否已完成 LLM 主张抽取
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 材料收件箱：待确认导入的新材料（演示"新材料导入"动线）
CREATE TABLE IF NOT EXISTS inbox_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT,
    source_type TEXT DEFAULT '新闻',
    source_name TEXT,
    publisher TEXT,
    published_at TEXT,
    source_url TEXT,
    stock_codes TEXT,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending/imported/duplicate/failed
    result_note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 热点榜快照：每次刷新生成一个批次
CREATE TABLE IF NOT EXISTS hot_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_at TEXT NOT NULL,                -- 批次时间
    rank INTEGER NOT NULL,
    title TEXT NOT NULL,
    heat REAL,
    source TEXT,
    summary TEXT,
    url TEXT,
    related_codes TEXT,                    -- JSON 数组：正文中出现的股票代码
    related_names TEXT,                    -- JSON 数组：命中的关注标的名称
    tracked_scope TEXT,                    -- 已被追踪为事件时的 scope key
    tracked_event_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_hot_topics_batch ON hot_topics(batch_at);

-- 动态事件范围：由热点追踪创建（内置范围见 pipeline/analyze.py）
CREATE TABLE IF NOT EXISTS scopes (
    key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    keywords TEXT NOT NULL,                -- JSON 数组
    source TEXT DEFAULT 'hot',             -- builtin/hot
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 同步状态表：记录各数据源的增量采集位点
CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


def get_conn() -> sqlite3.Connection:
    """获取数据库连接（行工厂为 sqlite3.Row，开启外键约束）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """初始化数据库与全部表（幂等）。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # 轻量迁移：旧库补充 events.scope / events.tracked 列
        columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
        if "scope" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN scope TEXT")
        if "tracked" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN tracked INTEGER NOT NULL DEFAULT 1")


if __name__ == "__main__":
    init_db()
    print(f"数据库初始化完成: {DB_PATH}")
