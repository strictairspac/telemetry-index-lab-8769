"""真实的主从存储（单机双 SQLite 文件，逻辑等价于 MySQL 主库/只读副本 + binlog/GTID）。

- 主库 primary.db：唯一写入口，业务行与复制日志在同一事务提交（原子 binlog）；
- 复制日志 replication_log(seq, payload)：对应 MySQL binlog 事件，seq 单调递增即 GTID 水位；
- 副本 replica.db：独立数据库文件，由 applier 线程按序回放日志，applied_seq 记录已应用水位；
- 副本以只读 URI 打开（mode=ro），任何写尝试直接抛错，杜绝应用层写穿；
- applier 支持暂停/恢复，用于真实模拟复制延迟与 STOP/START SLAVE。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any

_DDL_PRIMARY = """
CREATE TABLE IF NOT EXISTS telemetry (
    device_id   TEXT NOT NULL,
    metric      TEXT NOT NULL,
    value       REAL NOT NULL,
    recorded_at TEXT NOT NULL,
    version     INTEGER NOT NULL,
    PRIMARY KEY (device_id, metric)
);
CREATE TABLE IF NOT EXISTS replication_log (
    seq      INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    payload  TEXT NOT NULL
);
"""

_DDL_REPLICA = """
CREATE TABLE IF NOT EXISTS telemetry (
    device_id   TEXT NOT NULL,
    metric      TEXT NOT NULL,
    value       REAL NOT NULL,
    recorded_at TEXT NOT NULL,
    version     INTEGER NOT NULL,
    PRIMARY KEY (device_id, metric)
);
CREATE TABLE IF NOT EXISTS replica_status (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    applied_seq INTEGER NOT NULL
);
INSERT OR IGNORE INTO replica_status (id, applied_seq) VALUES (1, 0);
"""


def _connect_writable(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def _connect_readonly(path: str) -> sqlite3.Connection:
    # uri 只读模式：副本对应用侧强制只读（类比 MySQL 只读实例）
    return sqlite3.connect(
        f"file:{path}?mode=ro", uri=True, check_same_thread=False
    )


class PrimaryStore:
    """主库：唯一接受写的数据库，写操作受锁串行化。"""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self.conn = _connect_writable(path)
        self.conn.executescript(_DDL_PRIMARY)

    def max_seq(self) -> int:
        row = self.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM replication_log").fetchone()
        return int(row[0])

    def upsert_telemetry(
        self, device_id: str, metric: str, value: float, recorded_at: str
    ) -> tuple[dict[str, Any], int]:
        """补录/更正同一主键行；每次写入 version 递增，并追加一条复制日志。

        返回 (落库行, seq)。行与日志在同一事务提交，要么都成功要么都回滚。
        """
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                old = self.conn.execute(
                    "SELECT version FROM telemetry WHERE device_id=? AND metric=?",
                    (device_id, metric),
                ).fetchone()
                version = 1 if old is None else int(old[0]) + 1
                self.conn.execute(
                    """INSERT INTO telemetry (device_id, metric, value, recorded_at, version)
                       VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(device_id, metric) DO UPDATE SET
                       value=excluded.value,
                       recorded_at=excluded.recorded_at,
                       version=excluded.version""",
                    (device_id, metric, value, recorded_at, version),
                )
                event = json.dumps(
                    {
                        "device_id": device_id,
                        "metric": metric,
                        "value": value,
                        "recorded_at": recorded_at,
                        "version": version,
                    },
                    ensure_ascii=False,
                )
                cur = self.conn.execute(
                    "INSERT INTO replication_log (device_id, payload) VALUES (?, ?)",
                    (device_id, event),
                )
                seq = int(cur.lastrowid)
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        row = self.read_row(device_id, metric)
        assert row is not None
        return row, seq

    def read_row(self, device_id: str, metric: str) -> dict[str, Any] | None:
        cur = self.conn.execute(
            "SELECT device_id, metric, value, recorded_at, version "
            "FROM telemetry WHERE device_id=? AND metric=?",
            (device_id, metric),
        )
        return _row_to_dict(cur.fetchone())

    def close(self) -> None:
        with self._lock:
            self.conn.close()


class ReadReplica:
    """只读副本：独立数据文件，应用侧只读，数据只由 applier 回放写入。"""

    def __init__(self, path: str):
        self.path = path
        self._applier_conn = _connect_writable(path)
        self._applier_conn.executescript(_DDL_REPLICA)
        self.read_conn = _connect_readonly(path)

    @property
    def applied_seq(self) -> int:
        row = self.read_conn.execute(
            "SELECT applied_seq FROM replica_status WHERE id=1"
        ).fetchone()
        return int(row[0])

    def read_row(self, device_id: str, metric: str) -> dict[str, Any] | None:
        cur = self.read_conn.execute(
            "SELECT device_id, metric, value, recorded_at, version "
            "FROM telemetry WHERE device_id=? AND metric=?",
            (device_id, metric),
        )
        return _row_to_dict(cur.fetchone())

    def close(self) -> None:
        self.read_conn.close()
        self._applier_conn.close()


def _row_to_dict(row: tuple | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "device_id": row[0],
        "metric": row[1],
        "value": row[2],
        "recorded_at": row[3],
        "version": row[4],
    }


class ReplicatedStore:
    """主库 + 只读副本 + 可暂停的复制回放线程。"""

    def __init__(self, data_dir: str, *, poll_interval: float = 0.02):
        os.makedirs(data_dir, exist_ok=True)
        self.data_dir = data_dir
        self.poll_interval = poll_interval
        self.primary = PrimaryStore(os.path.join(data_dir, "primary.db"))
        self.replica = ReadReplica(os.path.join(data_dir, "replica.db"))

        self._stop = threading.Event()
        self._paused = threading.Event()
        self._pause_ack = threading.Event()
        self._applier_thread = threading.Thread(
            target=self._applier_loop, name="rlw-replication-applier", daemon=True
        )
        self._applier_thread.start()

    # ---------- 复制控制（对应 STOP / START SLAVE） ----------
    def pause_replication(self) -> None:
        """暂停回放：已在途批次应用完即停住，未应用的日志保留在主库。"""
        self._pause_ack.clear()
        self._paused.set()
        self._pause_ack.wait(timeout=5)

    def resume_replication(self) -> None:
        self._paused.clear()
        self._pause_ack.clear()

    def wait_caught_up(self, timeout: float = 5.0) -> bool:
        """等待副本 applied_seq 追平主库 max_seq。"""
        waited = 0.0
        interval = min(self.poll_interval, 0.02)
        while waited < timeout:
            if self.replica.applied_seq >= self.primary.max_seq():
                return True
            threading.Event().wait(interval)
            waited += interval
        return self.replica.applied_seq >= self.primary.max_seq()

    # ---------- applier 内部逻辑 ----------
    def _applier_loop(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                # 收到暂停指令后确认一次，测试可据此确认复制确已停住
                self._pause_ack.set()
                self._stop.wait(0.05)
                continue
            if not self._apply_batch():
                self._stop.wait(self.poll_interval)

    def _apply_batch(self, limit: int = 100) -> bool:
        """从主库拉取 applied_seq 之后的日志并按序回放到副本，推进水位。"""
        conn = self.replica._applier_conn  # noqa: SLF001 — 仅 applier 线程可写副本
        applied = self.replica.applied_seq
        rows = self.primary.conn.execute(
            "SELECT seq, payload FROM replication_log WHERE seq > ? ORDER BY seq LIMIT ?",
            (applied, limit),
        ).fetchall()
        if not rows:
            return False
        conn.execute("BEGIN IMMEDIATE")
        try:
            for seq, payload in rows:
                event = json.loads(payload)
                conn.execute(
                    """INSERT INTO telemetry (device_id, metric, value, recorded_at, version)
                       VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(device_id, metric) DO UPDATE SET
                       value=excluded.value,
                       recorded_at=excluded.recorded_at,
                       version=excluded.version""",
                    (
                        event["device_id"],
                        event["metric"],
                        event["value"],
                        event["recorded_at"],
                        event["version"],
                    ),
                )
                conn.execute(
                    "UPDATE replica_status SET applied_seq=? WHERE id=1", (int(seq),)
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return True

    # ---------- 委托给主库的写接口 ----------
    def write_telemetry(
        self, device_id: str, metric: str, value: float, recorded_at: str
    ) -> tuple[dict[str, Any], int]:
        return self.primary.upsert_telemetry(device_id, metric, value, recorded_at)

    def close(self) -> None:
        self._stop.set()
        self._paused.clear()
        self._applier_thread.join(timeout=5)
        self.primary.close()
        self.replica.close()

    def __enter__(self) -> "ReplicatedStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
