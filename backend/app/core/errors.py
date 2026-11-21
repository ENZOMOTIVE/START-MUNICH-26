class DomainError(Exception):
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


class NotFoundError(DomainError):
    def __init__(self, message: str = "not_found"):
        super().__init__("not_found", message)


class LiveActionsDisabled(DomainError):
    def __init__(self) -> None:
        super().__init__(
            "live_actions_disabled",
            "Live actions are disabled unless APP_MODE=live and LIVE_ACTIONS_ENABLED=true",
        )


class ConflictError(DomainError):
    def __init__(self, code: str, message: str | None = None):
        super().__init__(code, message)
