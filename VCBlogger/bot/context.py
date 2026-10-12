"""Who is asking, and where. Built by the Telegram layer, used by router and screens."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Ctx:
    user_id: int
    chat_id: int
    is_group: bool
    first_name: str = ""
    username: str = ""
    is_owner: bool = False


class AccessDenied(Exception):
    """Raised when a command is not allowed here; the message is shown to the user."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
