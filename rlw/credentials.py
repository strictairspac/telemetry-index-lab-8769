"""设备级凭据：主库写入成功后签发，查询时携带以声明读己之写水位。

令牌形态：rlwv1.<base64url(payload)>.<base64url(HMAC-SHA256(payload))>
payload = {"d": 设备ID, "s": 写入序列号(复制水位), "j": 随机ID, "e": 过期时间}

设计要点：
- HMAC 签名防伪造/防篡改，compare_digest 防时序比较；
- 序列号 s 即该笔写入在复制日志中的 seq，副本 applied_seq >= s 才允许副本返回；
- 设备绑定在 service 层强制（payload.d 必须等于查询设备），凭据不能跨设备使用。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from rlw.errors import CredentialError

PREFIX = "rlwv1"


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_credential(
    device_id: str,
    seq: int,
    secret: str,
    ttl_seconds: int = 3600,
    now: float | None = None,
) -> tuple[str, int]:
    """签发凭据，返回 (token, 过期epoch秒)。"""
    current = time.time() if now is None else now
    payload = {
        "d": device_id,
        "s": int(seq),
        "j": secrets.token_hex(8),
        "e": int(current + ttl_seconds),
    }
    body = _b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = hmac.new(
        secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{PREFIX}.{body}.{_b64encode(signature)}", payload["e"]


def verify_credential(token: str, secret: str, now: float | None = None) -> dict[str, Any]:
    """仅校验真伪与有效期；设备归属由 service 层判断。失败抛 CredentialError(401)。"""
    current = time.time() if now is None else now
    try:
        prefix, body, signature = str(token).split(".")
    except (ValueError, AttributeError):
        raise CredentialError(401, "invalid_credential", "凭据格式不正确")
    if prefix != PREFIX:
        raise CredentialError(401, "invalid_credential", "凭据版本不被识别")

    expected = hmac.new(
        secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).digest()
    try:
        given = _b64decode(signature)
    except Exception:
        raise CredentialError(401, "invalid_credential", "凭据编码损坏")
    if not hmac.compare_digest(expected, given):
        raise CredentialError(401, "invalid_credential", "凭据签名不匹配")

    try:
        payload = json.loads(_b64decode(body))
    except Exception:
        raise CredentialError(401, "invalid_credential", "凭据内容损坏")
    if not all(key in payload for key in ("d", "s", "e", "j")):
        raise CredentialError(401, "invalid_credential", "凭据声明不完整")
    if current >= payload["e"]:
        raise CredentialError(401, "credential_expired", "凭据已过期")
    return payload
