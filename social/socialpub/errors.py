class PublishError(Exception):
    """A platform call failed.

    kind:
      transient  the platform did NOT take the post (rate limit, connection refused before sending).
                 Safe to retry automatically, with a wait.
      permanent  the platform rejected it (bad token, bad media, policy). Retrying without a change
                 will fail again, so it is not retried automatically.
      uncertain  the request may have been processed (timeout after sending, a 5xx on the create
                 call). NEVER retried automatically: it could create a duplicate. A person checks
                 the platform, then marks it published or retries.
    """

    def __init__(self, kind: str, message: str, status: int | None = None):
        assert kind in ("transient", "permanent", "uncertain")
        super().__init__(message)
        self.kind = kind
        self.status = status
