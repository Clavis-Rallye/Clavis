import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import QaFeedbackMount, { MOUNT_JOIN_PARAM, MOUNT_KEY_STORAGE_KEY } from './QaFeedbackMount';
import { JOIN_PARAM, KEY_STORAGE_KEY } from './qaApi';
import { DEFAULT_INSTANCE_CONFIG, __resetInstanceConfigForTests } from '../config/instanceConfig';

// The factory runs only when something imports the bar module. That is how the
// test sees whether the lazy chunk was requested at all.
const bar = vi.hoisted(() => ({ loaded: vi.fn(), crash: false }));

vi.mock('./QaFeedbackBar', () => {
  bar.loaded();
  return {
    default: () => {
      if (bar.crash) throw new Error('boom');
      return <div data-testid="qa-bar-stub" />;
    },
  };
});

function renderMount(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <p>Produkt</p>
      <QaFeedbackMount />
    </MemoryRouter>,
  );
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

describe('QaFeedbackMount', () => {
  beforeEach(() => {
    bar.crash = false;
    localStorage.clear();
  });

  afterEach(() => {
    __resetInstanceConfigForTests();
    vi.restoreAllMocks();
  });

  it('uses the same storage key and parameter as the transport', () => {
    expect(MOUNT_KEY_STORAGE_KEY).toBe(KEY_STORAGE_KEY);
    expect(MOUNT_JOIN_PARAM).toBe(JOIN_PARAM);
  });

  // These three run before any test that loads the bar: once imported, the import cache keeps it.
  it('renders nothing and loads no bar chunk while the module is off, even with a key', async () => {
    __resetInstanceConfigForTests(DEFAULT_INSTANCE_CONFIG);
    localStorage.setItem(KEY_STORAGE_KEY, 'k-1');
    renderMount('/#qa-join=abc');
    await settle();
    expect(screen.queryByTestId('qa-bar-stub')).not.toBeInTheDocument();
    expect(bar.loaded).not.toHaveBeenCalled();
  });

  it('loads no bar chunk for a key in the query string', async () => {
    __resetInstanceConfigForTests({ ...DEFAULT_INSTANCE_CONFIG, qa_feedback: true });
    renderMount('/?qa-join=abc');
    await settle();
    expect(bar.loaded).not.toHaveBeenCalled();
  });

  it('loads no bar chunk in a browser without a key or a join link', async () => {
    __resetInstanceConfigForTests({ ...DEFAULT_INSTANCE_CONFIG, qa_feedback: true });
    renderMount('/dashboard');
    await settle();
    expect(screen.queryByTestId('qa-bar-stub')).not.toBeInTheDocument();
    expect(bar.loaded).not.toHaveBeenCalled();
  });

  it('loads the bar for a join link in the fragment', async () => {
    __resetInstanceConfigForTests({ ...DEFAULT_INSTANCE_CONFIG, qa_feedback: true });
    renderMount('/#qa-join=abc');
    expect(await screen.findByTestId('qa-bar-stub')).toBeInTheDocument();
    expect(bar.loaded).toHaveBeenCalledTimes(1);
  });

  it('loads the bar for a stored key', async () => {
    __resetInstanceConfigForTests({ ...DEFAULT_INSTANCE_CONFIG, qa_feedback: true });
    localStorage.setItem(KEY_STORAGE_KEY, 'k-1');
    renderMount('/dashboard');
    expect(await screen.findByTestId('qa-bar-stub')).toBeInTheDocument();
  });

  it('keeps the product alive when the bar crashes', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    bar.crash = true;
    __resetInstanceConfigForTests({ ...DEFAULT_INSTANCE_CONFIG, qa_feedback: true });
    localStorage.setItem(KEY_STORAGE_KEY, 'k-1');
    renderMount('/dashboard');
    await settle();
    expect(screen.getByText('Produkt')).toBeInTheDocument();
    expect(screen.queryByTestId('qa-bar-stub')).not.toBeInTheDocument();
  });
});
