import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/preact';
import { DeleteModal } from '../components/DeleteModal.jsx';

// Mock i18n to return keys as-is for testing
vi.mock('../lib/i18n.js', () => ({
  t: (key) => key,
}));

describe('DeleteModal', () => {
  const defaultProps = {
    job: { id: '1', title: 'Test Job' },
    onConfirm: vi.fn(),
    onClose: vi.fn(),
  };

  it('renders modal with dialog role', () => {
    const { container } = render(<DeleteModal {...defaultProps} />);
    expect(container.querySelector('[role="dialog"]')).toBeTruthy();
  });

  it('renders title and confirmation text', () => {
    const { getByText } = render(<DeleteModal {...defaultProps} />);
    expect(getByText('delete_title')).toBeTruthy();
    expect(getByText('delete_confirm')).toBeTruthy();
  });

  it('calls onConfirm when delete button clicked', () => {
    const onConfirm = vi.fn();
    const { getByText } = render(<DeleteModal {...defaultProps} onConfirm={onConfirm} />);
    fireEvent.click(getByText('delete_confirm_btn'));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when cancel button clicked', () => {
    const onClose = vi.fn();
    const { getByText } = render(<DeleteModal {...defaultProps} onClose={onClose} />);
    fireEvent.click(getByText('cancel'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when close (×) button clicked', () => {
    const onClose = vi.fn();
    const { container } = render(<DeleteModal {...defaultProps} onClose={onClose} />);
    fireEvent.click(container.querySelector('.modal-close'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when clicking the overlay background', () => {
    const onClose = vi.fn();
    const { container } = render(<DeleteModal {...defaultProps} onClose={onClose} />);
    const overlay = container.querySelector('.modal-overlay');
    // Click on overlay itself (not a child)
    fireEvent.click(overlay);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
