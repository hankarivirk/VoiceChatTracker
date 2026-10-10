"""Permission checking utilities."""
from typing import List
from ..config import SUDO_USERS
from .telegram_compat import is_admin_status


def is_sudo(user_id: int) -> bool:
    """Check if user_id is in SUDO_USERS."""
    return user_id in SUDO_USERS


async def is_admin(client, chat_id: int, user_id: int) -> bool:
    """Check if user is group administrator or sudo user."""
    if is_sudo(user_id):
        return True
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return is_admin_status(member.status)
    except Exception:
        return False
