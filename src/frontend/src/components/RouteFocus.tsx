import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router';

// Lazy pages (App.tsx's `lazy()` routes) mount their heading only after the
// first paint, so a single rAF can still land before the real DOM is there.
function waitTwoAnimationFrames(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => resolve());
    });
  });
}

function isEditableElement(element: Element | null): boolean {
  if (!element) {
    return false;
  }
  if (element.tagName === 'INPUT' || element.tagName === 'TEXTAREA') {
    return true;
  }
  return (element as HTMLElement).isContentEditable;
}

// Data-driven pages like the dashboard render their h1 only once their query
// has resolved — long after the two frames above. The MutationObserver that
// bridges that gap must not wait forever.
const LATE_HEADING_TIMEOUT_MS = 3000;

function focusElement(target: HTMLElement): void {
  if (!target.hasAttribute('tabindex')) {
    target.tabIndex = -1;
  }
  target.focus({ preventScroll: false });
}

/**
 * Moves focus to the new page's heading on every route change, so a screen
 * reader announces it. Renders nothing.
 */
export default function RouteFocus() {
  const { pathname } = useLocation();
  // Seeded with the current pathname (not a separate "first render" flag) so
  // React 19 StrictMode's mount→cleanup→mount replay of this same commit
  // compares the same pathname to itself both times and never treats the
  // initial load as a change.
  const previousPathname = useRef(pathname);

  useEffect(() => {
    const changed = previousPathname.current !== pathname;
    previousPathname.current = pathname;
    if (!changed) {
      return;
    }

    const elementAtRouteChange = document.activeElement;
    let cancelled = false;
    let lateHeadingObserver: MutationObserver | null = null;
    let lateHeadingTimeoutId: number | undefined;

    void waitTwoAnimationFrames().then(() => {
      if (cancelled) {
        return;
      }

      const active = document.activeElement;
      if (active instanceof Element && active.closest('[role="dialog"]')) {
        return;
      }
      if (isEditableElement(active) && active !== elementAtRouteChange) {
        return;
      }

      const main = document.querySelector<HTMLElement>('main');
      if (!main) {
        return;
      }

      const heading = main.querySelector<HTMLElement>('h1');
      if (heading) {
        focusElement(heading);
        return;
      }

      // No heading yet: park the focus on main so it is never lost, and
      // hand it over once the page renders its heading.
      focusElement(main);

      const observer = new MutationObserver(() => {
        const lateHeading = main.querySelector<HTMLElement>('h1');
        if (!lateHeading) {
          return;
        }
        observer.disconnect();
        window.clearTimeout(lateHeadingTimeoutId);
        // Take the heading only while the focus still sits where this
        // effect put it — a person who moved on keeps their place.
        const current = document.activeElement;
        if (current === main || current === document.body) {
          focusElement(lateHeading);
        }
      });
      lateHeadingObserver = observer;
      lateHeadingTimeoutId = window.setTimeout(() => {
        observer.disconnect();
      }, LATE_HEADING_TIMEOUT_MS);
      observer.observe(main, { childList: true, subtree: true });
    });

    return () => {
      cancelled = true;
      lateHeadingObserver?.disconnect();
      window.clearTimeout(lateHeadingTimeoutId);
    };
  }, [pathname]);

  return null;
}
