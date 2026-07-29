import sys

import pytest

from sigbot.listener import parse_envelope

BOT = "+15550001111"
SERVICE = {"id": 1, "name": "ops", "label": "Opsy", "group_id": "g1"}
SERVICES = {"g1": SERVICE}


def _env(text="hi", group="g1", source="+15559990000", name="Kay", **data_extra):
    data = {"message": text, "attachments": [],
            "groupInfo": {"groupId": group} if group else None, **data_extra}
    if group is None:
        data.pop("groupInfo")
    return {"sourceNumber": source, "sourceUuid": "uuid-1", "sourceName": name,
            "dataMessage": data}


def test_routes_registered_group():
    inc = parse_envelope(_env(), BOT, "Botsy", SERVICES)
    assert inc and inc.service is SERVICE
    assert inc.sender == "+15559990000" and inc.sender_name == "Kay"
    assert inc.text == "hi" and not inc.mentioned


def test_ignores_unregistered_group_and_dm():
    assert parse_envelope(_env(group="other"), BOT, "Botsy", SERVICES) is None
    assert parse_envelope(_env(group=None), BOT, "Botsy", SERVICES) is None


def test_ignores_own_echo_and_noise():
    assert parse_envelope(_env(source=BOT), BOT, "Botsy", SERVICES) is None
    assert parse_envelope({"sourceNumber": "+1x", "receiptMessage": {}},
                          BOT, "Botsy", SERVICES) is None
    assert parse_envelope(_env(text=""), BOT, "Botsy", SERVICES) is None


def test_uuid_only_sender():
    env = _env()
    env["sourceNumber"] = None
    inc = parse_envelope(env, BOT, "Botsy", SERVICES)
    assert inc and inc.sender == "uuid-1"


def test_mention_via_signal_mention():
    inc = parse_envelope(_env(mentions=[{"number": BOT, "start": 0, "length": 1}]),
                         BOT, "Botsy", SERVICES)
    assert inc and inc.mentioned


@pytest.mark.parametrize("text,mentioned", [
    ("hey opsy, status?", True),     # label, case-insensitive
    ("botsy wake up", True),         # bot name
    ("nothing to see", False),
])
def test_mention_via_name(text, mentioned):
    inc = parse_envelope(_env(text=text), BOT, "Botsy", SERVICES)
    assert inc and inc.mentioned == mentioned


def test_attachment_only_message_passes():
    env = _env(text="")
    env["dataMessage"]["attachments"] = [{"id": "a1", "contentType": "image/jpeg"}]
    inc = parse_envelope(env, BOT, "Botsy", SERVICES)
    assert inc and inc.attachments[0]["id"] == "a1"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
