"""子系统统一错误类型，HTTP 层据此映射状态码。"""


class CredentialError(Exception):
    """凭据校验失败。401=无效/过期，403=跨设备使用。"""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class RetryLater(Exception):
    """副本落后且主库回源并发槽已满，调用方应稍后重试。"""

    def __init__(self, retry_after: int = 1, message: str = "主库回源并发已满，请稍后重试"):
        super().__init__(message)
        self.retry_after = retry_after
        self.code = "retry_later"
        self.message = message


class ValidationError(ValueError):
    """请求参数非法，映射 400。"""
