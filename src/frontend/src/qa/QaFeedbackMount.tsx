/**
 * Mount point of the feedback bar. It renders and loads nothing while
 * `GET /api/public/config` reports `qa_feedback: false`. When it is on, the bar
 * loads as its own lazy chunk, and only in a browser that holds a join key or
 * opens a join link (`#qa-join=<key>`). A stranger on a demo system loads no
 * byte of it.
 *
 * A crash of this tester tool, or a chunk that fails to load, must never take
 * the product down, so a boundary of its own swallows both.
 */
import { Component, lazy, Suspense, useState, type ReactNode } from 'react';
import { useLocation } from 'react-router';
import { useInstanceConfig } from '../config/instanceConfig';

const QaFeedbackBar = lazy(() => import('./QaFeedbackBar'));

// The same names as `KEY_STORAGE_KEY` and `JOIN_PARAM` in qaApi.ts, repeated
// here so the main bundle does not pull in the bar's transport. A test keeps
// the two in step.
export const MOUNT_KEY_STORAGE_KEY = 'clavis.qa.key';
export const MOUNT_JOIN_PARAM = 'qa-join';

function holdsKey(hash: string): boolean {
  if (new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash).get(MOUNT_JOIN_PARAM)) return true;
  try {
    return Boolean(localStorage.getItem(MOUNT_KEY_STORAGE_KEY));
  } catch {
    return false;
  }
}

class QaBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: unknown): void {
    console.error('[qa] the feedback bar failed and is switched off for this page', error);
  }

  render(): ReactNode {
    return this.state.failed ? null : this.props.children;
  }
}

export default function QaFeedbackMount() {
  const { qa_feedback: enabled } = useInstanceConfig();
  const location = useLocation();
  // Once loaded, the bar stays mounted: it drops its own key and renders
  // nothing when the session ends.
  const [joined, setJoined] = useState(() => holdsKey(location.hash));
  if (!joined && holdsKey(location.hash)) setJoined(true);
  if (!enabled || !joined) return null;
  return (
    <QaBoundary>
      <Suspense fallback={null}>
        <QaFeedbackBar />
      </Suspense>
    </QaBoundary>
  );
}
