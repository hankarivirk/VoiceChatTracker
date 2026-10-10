"""Small helpers that keep the bot working across Pyrogram-family versions.

Pyrogram 2.x returns enums (ChatType.SUPERGROUP, ChatMemberStatus.OWNER, ...), which do
not compare equal to plain strings, while older code paths used strings.
"""


def enum_name(value) -> str:
    """Lower-cased name of an enum member, or the lower-cased string itself."""
    name = getattr(value, "name", None)
    return str(name if name is not None else value).lower()


def is_group_chat(chat) -> bool:
    return enum_name(getattr(chat, "type", "")) in ("group", "supergroup")


def is_admin_status(status) -> bool:
    return enum_name(status) in ("owner", "creator", "administrator")


def html_parse_mode():
    """ParseMode.HTML when Pyrogram is importable, otherwise the string form."""
    try:
        from pyrogram import enums
        return enums.ParseMode.HTML
    except Exception:
        return "html"
