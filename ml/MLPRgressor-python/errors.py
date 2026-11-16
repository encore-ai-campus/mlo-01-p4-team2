"""Safe job diagnostics. Never attach raw SDK exception messages."""
class JobError(RuntimeError):
    def __init__(self, code, *, retryable=False, deferred=False, retry_after=0,
                 request_id=None, details=None):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.deferred = deferred
        self.retry_after = retry_after
        self.request_id = request_id
        self.details = details or {}
