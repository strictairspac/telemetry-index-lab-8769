"""读写接口与读己之写对账策略。

流程（携带凭据的查询）：
1. 校验凭据真伪/有效期，并确认凭据绑定设备 == 查询设备，否则拒绝（跨设备不可用）；
2. 先查真实只读副本：applied_seq >= 凭据水位 seq 才说明这笔写入已回放，从副本返回；
3. 副本没追上则最多等待 replica_wait_seconds（默认 1 秒），期间持续对账；
4. 仍未追上则回源主库；回源并发受信号量约束，槽满立刻返回 RetryLater（HTTP 503 + Retry-After）。

不携带凭据的查询视为普通只读流量：直接副本返回，不等待、不回源。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from rlw import credentials as creds
from rlw.errors import CredentialError, RetryLater, ValidationError
from rlw.storage import ReplicatedStore

SOURCE_PRIMARY = "primary"
SOURCE_REPLICA = "replica"


@dataclass
class ReadResult:
    row: dict[str, Any] | None
    source: str                      # 实际读库：primary / replica
    required_seq: int                # 凭据要求的水位（无凭据为 0）
    replica_applied_seq: int         # 查询时刻副本已应用水位
    waited_seconds: float = 0.0
    credential_device: str | None = None
    fell_back: bool = False          # 是否因副本滞后回源主库


class TelemetryService:
    def __init__(
        self,
        store: ReplicatedStore,
        *,
        secret: str | None = None,
        credential_ttl_seconds: int = 3600,
        replica_wait_seconds: float = 1.0,
        max_primary_readers: int = 8,
        primary_read_delay: float = 0.0,
    ):
        self.store = store
        self.secret = secret or _default_secret()
        self.credential_ttl = credential_ttl_seconds
        self.replica_wait_seconds = replica_wait_seconds
        self.primary_read_delay = primary_read_delay  # 测试钩子：拉长主库读以制造回源满载
        self._primary_slots = threading.BoundedSemaphore(max_primary_readers)

    # ---------------- 写接口 ----------------
    def write_telemetry(
        self, device_id: str, metric: str, value: Any, recorded_at: str
    ) -> dict[str, Any]:
        """补录/更正写入：主库落库后签发设备凭据，凭据内含该笔写入的复制水位。"""
        _validate_telemetry(device_id, metric, value, recorded_at)
        value = float(value)
        row, seq = self.store.write_telemetry(device_id, metric, value, recorded_at)
        token, expires_at = creds.issue_credential(
            device_id, seq, self.secret, ttl_seconds=self.credential_ttl
        )
        return {
            "row": row,
            "seq": seq,
            "device_id": device_id,
            "credential": token,
            "credential_expires_at": expires_at,
            "primary_max_seq": self.store.primary.max_seq(),
        }

    # ---------------- 读接口 ----------------
    def read_telemetry(
        self, device_id: str, metric: str, token: str | None = None
    ) -> ReadResult:
        if not device_id or not isinstance(device_id, str):
            raise ValidationError("device_id 不能为空")
        if not metric or not isinstance(metric, str):
            raise ValidationError("metric 不能为空")

        replica = self.store.replica
        applied = replica.applied_seq

        # 无凭据：普通只读流量，直接读副本
        if not token:
            return ReadResult(
                row=replica.read_row(device_id, metric),
                source=SOURCE_REPLICA,
                required_seq=0,
                replica_applied_seq=applied,
            )

        payload = creds.verify_credential(token, self.secret)
        # 凭据与设备强绑定，跨设备使用直接拒绝
        if payload["d"] != device_id:
            raise CredentialError(
                403,
                "credential_device_mismatch",
                "凭据不能跨设备使用",
            )
        required_seq = int(payload["s"])

        # 对账：副本已应用水位覆盖该笔写入才从副本返回
        deadline = time.monotonic() + self.replica_wait_seconds
        waited = 0.0
        applied = replica.applied_seq
        while applied < required_seq and time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            time.sleep(min(0.02, remaining))
            waited = self.replica_wait_seconds - max(0.0, deadline - time.monotonic())
            applied = replica.applied_seq

        if applied >= required_seq:
            return ReadResult(
                row=replica.read_row(device_id, metric),
                source=SOURCE_REPLICA,
                required_seq=required_seq,
                replica_applied_seq=applied,
                waited_seconds=round(waited, 6),
                credential_device=payload["d"],
            )

        # 副本在 1 秒窗口内未追上 → 回源主库；并发槽满则请稍后重试
        if not self._primary_slots.acquire(blocking=False):
            raise RetryLater(retry_after=1)
        try:
            if self.primary_read_delay:
                time.sleep(self.primary_read_delay)
            row = self.store.primary.read_row(device_id, metric)
            return ReadResult(
                row=row,
                source=SOURCE_PRIMARY,
                required_seq=required_seq,
                replica_applied_seq=applied,
                waited_seconds=round(waited, 6),
                credential_device=payload["d"],
                fell_back=True,
            )
        finally:
            self._primary_slots.release()

    # ---------------- 管理与观测 ----------------
    def replication_status(self) -> dict[str, Any]:
        primary_seq = self.store.primary.max_seq()
        applied = self.store.replica.applied_seq
        return {
            "primary_max_seq": primary_seq,
            "replica_applied_seq": applied,
            "caught_up": applied >= primary_seq,
            "lag_events": max(0, primary_seq - applied),
            "replication_paused": self.store._paused.is_set(),  # noqa: SLF001
        }


def _validate_telemetry(
    device_id: str, metric: str, value: Any, recorded_at: str
) -> None:
    if not device_id or not isinstance(device_id, str) or len(device_id) > 64:
        raise ValidationError("device_id 必须为 1-64 字符的字符串")
    if not metric or not isinstance(metric, str) or len(metric) > 64:
        raise ValidationError("metric 必须为 1-64 字符的字符串")
    if not recorded_at or not isinstance(recorded_at, str) or len(recorded_at) > 40:
        raise ValidationError("recorded_at 必须为 1-40 字符的字符串")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("value 必须为数值")
    if value != value or value in (float("inf"), float("-inf")):
        raise ValidationError("value 不能为 NaN/Infinity")


def _default_secret() -> str:
    import os

    secret = os.getenv("RLW_CREDENTIAL_SECRET")
    if secret:
        return secret
    # 本地演示兜底；生产必须通过环境变量注入
    return "rlw-local-dev-only-secret-change-me"
