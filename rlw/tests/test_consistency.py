"""场景验证：
1) 暂停复制时补录/更正 —— 副本读不到新值，携凭据查询等待不超过 1 秒后回主库读到新值；
2) 恢复复制后回读 —— 水位追平，携凭据查询直接从真实副本返回新值；
3) 主库回源满载 —— 并发槽满时返回 RetryLater / HTTP 503 + Retry-After；
另含：凭据不能跨设备、副本强制只读、无凭据读走副本、HTTP 端到端冒烟。
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from rlw.credentials import verify_credential
from rlw.errors import CredentialError, RetryLater
from rlw.http_app import build_server
from rlw.service import SOURCE_PRIMARY, SOURCE_REPLICA, TelemetryService
from rlw.storage import ReplicatedStore

REPLICA_WAIT = 0.3  # 测试内缩短等待窗口；生产默认 1 秒


class RlwTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = self._tmp.name
        self.store = ReplicatedStore(self.data_dir)
        self.service = TelemetryService(
            self.store,
            secret="unit-test-secret",
            replica_wait_seconds=REPLICA_WAIT,
            max_primary_readers=8,
        )
        # 等初始空库水位一致
        self.assertTrue(self.store.wait_caught_up())

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    # ---------- 场景 1：暂停复制时补录/更正 ----------
    def test_01_backfill_while_replication_paused_falls_back_to_primary(self) -> None:
        device, metric = "pump-17", "bearing_temp"

        # 先有一笔已同步的历史值
        first = self.service.write_telemetry(device, metric, 41.0, "2026-11-18T08:00:00Z")
        self.assertTrue(self.store.wait_caught_up())
        self.assertEqual(self.store.replica.read_row(device, metric)["value"], 41.0)

        # 暂停复制（对应 STOP SLAVE），确认 applier 确已停住
        self.store.pause_replication()
        self.assertTrue(self.store._paused.is_set())

        # 暂停期间补录（新主键）与更正（已有主键）两笔
        backfill = self.service.write_telemetry(device, "vibration", 0.32, "2026-11-18T09:00:00Z")
        correction = self.service.write_telemetry(device, metric, 43.5, "2026-11-18T09:05:00Z")

        # 复制确实没动：副本水位停在暂停前，新值在副本不可见
        self.assertLess(self.store.replica.applied_seq, backfill["seq"])
        self.assertIsNone(self.store.replica.read_row(device, "vibration"))
        self.assertEqual(self.store.replica.read_row(device, metric)["value"], 41.0)
        # 主库已是新值
        self.assertEqual(self.store.primary.read_row(device, metric)["value"], 43.5)
        self.assertEqual(self.store.primary.read_row(device, metric)["version"], 2)

        # 无凭据查询：普通只读流量，直接给副本旧值，且标明读库为副本
        anon = self.service.read_telemetry(device, metric)
        self.assertEqual(anon.source, SOURCE_REPLICA)
        self.assertEqual(anon.row["value"], 41.0)

        # 携补录凭据查询：对账发现副本未应用 -> 等待 <= 窗口（最多1秒策略，测试用0.3秒）
        start = time.monotonic()
        result = self.service.read_telemetry(device, "vibration", backfill["credential"])
        elapsed = time.monotonic() - start
        self.assertEqual(result.source, SOURCE_PRIMARY)
        self.assertTrue(result.fell_back)
        self.assertEqual(result.row["value"], 0.32)
        self.assertEqual(result.required_seq, backfill["seq"])
        self.assertLess(result.replica_applied_seq, backfill["seq"])
        self.assertGreaterEqual(elapsed, REPLICA_WAIT - 0.15)
        self.assertLessEqual(elapsed, REPLICA_WAIT + 0.3)

        # 携更正凭据查询同样回主库，读到更正后的新值（读己之写保证）
        corrected = self.service.read_telemetry(device, metric, correction["credential"])
        self.assertEqual(corrected.source, SOURCE_PRIMARY)
        self.assertEqual(corrected.row["value"], 43.5)
        self.assertEqual(corrected.row["version"], 2)

        # 凭据本身可独立验真，水位即写入 seq
        payload = verify_credential(backfill["credential"], "unit-test-secret")
        self.assertEqual(payload["d"], device)
        self.assertEqual(payload["s"], backfill["seq"])

    # ---------- 场景 2：恢复复制后回读 ----------
    def test_02_read_from_replica_after_replication_resumes(self) -> None:
        device, metric = "pump-21", "pressure"
        written = self.service.write_telemetry(device, metric, 0.91, "2026-11-18T10:00:00Z")

        self.store.pause_replication()
        # 暂停期间再来一笔更正，制造滞后
        lagged = self.service.write_telemetry(device, metric, 0.97, "2026-11-18T10:10:00Z")
        self.assertLess(self.store.replica.applied_seq, lagged["seq"])

        # 恢复复制（对应 START SLAVE），等待水位追平
        self.store.resume_replication()
        self.assertTrue(self.store.wait_caught_up(timeout=5))
        self.assertEqual(self.store.replica.applied_seq, self.store.primary.max_seq())

        # 携滞后那笔的凭据：副本已应用该写入，直接从真实副本返回，无需等待/回源
        start = time.monotonic()
        result = self.service.read_telemetry(device, metric, lagged["credential"])
        elapsed = time.monotonic() - start
        self.assertEqual(result.source, SOURCE_REPLICA)
        self.assertFalse(result.fell_back)
        self.assertEqual(result.row["value"], 0.97)
        self.assertEqual(result.row["version"], 2)
        self.assertGreaterEqual(result.replica_applied_seq, lagged["seq"])
        self.assertLess(elapsed, 0.05)

        # 旧凭据（更早水位）同样可在副本满足，值是最新值
        old_cred_result = self.service.read_telemetry(device, metric, written["credential"])
        self.assertEqual(old_cred_result.source, SOURCE_REPLICA)
        self.assertEqual(old_cred_result.row["value"], 0.97)

    # ---------- 场景 3：主库回源并发满载 ----------
    def test_03_primary_fallback_saturation_returns_retry_later(self) -> None:
        # 单并发槽 + 主库读加延迟，制造确定性的槽位竞争
        store = ReplicatedStore(Path(self.data_dir) / "sat")
        service = TelemetryService(
            store,
            secret="unit-test-secret",
            replica_wait_seconds=REPLICA_WAIT,
            max_primary_readers=1,
            primary_read_delay=0.6,
        )
        try:
            store.pause_replication()
            outcome = service.write_telemetry("pump-33", "flow", 12.4, "2026-11-18T11:00:00Z")
            self.assertLess(store.replica.applied_seq, outcome["seq"])

            results: dict[str, object] = {}
            barrier = threading.Event()

            def first_reader() -> None:
                barrier.set()
                res = service.read_telemetry("pump-33", "flow", outcome["credential"])
                results["first"] = res

            def second_reader() -> None:
                barrier.wait()
                time.sleep(0.1)  # 确保第一个已占住唯一回源槽
                try:
                    service.read_telemetry("pump-33", "flow", outcome["credential"])
                except RetryLater as exc:
                    results["second_error"] = exc

            t1 = threading.Thread(target=first_reader)
            t2 = threading.Thread(target=second_reader)
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)

            first = results["first"]
            self.assertEqual(first.source, SOURCE_PRIMARY)  # type: ignore[union-attr]
            self.assertEqual(first.row["value"], 12.4)  # type: ignore[union-attr]
            second_error = results["second_error"]
            self.assertIsInstance(second_error, RetryLater)
            self.assertEqual(second_error.retry_after, 1)  # type: ignore[union-attr]

            # 恢复复制后，重试即可在副本成功，不再需要回源
            store.resume_replication()
            self.assertTrue(store.wait_caught_up(timeout=5))
            retry = service.read_telemetry("pump-33", "flow", outcome["credential"])
            self.assertEqual(retry.source, SOURCE_REPLICA)
            self.assertEqual(retry.row["value"], 12.4)
        finally:
            store.close()

    # ---------- 凭据：设备隔离 / 篡改 / 过期 ----------
    def test_04_credential_is_device_scoped_and_tamper_proof(self) -> None:
        a = self.service.write_telemetry("device-A", "temp", 20.0, "2026-11-18T12:00:00Z")
        b = self.service.write_telemetry("device-B", "temp", 21.0, "2026-11-18T12:00:00Z")
        self.store.wait_caught_up()

        # 跨设备使用 → 403
        with self.assertRaises(CredentialError) as ctx:
            self.service.read_telemetry("device-B", "temp", a["credential"])
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(ctx.exception.code, "credential_device_mismatch")

        # 篡改签名 → 401
        head, body, sig = a["credential"].split(".")
        tampered = f"{head}.{body}.{sig[:-2]}AA"
        with self.assertRaises(CredentialError) as ctx2:
            self.service.read_telemetry("device-A", "temp", tampered)
        self.assertEqual(ctx2.exception.status, 401)

        # 非本服务密钥签发 → 401
        from rlw.credentials import issue_credential

        foreign, _ = issue_credential("device-A", 1, "wrong-secret")
        with self.assertRaises(CredentialError) as ctx3:
            self.service.read_telemetry("device-A", "temp", foreign)
        self.assertEqual(ctx3.exception.status, 401)

        # 过期凭据 → 401
        expired, _ = issue_credential(
            "device-A", 1, "unit-test-secret", ttl_seconds=-10
        )
        with self.assertRaises(CredentialError) as ctx4:
            self.service.read_telemetry("device-A", "temp", expired)
        self.assertEqual(ctx4.exception.code, "credential_expired")

        # 正常凭据读到各自设备的值
        ok_a = self.service.read_telemetry("device-A", "temp", a["credential"])
        ok_b = self.service.read_telemetry("device-B", "temp", b["credential"])
        self.assertEqual(ok_a.row["value"], 20.0)
        self.assertEqual(ok_b.row["value"], 21.0)
        self.assertEqual(ok_a.source, SOURCE_REPLICA)

    # ---------- 副本强制只读 ----------
    def test_05_replica_connection_is_read_only(self) -> None:
        self.service.write_telemetry("device-ro", "m", 1.0, "2026-11-18T13:00:00Z")
        self.store.wait_caught_up()
        import sqlite3

        with self.assertRaises(sqlite3.OperationalError):
            self.store.replica.read_conn.execute(
                "INSERT INTO telemetry (device_id, metric, value, recorded_at, version) "
                "VALUES ('x','y',1,'z',1)"
            )
        with self.assertRaises(sqlite3.OperationalError):
            self.store.replica.read_conn.execute("DELETE FROM telemetry")

    # ---------- 参数校验 ----------
    def test_06_input_validation(self) -> None:
        from rlw.errors import ValidationError

        for bad in [
            ("", "m", 1.0, "t"),
            ("d", "", 1.0, "t"),
            ("d", "m", "not-a-number", "t"),
            ("d", "m", float("nan"), "t"),
            ("d", "m", float("inf"), "t"),
            ("d", "m", 1.0, ""),
        ]:
            with self.assertRaises(ValidationError):
                self.service.write_telemetry(*bad)

    # ---------- HTTP 端到端冒烟 ----------
    def test_07_http_end_to_end(self) -> None:
        server = build_server(
            Path(self.data_dir) / "http",
            host="127.0.0.1",
            port=0,
            replica_wait_seconds=REPLICA_WAIT,
            max_primary_readers=1,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base = f"http://{host}:{port}"
        try:
            # 健康检查
            with urllib.request.urlopen(f"{base}/health", timeout=5) as resp:
                health = json.loads(resp.read())
            self.assertEqual(health["status"], "ok")

            # 暂停 → 写入 → 携凭据读 → 回源主库，响应标明实际读库
            req = urllib.request.Request(
                f"{base}/admin/replication/pause", data=b"", method="POST"
            )
            urllib.request.urlopen(req, timeout=5).read()

            payload = json.dumps(
                {
                    "device_id": "http-device",
                    "metric": "temp",
                    "value": 36.6,
                    "recorded_at": "2026-11-18T14:00:00Z",
                }
            ).encode()
            req = urllib.request.Request(
                f"{base}/api/telemetry/ingest",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 201)
                ingest = json.loads(resp.read())
            self.assertIn("credential", ingest)
            self.assertEqual(ingest["row"]["value"], 36.6)

            req = urllib.request.Request(
                f"{base}/api/telemetry?device_id=http-device&metric=temp",
                headers={"X-Device-Credential": ingest["credential"]},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                read_back = json.loads(resp.read())
            self.assertEqual(read_back["read_source"], SOURCE_PRIMARY)
            self.assertTrue(read_back["fell_back_to_primary"])
            self.assertEqual(read_back["row"]["value"], 36.6)

            # 回源满载 → 503 + Retry-After
            server.service.primary_read_delay = 0.6
            outcome: dict = {}

            def call() -> None:
                try:
                    urllib.request.urlopen(
                        urllib.request.Request(
                            f"{base}/api/telemetry?device_id=http-device&metric=temp",
                            headers={"X-Device-Credential": ingest["credential"]},
                        ),
                        timeout=5,
                    ).read()
                except urllib.error.HTTPError as exc:
                    outcome["code"] = exc.code
                    outcome["retry_after"] = exc.headers.get("Retry-After")
                    outcome["body"] = json.loads(exc.read())

            # 连续两发：单槽位下至少一发拿到 503
            threads = [threading.Thread(target=call) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(outcome.get("code"), 503)
            self.assertEqual(outcome.get("retry_after"), "1")
            self.assertEqual(outcome["body"]["error"]["code"], "retry_later")

            # 跨设备凭据 → 403
            try:
                urllib.request.urlopen(
                    urllib.request.Request(
                        f"{base}/api/telemetry?device_id=other-device&metric=temp",
                        headers={"X-Device-Credential": ingest["credential"]},
                    ),
                    timeout=5,
                )
                self.fail("expected 403")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 403)
                self.assertEqual(json.loads(exc.read())["error"]["code"], "credential_device_mismatch")

            # 恢复复制 → 副本回读
            req = urllib.request.Request(
                f"{base}/admin/replication/resume", data=b"", method="POST"
            )
            urllib.request.urlopen(req, timeout=6).read()
            with urllib.request.urlopen(
                urllib.request.Request(
                    f"{base}/api/telemetry?device_id=http-device&metric=temp",
                    headers={"X-Device-Credential": ingest["credential"]},
                ),
                timeout=5,
            ) as resp:
                read_back = json.loads(resp.read())
            self.assertEqual(read_back["read_source"], SOURCE_REPLICA)
            self.assertFalse(read_back["fell_back_to_primary"])
        finally:
            server.shutdown()
            server.service.store.close()
            server.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
