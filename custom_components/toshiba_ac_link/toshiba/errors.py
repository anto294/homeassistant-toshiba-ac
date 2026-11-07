"""Errors of the Toshiba client."""

from __future__ import annotations


# ---------------------------
#   ToshibaError
# ---------------------------
class ToshibaError(Exception):
    """A request to Toshiba did not succeed."""


# ---------------------------
#   ToshibaConnectionError
# ---------------------------
class ToshibaConnectionError(ToshibaError):
    """Toshiba could not be reached, or gave an answer that cannot be used."""


# ---------------------------
#   ToshibaAuthError
# ---------------------------
class ToshibaAuthError(ToshibaError):
    """Toshiba rejected the password or the token. Only the user can solve this."""


# ---------------------------
#   ToshibaBlockedError
# ---------------------------
class ToshibaBlockedError(ToshibaError):
    """A request was not sent, or must not be repeated, for retry_after seconds."""

    def __init__(self, message: str, retry_after: float) -> None:
        """Store how long to wait."""
        super().__init__(message)
        self.retry_after = retry_after


# ---------------------------
#   ToshibaLoginLimitError
# ---------------------------
class ToshibaLoginLimitError(ToshibaBlockedError):
    """Too many logins failed recently, so another one could lock the account."""


# ---------------------------
#   ToshibaCommandError
# ---------------------------
class ToshibaCommandError(ToshibaError):
    """Toshiba did not deliver a command to its unit."""
