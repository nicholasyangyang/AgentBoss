import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent } from '@testing-library/preact';
import { ThemeToggle } from '../components/ThemeToggle.jsx';

describe('ThemeToggle', () => {
  beforeEach(() => {
    document.documentElement.classList.remove('dark');
    localStorage.clear();
  });

  it('renders a button with theme-toggle class', () => {
    const { container } = render(<ThemeToggle />);
    expect(container.querySelector('.theme-toggle')).toBeTruthy();
  });

  it('shows moon icon when in light mode', () => {
    const { container } = render(<ThemeToggle />);
    expect(container.querySelector('.theme-toggle').textContent).toContain('🌙');
  });

  it('toggles to dark mode on click', () => {
    const { container } = render(<ThemeToggle />);
    const btn = container.querySelector('.theme-toggle');
    fireEvent.click(btn);
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(localStorage.getItem('agentboss_theme')).toBe('dark');
  });

  it('toggles back to light mode on second click', () => {
    const { container } = render(<ThemeToggle />);
    const btn = container.querySelector('.theme-toggle');
    fireEvent.click(btn); // → dark
    fireEvent.click(btn); // → light
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    expect(localStorage.getItem('agentboss_theme')).toBe('light');
  });

  it('shows sun icon when in dark mode', () => {
    document.documentElement.classList.add('dark');
    const { container } = render(<ThemeToggle />);
    expect(container.querySelector('.theme-toggle').textContent).toContain('☀️');
  });

  it('has accessible aria-label', () => {
    const { container } = render(<ThemeToggle />);
    const btn = container.querySelector('.theme-toggle');
    expect(btn.getAttribute('aria-label')).toBe('Switch to dark mode');
  });
});
