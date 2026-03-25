"""Nostr WebSocket client: message builders, parsers, and relay connection."""

import json
import asyncio
import aiohttp

from shared.crypto import nip04_encrypt, derive_pub
from shared.event import build_event


def build_req_filter(
    sub_id: str,
    kinds: list[int],
    tags: dict[str, list[str]] | None = None,
    limit: int | None = None,
) -> str:
    filter_obj: dict = {"kinds": kinds}
    if tags:
        filter_obj.update(tags)
    if limit is not None:
        filter_obj["limit"] = limit
    return json.dumps(["REQ", sub_id, filter_obj])


def build_event_message(event: dict) -> str:
    return json.dumps(["EVENT", event])


def build_close_message(sub_id: str) -> str:
    return json.dumps(["CLOSE", sub_id])


def parse_relay_message(raw: str) -> tuple[str, dict]:
    msg = json.loads(raw)
    msg_type = msg[0]

    if msg_type == "EVENT":
        return "EVENT", {"sub_id": msg[1], "event": msg[2]}
    elif msg_type == "EOSE":
        return "EOSE", {"sub_id": msg[1]}
    elif msg_type == "OK":
        return "OK", {
            "event_id": msg[1],
            "accepted": msg[2],
            "message": msg[3] if len(msg) > 3 else "",
        }
    elif msg_type == "NOTICE":
        return "NOTICE", {"message": msg[1]}
    elif msg_type == "CLOSED":
        return "CLOSED", {"sub_id": msg[1], "message": msg[2] if len(msg) > 2 else ""}
    else:
        return "UNKNOWN", {"raw": msg}


def _merge_events(event_lists: list[list[dict]]) -> list[dict]:
    """Merge events from multiple relays, deduplicate by event_id.

    Later duplicates (same id) override earlier ones.
    Result sorted by created_at descending (most recent first).
    """
    by_id: dict[str, dict] = {}
    for events in event_lists:
        for event in events:
            by_id[event["id"]] = event
    merged = list(by_id.values())
    merged.sort(key=lambda e: e.get("created_at", 0), reverse=True)
    return merged


async def fetch_events_from_relays(
    relay_urls: list[str],
    kinds: list[int],
    tags: dict | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Parallel query to multiple relays, merge and deduplicate results.

    Args:
        relay_urls: List of relay WebSocket URLs
        kinds: Event kinds to subscribe to
        tags: NIP-01 filter tags
        limit: Max events per relay (optional)

    Returns:
        Deduplicated list of events from all relays, sorted by created_at desc
    """
    async def fetch_from_relay(url: str) -> list[dict]:
        relay = NostrRelay(url)
        events = []
        try:
            await relay.connect()
            await relay.subscribe("_multi", kinds, tags, limit)
            async for event in relay.receive_events("_multi"):
                events.append(event)
            await relay.unsubscribe("_multi")
        finally:
            await relay.close()
        return events

    results = await asyncio.gather(*[fetch_from_relay(url) for url in relay_urls])
    return _merge_events(list(results))


class NostrRelay:
    """Single relay WebSocket connection."""

    def __init__(self, url: str):
        self.url = url
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None

    async def connect(self):
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self.url)

    async def close(self):
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()

    async def send(self, message: str):
        if not self._ws:
            raise RuntimeError("not connected")
        await self._ws.send_str(message)

    async def publish_event(self, event: dict) -> dict:
        await self.send(build_event_message(event))
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                msg_type, data = parse_relay_message(msg.data)
                if msg_type == "OK" and data["event_id"] == event["id"]:
                    return data
        return {"event_id": event["id"], "accepted": False, "message": "no response"}

    async def subscribe(self, sub_id: str, kinds: list[int], tags: dict | None = None, limit: int | None = None):
        req = build_req_filter(sub_id, kinds, tags, limit)
        await self.send(req)

    async def receive_events(self, sub_id: str):
        """Yield events until EOSE."""
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                msg_type, data = parse_relay_message(msg.data)
                if msg_type == "EVENT" and data["sub_id"] == sub_id:
                    yield data["event"]
                elif msg_type == "EOSE" and data["sub_id"] == sub_id:
                    break

    async def unsubscribe(self, sub_id: str):
        await self.send(build_close_message(sub_id))

    async def send_dm(self, sender_privkey: str, recipient_pubkey: str, plaintext: str) -> dict:
        """Send an encrypted NIP-04 DM to a recipient.

        Args:
            sender_privkey: Sender's private key hex
            recipient_pubkey: Recipient's public key hex
            plaintext: Message to encrypt

        Returns:
            Result dict with event_id, accepted, message
        """
        ciphertext = nip04_encrypt(plaintext, sender_privkey, recipient_pubkey)
        sender_pubkey = derive_pub(sender_privkey)
        event = build_event(
            kind=4,
            content=ciphertext,
            privkey=sender_privkey,
            pubkey=sender_pubkey,
            tags=[["p", recipient_pubkey]],
        )
        return await self.publish_event(event)
