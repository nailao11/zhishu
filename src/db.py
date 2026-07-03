"""SQLite 数据库层

存储所有查询过的关键词每日指数。同一天的同一关键词重复入库会自动覆盖。
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from .crawler import KeywordResult

DEFAULT_DB_PATH = Path("data/zhishu.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT UNIQUE NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS daily_index (
    keyword TEXT NOT NULL,
    date TEXT NOT NULL,
    all_index INTEGER NOT NULL DEFAULT 0,
    pc_index INTEGER NOT NULL DEFAULT 0,
    wise_index INTEGER NOT NULL DEFAULT 0,
    area INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (keyword, date, area)
);

CREATE INDEX IF NOT EXISTS idx_daily_index_keyword ON daily_index(keyword);
CREATE INDEX IF NOT EXISTS idx_daily_index_date ON daily_index(date);

CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    finished_at TEXT,
    keyword_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    error TEXT
);
"""


class Database:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # --------- 关键词管理 ---------

    def add_keyword(self, keyword: str) -> bool:
        with self._lock, self._conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO keywords (keyword, enabled) VALUES (?, 1)",
                    (keyword,),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def add_keywords(self, keywords: Iterable[str]) -> int:
        added = 0
        with self._lock, self._conn() as conn:
            for kw in keywords:
                kw = kw.strip()
                if not kw:
                    continue
                try:
                    conn.execute(
                        "INSERT INTO keywords (keyword, enabled) VALUES (?, 1)",
                        (kw,),
                    )
                    added += 1
                except sqlite3.IntegrityError:
                    pass
        return added

    def remove_keyword(self, keyword: str) -> bool:
        with self._lock, self._conn() as conn:
            cur = conn.execute("DELETE FROM keywords WHERE keyword=?", (keyword,))
            return cur.rowcount > 0

    def set_keyword_enabled(self, keyword: str, enabled: bool) -> bool:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "UPDATE keywords SET enabled=? WHERE keyword=?",
                (1 if enabled else 0, keyword),
            )
            return cur.rowcount > 0

    def list_keywords(self, enabled_only: bool = False) -> list[dict]:
        with self._conn() as conn:
            sql = "SELECT keyword, enabled, created_at FROM keywords"
            if enabled_only:
                sql += " WHERE enabled=1"
            sql += " ORDER BY id"
            return [dict(r) for r in conn.execute(sql).fetchall()]

    # --------- 数据写入 ---------

    def save_results(self, results: Iterable[KeywordResult], area: int = 0) -> int:
        written = 0
        with self._lock, self._conn() as conn:
            for res in results:
                for point in res.daily_points():
                    conn.execute(
                        """
                        INSERT INTO daily_index
                            (keyword, date, all_index, pc_index, wise_index, area, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                        ON CONFLICT(keyword, date, area) DO UPDATE SET
                            all_index = excluded.all_index,
                            pc_index = excluded.pc_index,
                            wise_index = excluded.wise_index,
                            updated_at = excluded.updated_at
                        """,
                        (
                            res.keyword,
                            point["date"],
                            point["all"],
                            point["pc"],
                            point["wise"],
                            area,
                        ),
                    )
                    written += 1
        return written

    # --------- 数据查询 ---------

    def query_index(
        self,
        keyword: str,
        start_date: str | None = None,
        end_date: str | None = None,
        area: int = 0,
    ) -> list[dict]:
        sql = "SELECT date, all_index, pc_index, wise_index FROM daily_index WHERE keyword=? AND area=?"
        params: list = [keyword, area]
        if start_date:
            sql += " AND date >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND date <= ?"
            params.append(end_date)
        sql += " ORDER BY date"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def latest_index(self, keyword: str, area: int = 0) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT date, all_index, pc_index, wise_index, updated_at
                FROM daily_index
                WHERE keyword=? AND area=?
                ORDER BY date DESC LIMIT 1
                """,
                (keyword, area),
            ).fetchone()
            return dict(row) if row else None

    # --------- 运行日志 ---------

    def start_run(self, keyword_count: int) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO run_log (keyword_count, status) VALUES (?, 'running')",
                (keyword_count,),
            )
            return cur.lastrowid

    def finish_run(
        self,
        run_id: int,
        success: int,
        fail: int,
        error: str | None = None,
    ) -> None:
        # 全成功=success，部分成功=partial，全失败=failed；error 无论成败都记录
        if fail <= 0:
            status = "success"
        elif success > 0:
            status = "partial"
        else:
            status = "failed"
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE run_log
                SET finished_at = datetime('now', 'localtime'),
                    success_count = ?, fail_count = ?, status = ?, error = ?
                WHERE id = ?
                """,
                (success, fail, status, error, run_id),
            )

    def recent_runs(self, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM run_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()]

    # --------- 数据清理 ---------

    def prune_old(self, retention_days: int = 45) -> dict:
        """删除早于保留期的历史指数和运行记录。"""
        if retention_days <= 0:
            return {"daily_index_deleted": 0, "run_log_deleted": 0}
        cutoff_date = (date.today() - timedelta(days=retention_days)).isoformat()
        cutoff_ts = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self._conn() as conn:
            c1 = conn.execute("DELETE FROM daily_index WHERE date < ?", (cutoff_date,))
            c2 = conn.execute("DELETE FROM run_log WHERE started_at < ?", (cutoff_ts,))
            return {"daily_index_deleted": c1.rowcount, "run_log_deleted": c2.rowcount}

    def prune_expired_keywords(self, ttl_days: int) -> int:
        """删除添加满 ttl_days 天的关键词；历史数据仍按保留期单独清理。"""
        if ttl_days <= 0:
            return 0
        cutoff = (datetime.now() - timedelta(days=ttl_days)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self._conn() as conn:
            cur = conn.execute("DELETE FROM keywords WHERE created_at < ?", (cutoff,))
            return cur.rowcount
