import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { pollWhileVisible } from './pollWhileVisible';

describe('pollWhileVisible', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('asks at most three times in a minute', async () => {
    const task = vi.fn().mockResolvedValue(undefined);
    const stop = pollWhileVisible(task);

    await vi.advanceTimersByTimeAsync(60_000);
    stop();

    // 30 s give or take 5 s: one or two calls in the first minute, never the
    // six of a ten-second poll. Five waiting phones used up the lookup's limit.
    expect(task.mock.calls.length).toBeGreaterThanOrEqual(1);
    expect(task.mock.calls.length).toBeLessThanOrEqual(3);
  });

  it('does not ask after it was stopped, and tells a late answer so', async () => {
    let finish: () => void = () => {};
    let stoppedAtAnswer: boolean | null = null;
    const task = vi.fn((isStopped: () => boolean) =>
      new Promise<void>((resolve) => {
        finish = () => {
          stoppedAtAnswer = isStopped();
          resolve();
        };
      }),
    );
    const stop = pollWhileVisible(task);
    await vi.advanceTimersByTimeAsync(36_000);
    expect(task).toHaveBeenCalledTimes(1);

    stop();
    finish();
    await vi.advanceTimersByTimeAsync(120_000);

    expect(stoppedAtAnswer).toBe(true);
    expect(task).toHaveBeenCalledTimes(1);
  });
});
