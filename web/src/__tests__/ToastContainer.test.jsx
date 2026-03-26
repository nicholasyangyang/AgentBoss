import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/preact';
import { ToastContainer } from '../components/ToastContainer.jsx';

describe('ToastContainer', () => {
  it('returns null when toasts array is empty', () => {
    const { container } = render(<ToastContainer toasts={[]} onDismiss={() => {}} />);
    expect(container.innerHTML).toBe('');
  });

  it('renders success toast with message', () => {
    const toasts = [{ id: 1, message: 'Job posted!', type: 'success' }];
    const { getByText } = render(<ToastContainer toasts={toasts} onDismiss={() => {}} />);
    expect(getByText('Job posted!')).toBeTruthy();
  });

  it('renders error toast with close button', () => {
    const toasts = [{ id: 1, message: 'Failed', type: 'error' }];
    const { container } = render(<ToastContainer toasts={toasts} onDismiss={() => {}} />);
    expect(container.querySelector('.toast-error')).toBeTruthy();
    expect(container.querySelector('.toast-close-btn')).toBeTruthy();
  });

  it('success toast has no close button', () => {
    const toasts = [{ id: 1, message: 'Done', type: 'success' }];
    const { container } = render(<ToastContainer toasts={toasts} onDismiss={() => {}} />);
    expect(container.querySelector('.toast-close-btn')).toBeNull();
  });

  it('calls onDismiss when error toast close button clicked', () => {
    const onDismiss = vi.fn();
    const toasts = [{ id: 42, message: 'Error!', type: 'error' }];
    const { container } = render(<ToastContainer toasts={toasts} onDismiss={onDismiss} />);
    fireEvent.click(container.querySelector('.toast-close-btn'));
    expect(onDismiss).toHaveBeenCalledWith(42);
  });

  it('renders multiple toasts', () => {
    const toasts = [
      { id: 1, message: 'A', type: 'success' },
      { id: 2, message: 'B', type: 'error' },
    ];
    const { container } = render(<ToastContainer toasts={toasts} onDismiss={() => {}} />);
    expect(container.querySelectorAll('.toast')).toHaveLength(2);
  });
});
