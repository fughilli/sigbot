import sys

import pytest

from sigbot.agent import build_llm_messages, compose_system, should_respond
from sigbot.listener import Incoming


def _row(direction, text, sender_name=None, has_attachments=False):
    return {"direction": direction, "via": "signal" if direction == "in" else "agent",
            "text": text, "sender": "u1", "sender_name": sender_name,
            "has_attachments": has_attachments}


def test_build_merges_consecutive_and_prefixes_senders():
    rows = [
        _row("in", "hi", "Kay"),
        _row("in", "you there?", "Jo"),
        _row("out", "yes!"),
        _row("in", "great", "Kay"),
    ]
    msgs = build_llm_messages(rows, [])
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0]["content"][0]["text"] == "Kay: hi\nJo: you there?"
    assert msgs[1]["content"][0]["text"] == "yes!"
    assert msgs[2]["content"][0]["text"] == "Kay: great"


def test_build_attaches_images_to_last_turn():
    msgs = build_llm_messages([_row("in", "look", "Kay", has_attachments=True)],
                              [(b"\xff\xd8", "image/jpeg")])
    blocks = msgs[-1]["content"]
    assert blocks[0]["type"] == "text" and blocks[1]["type"] == "image"
    assert blocks[1]["source"]["media_type"] == "image/jpeg"


def test_build_empty_or_assistant_tail_yields_nothing():
    assert build_llm_messages([], []) == []
    assert build_llm_messages([_row("out", "hello?")], []) == []


def _inc(policy, mentioned):
    return Incoming(service={"respond_to": policy, "label": "Opsy"}, sender="u",
                    sender_name="Kay", text="x", attachments=[], mentioned=mentioned)


@pytest.mark.parametrize("policy,mentioned,expect", [
    ("all", False, True),
    ("mention", False, False),
    ("mention", True, True),
    ("none", True, False),   # transport-only: never replies, even mentioned
    ("none", False, False),
])
def test_should_respond(policy, mentioned, expect):
    assert should_respond(_inc(policy, mentioned)) is expect


def test_compose_system_includes_persona():
    system = compose_system({"label": "Opsy", "group_name": "Ops Chat",
                             "system_prompt": "You watch deploys."})
    assert '"Opsy"' in system and '"Ops Chat"' in system
    assert "You watch deploys." in system


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
