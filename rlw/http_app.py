"""零依赖 HTTP 适配层（仅标准库 http.server）。

路由：
- POST /api/telemetry/ingest        主库补录/更正，返回设备凭据
- GET  /api/telemetry?device_id=&metric=   携带 X-Device-Credential 时按读己之写对账
- GET  /replication/status          复制水位观测
- POST /admin/replication/pause     暂停复制（试验/运维用）
- POST /admin/replication/resume    恢复复制
- GET  /health

所有读响应都带 read_source 标明实际读库（primary/replica）。
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from rlw.errors import CredentialError, RetryLater, ValidationError
from rlw.service import TelemetryService
from rlw.storage import ReplicatedStore

_MAX_BODY = 64 * 1024


class _Handler(BaseHTTPRequestHandler):
    server_version = "rlw/1.0"

    def log_message(self, fmt: str, *args: object) -> None:  # 安静模式
        return

    @property
    def service(self) -> TelemetryService:
        # RlwHTTPServer 在构造时持有 service，handler 由其工厂创建
        return self.server.service  # type: ignore[attr-defined]

    # ---------- 工具 ----------
    def _send_json(self, status: int, payload: dict, headers: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ValidationError("请求体不能为空")
        if length > _MAX_BODY:
            raise ValidationError("请求体超过 64KB 限制")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValidationError("请求体不是合法 JSON")
        if not isinstance(data, dict):
            raise ValidationError("请求体必须是 JSON 对象")
        return data

    # ---------- 路由 ----------
    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._send_json(200, {"status": "ok", **self.service.replication_status()})
                return
            if parsed.path == "/replication/status":
                self._send_json(200, self.service.replication_status())
                return
            if parsed.path == "/api/telemetry":
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                device_id = query.get("device_id", "")
                metric = query.get("metric", "")
                token = self.headers.get("X-Device-Credential") or query.get("credential")
                result = self.service.read_telemetry(device_id, metric, token)
                self._send_json(
                    200,
                    {
                        "row": result.row,
                        "read_source": result.source,
                        "required_seq": result.required_seq,
                        "replica_applied_seq": result.replica_applied_seq,
                        "waited_seconds": result.waited_seconds,
                        "fell_back_to_primary": result.fell_back,
                        "credential_device": result.credential_device,
                    },
                )
                return
            self._send_json(404, {"error": {"code": "not_found", "message": "路径不存在"}})
        except CredentialError as exc:
            self._send_json(exc.status, {"error": {"code": exc.code, "message": exc.message}})
        except ValidationError as exc:
            self._send_json(400, {"error": {"code": "invalid_request", "message": str(exc)}})
        except RetryLater as exc:
            self._send_json(
                503,
                {"error": {"code": exc.code, "message": exc.message}},
                {"Retry-After": exc.retry_after},
            )
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"error": {"code": "internal", "message": str(exc)}})

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/telemetry/ingest":
                data = self._read_json()
                outcome = self.service.write_telemetry(
                    data.get("device_id", ""),
                    data.get("metric", ""),
                    data.get("value"),
                    data.get("recorded_at", ""),
                )
                self._send_json(201, outcome)
                return
            if parsed.path == "/admin/replication/pause":
                self.service.store.pause_replication()
                self._send_json(200, {"paused": True, **self.service.replication_status()})
                return
            if parsed.path == "/admin/replication/resume":
                self.service.store.resume_replication()
                self.service.store.wait_caught_up(timeout=5)
                self._send_json(200, {"paused": False, **self.service.replication_status()})
                return
            self._send_json(404, {"error": {"code": "not_found", "message": "路径不存在"}})
        except CredentialError as exc:
            self._send_json(exc.status, {"error": {"code": exc.code, "message": exc.message}})
        except ValidationError as exc:
            self._send_json(400, {"error": {"code": "invalid_request", "message": str(exc)}})
        except RetryLater as exc:
            self._send_json(
                503,
                {"error": {"code": exc.code, "message": exc.message}},
                {"Retry-After": exc.retry_after},
            )
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"error": {"code": "internal", "message": str(exc)}})


class RlwHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: TelemetryService):
        self.service = service
        super().__init__(address, _Handler)


def build_server(
    data_dir: str,
    host: str = "127.0.0.1",
    port: int = 19341,
    *,
    replica_wait_seconds: float = 1.0,
    max_primary_readers: int = 8,
    primary_read_delay: float = 0.0,
) -> RlwHTTPServer:
    store = ReplicatedStore(data_dir)
    service = TelemetryService(
        store,
        replica_wait_seconds=replica_wait_seconds,
        max_primary_readers=max_primary_readers,
        primary_read_delay=primary_read_delay,
    )
    return RlwHTTPServer((host, port), service)
