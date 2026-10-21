"""The base of the errors the UI reports to the person using it."""


class Problem(Exception):
    """An error whose message was written for the person using the UI: what went wrong and what to do about
    it. The API sends .message and nothing else of the exception (no traceback, no wrapped error)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
