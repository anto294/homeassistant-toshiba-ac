"""Errors of the Toshiba client."""

from __future__ import annotations

import math
from typing import Any

from ..const import DOMAIN


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


# ---------------------------
#   translation_of
# ---------------------------
def translation_of(error: ToshibaError) -> dict[str, Any]:
    """Return the arguments that show an error to a user, in the user's language.

    The text of an error of this client stays in English, for the log. It can name
    details that mean nothing to a user, so it is not a placeholder.
    """
    placeholders: dict[str, str] = {}
    if isinstance(error, ToshibaAuthError):
        key = "token_rejected"
    elif isinstance(error, ToshibaBlockedError):
        key = "blocked"
        placeholders["minutes"] = str(max(1, math.ceil(error.retry_after / 60)))
    elif isinstance(error, ToshibaCommandError):
        key = "command_not_delivered"
    else:
        key = "cannot_connect"
    return {
        "translation_domain": DOMAIN,
        "translation_key": key,
        "translation_placeholders": placeholders,
    }
