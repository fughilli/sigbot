import sys

import pytest

from sigbot import auth
from sigbot.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def _mk_service(store, name="ops", group="g1"):
    return store.create_service(
        name=name, group_id=group, group_send_id=f"group.{group}",
        group_name="Test Group", label="Opsy", system_prompt="You are Opsy.")


def test_service_lifecycle(store):
    s = _mk_service(store)
    assert s["id"] and s["prefix_label"] is True and s["enabled"] is True
    assert store.get_service_by_group("g1")["name"] == "ops"
    assert store.get_service_by_name("ops")["id"] == s["id"]

    updated = store.update_service(s["id"], label="Deputy", respond_to="mention",
                                   prefix_label=False)
    assert updated["label"] == "Deputy" and updated["prefix_label"] is False

    with pytest.raises(ValueError):
        store.update_service(s["id"], group_id="other")  # immutable

    store.update_service(s["id"], enabled=False)
    assert store.services_by_group() == {}
    assert "g1" in store.services_by_group(enabled_only=False)

    assert store.delete_service(s["id"])
    assert store.get_service(s["id"]) is None


def test_service_unique_constraints(store):
    _mk_service(store)
    with pytest.raises(Exception):
        _mk_service(store, name="other", group="g1")   # group taken
    with pytest.raises(Exception):
        _mk_service(store, name="ops", group="g2")     # name taken


def test_api_key_lifecycle(store):
    s = _mk_service(store)
    key, key_hash = auth.new_api_key()
    row = store.add_api_key(s["id"], key_hash, label="ci")
    assert key.startswith("sb_") and key not in str(row)  # plaintext never stored

    resolved = store.service_for_key(auth.hash_api_key(key))
    assert resolved["id"] == s["id"]
    assert store.list_api_keys(s["id"])[0]["last_used_at"]  # touched

    assert store.service_for_key(auth.hash_api_key("sb_wrong")) is None

    store.update_service(s["id"], enabled=False)
    assert store.service_for_key(auth.hash_api_key(key)) is None  # disabled service

    store.update_service(s["id"], enabled=True)
    assert store.revoke_api_key(row["id"])
    assert not store.revoke_api_key(row["id"])  # already revoked
    assert store.service_for_key(auth.hash_api_key(key)) is None


def test_keys_cascade_with_service(store):
    s = _mk_service(store)
    _, key_hash = auth.new_api_key()
    store.add_api_key(s["id"], key_hash)
    store.delete_service(s["id"])
    assert store.service_for_key(key_hash) is None


def test_admin_and_sessions(store):
    assert store.count_admins() == 0
    store.upsert_admin("k", auth.hash_password("hunter22"))
    assert auth.verify_password("hunter22", store.get_admin("k")["password_hash"])
    assert not auth.verify_password("wrong", store.get_admin("k")["password_hash"])

    token, token_hash = auth.new_session_token()
    store.create_session(token_hash, "k")
    assert store.session_user(token_hash) == "k"
    assert store.session_user(auth.hash_session_token("forged")) is None
    store.delete_session(token_hash)
    assert store.session_user(token_hash) is None


def test_message_attachments_and_scoping(store):
    s = _mk_service(store)
    other = _mk_service(store, name="other", group="g2")
    store.append_message(s["id"], "in", "signal", "look at this", sender="u1",
                         attachments=[{"id": "att-1", "contentType": "image/jpeg"}])
    msg = store.recent_messages(s["id"])[0]
    assert msg["has_attachments"] and msg["attachments"][0]["id"] == "att-1"

    assert store.service_has_attachment(s["id"], "att-1")
    assert not store.service_has_attachment(s["id"], "att-2")
    assert not store.service_has_attachment(other["id"], "att-1")  # other service


def test_messages_and_cursor(store):
    s = _mk_service(store)
    first = store.append_message(s["id"], "in", "signal", "hi", sender="u1",
                                 sender_name="Kay")
    store.append_message(s["id"], "out", "agent", "hello Kay")
    msgs = store.recent_messages(s["id"])
    assert [m["text"] for m in msgs] == ["hi", "hello Kay"]  # oldest first

    newer = store.recent_messages(s["id"], after_id=first["id"])
    assert [m["text"] for m in newer] == ["hello Kay"]
    assert store.recent_messages(s["id"], after_id=newer[-1]["id"]) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
