import json
import pytest
from unittest.mock import AsyncMock, patch
from cli.nostr_client import (
    build_req_filter,
    build_event_message,
    build_close_message,
    parse_relay_message,
    NostrRelay,
    _merge_events,
)
from shared.crypto import gen_keys, nip04_encrypt
from shared.event import build_event


class TestMessageBuilders:
    def test_build_req_filter_basic(self):
        req = build_req_filter(
            sub_id="sub1",
            kinds=[30078],
            tags={"#t": ["agentboss", "job"]},
        )
        parsed = json.loads(req)
        assert parsed[0] == "REQ"
        assert parsed[1] == "sub1"
        assert parsed[2]["kinds"] == [30078]
        assert parsed[2]["#t"] == ["agentboss", "job"]

    def test_build_req_filter_with_province(self):
        req = build_req_filter(
            sub_id="sub2",
            kinds=[30078],
            tags={"#t": ["agentboss", "job"], "#province": ["1"]},
        )
        parsed = json.loads(req)
        assert parsed[2]["#province"] == ["1"]

    def test_build_req_filter_with_limit(self):
        req = build_req_filter(sub_id="s", kinds=[30078], limit=50)
        parsed = json.loads(req)
        assert parsed[2]["limit"] == 50

    def test_build_event_message(self):
        priv, pub = gen_keys()
        ev = build_event(kind=1, content="hello", privkey=priv, pubkey=pub)
        msg = build_event_message(ev)
        parsed = json.loads(msg)
        assert parsed[0] == "EVENT"
        assert parsed[1]["id"] == ev["id"]

    def test_build_close_message(self):
        msg = build_close_message("sub1")
        parsed = json.loads(msg)
        assert parsed == ["CLOSE", "sub1"]


class TestParseRelayMessage:
    def test_parse_event_message(self):
        raw = json.dumps(["EVENT", "sub1", {"id": "abc", "kind": 30078}])
        msg_type, data = parse_relay_message(raw)
        assert msg_type == "EVENT"
        assert data["sub_id"] == "sub1"
        assert data["event"]["id"] == "abc"

    def test_parse_eose_message(self):
        raw = json.dumps(["EOSE", "sub1"])
        msg_type, data = parse_relay_message(raw)
        assert msg_type == "EOSE"
        assert data["sub_id"] == "sub1"

    def test_parse_ok_accepted(self):
        raw = json.dumps(["OK", "eventid1", True, ""])
        msg_type, data = parse_relay_message(raw)
        assert msg_type == "OK"
        assert data["event_id"] == "eventid1"
        assert data["accepted"] is True

    def test_parse_ok_rejected(self):
        raw = json.dumps(["OK", "eventid1", False, "blocked: not on whitelist"])
        msg_type, data = parse_relay_message(raw)
        assert msg_type == "OK"
        assert data["accepted"] is False
        assert "whitelist" in data["message"]

    def test_parse_notice(self):
        raw = json.dumps(["NOTICE", "some error"])
        msg_type, data = parse_relay_message(raw)
        assert msg_type == "NOTICE"
        assert data["message"] == "some error"

    def test_parse_unknown(self):
        raw = json.dumps(["UNKNOWN", "data"])
        msg_type, data = parse_relay_message(raw)
        assert msg_type == "UNKNOWN"


class TestSendDM:
    @pytest.mark.asyncio
    async def test_send_dm_builds_encrypted_kind4_event(self):
        """send_dm encrypts content and sends a kind:4 DM event."""
        alice_priv, alice_pub = gen_keys()
        bob_priv, bob_pub = gen_keys()

        relay = NostrRelay("wss://example.com")
        relay.publish_event = AsyncMock(return_value={"event_id": "test123", "accepted": True, "message": ""})

        result = await relay.send_dm(alice_priv, bob_pub, "Hello, Bob!")

        # Verify publish_event was called with a kind:4 event
        published_event = relay.publish_event.call_args[0][0]
        assert published_event["kind"] == 4
        assert published_event["pubkey"] == alice_pub
        assert published_event["tags"] == [["p", bob_pub]]

        # Content should be NIP-04 encrypted (Base64)
        import base64
        combined = base64.b64decode(published_event["content"])
        assert len(combined) > 48  # 32 (ephem_pub_x) + 16 (iv) + min ciphertext

        # Verify result
        assert result["event_id"] == "test123"
        assert result["accepted"] is True


class TestMergeEvents:
    def test_merge_events_deduplicates_by_id(self):
        """_merge_events removes duplicates by event_id, later overrides earlier."""
        events1 = [
            {"id": "abc", "created_at": 1000, "content": "v1"},
            {"id": "def", "created_at": 1002, "content": "v2"},
        ]
        events2 = [
            {"id": "abc", "created_at": 1000, "content": "v1"},  # duplicate
            {"id": "ghi", "created_at": 1001, "content": "v3"},
        ]
        result = _merge_events([events1, events2])
        ids = [e["id"] for e in result]
        assert set(ids) == {"abc", "def", "ghi"}
        # Should be sorted by created_at desc
        assert result[0]["id"] == "def"  # created_at:1002
        assert result[1]["id"] == "ghi"  # created_at:1001
        assert result[2]["id"] == "abc"  # created_at:1000

    def test_merge_events_empty_lists(self):
        """_merge_events handles empty input."""
        result = _merge_events([])
        assert result == []
        result = _merge_events([[]])
        assert result == []
