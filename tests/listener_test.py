import sys

import pytest

from finder.bot.listener import parse_row

USER_UUID = "7e57ab1e-0000-4000-8000-000000000000"


def row(sender=USER_UUID, text="hi", direction="in", attachments=None):
    return {"id": 1, "direction": direction, "via": "signal", "sender": sender,
            "sender_name": "Kay", "text": text,
            "attachments": attachments or [], "has_attachments": bool(attachments)}


def test_stranger_is_silently_dropped():
    assert parse_row(row(sender="other-uuid"), USER_UUID) is None
    assert parse_row(row(sender=None), USER_UUID) is None


def test_user_message_accepted():
    inc = parse_row(row(text="pause the couch search"), USER_UUID)
    assert inc and inc.text == "pause the couch search"


def test_outgoing_echoes_dropped():
    # the finder's own sends come back through the log as direction='out'
    assert parse_row(row(direction="out", sender=None), USER_UUID) is None


def test_empty_message_dropped():
    assert parse_row(row(text=""), USER_UUID) is None


def test_attachment_only_message_accepted():
    inc = parse_row(row(text="", attachments=[{"id": "a1", "contentType": "image/jpeg"}]),
                    USER_UUID)
    assert inc and inc.attachments[0]["id"] == "a1"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
