"""rlw — 设备遥测补录/更正的读写一致性子系统（read-your-writes）。

组件：
- storage.ReplicatedStore：主库 + 真实只读副本 + 可暂停/恢复的复制回放线程
- credentials：设备级 HMAC 凭据（写入后签发，查询时对账水位）
- service.TelemetryService：读写接口、副本对账、主库回源与并发限流
- http_app：零依赖 HTTP 适配层
"""
from rlw.errors import CredentialError, RetryLater, ValidationError
from rlw.service import ReadResult, TelemetryService
from rlw.storage import ReplicatedStore

__all__ = [
    "ReplicatedStore",
    "TelemetryService",
    "ReadResult",
    "CredentialError",
    "RetryLater",
    "ValidationError",
]
__version__ = "1.0.0"
