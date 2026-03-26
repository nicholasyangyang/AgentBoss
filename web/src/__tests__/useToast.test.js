import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/preact';
import { useToast } from '../hooks/useToast.js';

describe('useToast', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it('starts with empty toasts', () => {
    const { result } = renderHook(() => useToast());
    expect(result.current.toasts).toEqual([]);
  });

  it('adds a success toast', () => {
    const { result } = renderHook(() => useToast());
    act(() => result.current.showToast('Job posted'));
    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0].message).toBe('Job posted');
    expect(result.current.toasts[0].type).toBe('success');
  });

  it('adds an error toast', () => {
    const { result } = renderHook(() => useToast());
    act(() => result.current.showToast('Failed', 'error'));
    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0].type).toBe('error');
  });

  it('auto-dismisses success toasts after 3 seconds', () => {
    const { result } = renderHook(() => useToast());
    act(() => result.current.showToast('Done'));
    expect(result.current.toasts).toHaveLength(1);

    act(() => vi.advanceTimersByTime(3000));
    expect(result.current.toasts).toHaveLength(0);
  });

  it('does NOT auto-dismiss error toasts', () => {
    const { result } = renderHook(() => useToast());
    act(() => result.current.showToast('Error!', 'error'));

    act(() => vi.advanceTimersByTime(5000));
    expect(result.current.toasts).toHaveLength(1);
  });

  it('dismissToast removes a specific toast', () => {
    const { result } = renderHook(() => useToast());
    act(() => result.current.showToast('A', 'error'));
    vi.advanceTimersByTime(1); // ensure different Date.now() IDs
    act(() => result.current.showToast('B', 'error'));
    expect(result.current.toasts).toHaveLength(2);

    const idToRemove = result.current.toasts[0].id;
    act(() => result.current.dismissToast(idToRemove));
    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0].message).toBe('B');
  });
});
