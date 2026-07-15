import sys

import pytest

from finder.bot.listener import parse_envelope

USER = "+15551234567"
GROUP = "grp-internal-id=="


def env(source=USER, message="hi", group=GROUP, attachments=None, data=True):
    e = {"sourceNumber": source}
    if data:
        dm = {"message": message, "attachments": attachments or []}
        if group is not None:
            dm["groupInfo"] = {"groupId": group}
        e["dataMessage"] = dm
    return e


def test_stranger_is_silently_dropped():
    assert parse_envelope(env(source="+19998887777"), USER, GROUP) is None


def test_receipts_and_sync_noise_dropped():
    assert parse_envelope(env(data=False), USER, GROUP) is None
    assert parse_envelope(env(message="", attachments=[]), USER, GROUP) is None


def test_group_message_accepted():
    inc = parse_envelope(env(message="pause the couch search"), USER, GROUP)
    assert inc and inc.text == "pause the couch search"


def test_other_group_ignored():
    assert parse_envelope(env(group="another-group=="), USER, GROUP) is None


def test_dm_flagged_for_redirect():
    inc = parse_envelope(env(group=None), USER, GROUP)
    assert inc and inc.text == "__dm__"


def test_attachment_only_message_accepted():
    inc = parse_envelope(
        env(message="", attachments=[{"id": "a1", "contentType": "image/jpeg"}]),
        USER, GROUP,
    )
    assert inc and inc.attachments[0]["id"] == "a1"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
