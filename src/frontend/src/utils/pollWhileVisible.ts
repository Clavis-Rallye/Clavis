/** The base interval and the spread around it. */
const EVERY_MS = 30_000;
const JITTER_MS = 10_000;

/**
 * Calls `task` about every 30 seconds while the tab is visible, and returns
 * the function that stops it.
 *
 * Built for the pages where a group waits for a run to start. The whole
 * group often sits behind one address, the school's or the city's Wi-Fi, and
 * the public lookup is limited per address. So each phone asks rarely, at its
 * own random offset so that thirty phones do not ask in the same second, and
 * not at all while its tab is hidden. A tab that becomes visible asks at once.
 *
 * `task` gets a function that says whether the poll was stopped. A task
 * that acts on an answer, for example one that creates something, asks it
 * first: the answer of a call that was still out may arrive after the page
 * is gone.
 */
export function pollWhileVisible(task: (isStopped: () => boolean) => Promise<unknown>): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const next = () => {
    clearTimeout(timer);
    timer = setTimeout(run, EVERY_MS - JITTER_MS / 2 + Math.random() * JITTER_MS);
  };
  const run = () => {
    if (stopped) return;
    if (document.visibilityState === 'hidden') {
      next();
      return;
    }
    void task(() => stopped).finally(() => {
      if (!stopped) next();
    });
  };
  const onVisible = () => {
    if (!stopped && document.visibilityState === 'visible') run();
  };

  document.addEventListener('visibilitychange', onVisible);
  next();

  return () => {
    stopped = true;
    clearTimeout(timer);
    document.removeEventListener('visibilitychange', onVisible);
  };
}
