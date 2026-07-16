import sys

import pytest

from finder.bot.listener import parse_envelope

USER = "+15551234567"
USER_UUID = "7e57ab1e-0000-4000-8000-000000000000"
GROUP = "grp-internal-id=="


def env(source=USER, message="hi", group=GROUP, attachments=None, data=True,
        source_uuid=None):
    e = {"sourceNumber": source, "sourceUuid": source_uuid}
    if data:
        dm = {"message": message, "attachments": attachments or []}
        if group is not None:
            dm["groupInfo"] = {"groupId": group}
        e["dataMessage"] = dm
    return e


def test_stranger_is_silently_dropped():
    assert parse_envelope(env(source="+19998887777"), USER, GROUP) is None


def test_uuid_allowlist():
    # number-sharing disabled: sourceNumber is null, uuid identifies the user
    e = env(source=None, source_uuid=USER_UUID)
    assert parse_envelope(e, USER_UUID, GROUP).text == "hi"
    assert parse_envelope(e, USER, GROUP) is None
    # stranger's uuid doesn't match
    assert parse_envelope(env(source=None, source_uuid="other-uuid"), USER_UUID, GROUP) is None


def test_null_user_id_never_matches_null_source():
    # paranoia: an envelope with both source fields null must not match anything
    e = env(source=None, source_uuid=None)
    assert parse_envelope(e, USER, GROUP) is None


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
