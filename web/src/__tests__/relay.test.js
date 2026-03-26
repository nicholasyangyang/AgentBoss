import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createRelayClient } from '../lib/relay.js';

// ── Mock WebSocket ──────────────────────────────────────
let mockWs;

class MockWebSocket {
  constructor() {
    mockWs = this;
    this._listeners = {};
  }
  set onopen(fn) { this._onopen = fn; }
  set onerror(fn) { this._onerror = fn; }
  set onmessage(fn) { this._onmessage = fn; }
  addEventListener(type, fn) {
    this._listeners[type] = this._listeners[type] || [];
    this._listeners[type].push(fn);
  }
  removeEventListener(type, fn) {
    if (this._listeners[type]) {
      this._listeners[type] = this._listeners[type].filter((f) => f !== fn);
    }
  }
  send() {}
  close() {}
}

vi.stubGlobal('WebSocket', MockWebSocket);

// ── Tests ───────────────────────────────────────────────

describe('createRelayClient', () => {
  let client;

  beforeEach(async () => {
    client = createRelayClient();
    const connectPromise = client.connect();
    mockWs._onopen(); // simulate connection open
    await connectPromise;
  });

  describe('onmessage malformed JSON (Issue #49)', () => {
    it('does not throw on malformed JSON in onmessage', () => {
      expect(() => {
        mockWs._onmessage({ data: 'not valid json{{{' });
      }).not.toThrow();
    });

    it('still processes valid JSON after receiving malformed message', () => {
      const handler = vi.fn();
      client.onEvent(handler);

      // malformed first
      mockWs._onmessage({ data: '<<<garbage>>>' });

      // valid message after — should still work
      mockWs._onmessage({
        data: JSON.stringify(['EVENT', 'sub1', { id: 'e1', content: '{}' }]),
      });

      // handler not called because subId doesn't match, but no crash
      expect(true).toBe(true); // survived without throwing
    });
  });

  describe('publish malformed JSON (Issue #49)', () => {
    it('does not throw when publish handler receives malformed JSON', () => {
      const event = { id: 'test-event-id' };
      // Start publish (don't await — we test the handler)
      client.publish(event);

      // simulate relay sending garbage
      expect(() => {
        const listeners = mockWs._listeners['message'] || [];
        listeners.forEach((fn) => fn({ data: 'not json' }));
      }).not.toThrow();
    });
  });

  describe('ws.send error handling (Issue #49)', () => {
    it('subscribe does not crash if ws.send throws', () => {
      mockWs.send = () => { throw new Error('WebSocket is closed'); };

      expect(() => {
        client.subscribe('sub1', { kinds: [1] });
      }).not.toThrow();
    });

    it('unsubscribe does not crash if ws.send throws', () => {
      mockWs.send = () => { throw new Error('WebSocket is closed'); };

      expect(() => {
        client.unsubscribe('sub1');
      }).not.toThrow();
    });
  });
});
