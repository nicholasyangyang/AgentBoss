import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/preact';
import { useAuth } from '../hooks/useAuth.js';

// Mock nostr.js
vi.mock('../lib/nostr.js', () => ({
  hasSigner: vi.fn(),
  getPublicKey: vi.fn(),
  shortPubkey: vi.fn((hex) => hex ? `${hex.slice(0, 8)}…` : ''),
}));

import { hasSigner, getPublicKey } from '../lib/nostr.js';

describe('useAuth', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('sets loading=false when no signer', async () => {
    hasSigner.mockReturnValue(false);
    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.pubkey).toBeNull();
    expect(result.current.hasSigner).toBe(false);
  });

  it('fetches pubkey when signer available', async () => {
    hasSigner.mockReturnValue(true);
    getPublicKey.mockResolvedValue('a'.repeat(64));
    const { result } = renderHook(() => useAuth());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.pubkey).toBe('a'.repeat(64));
    expect(result.current.shortPubkey).toBe('aaaaaaaa…');
  });

  it('sets error when getPublicKey rejects', async () => {
    hasSigner.mockReturnValue(true);
    getPublicKey.mockRejectedValue(new Error('User denied'));
    const { result } = renderHook(() => useAuth());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe('User denied');
    expect(result.current.pubkey).toBeNull();
  });
});
