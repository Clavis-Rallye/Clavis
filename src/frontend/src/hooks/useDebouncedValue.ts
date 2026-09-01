import { useEffect, useState } from 'react';

/**
 * Return `value` only after it has stayed unchanged for `delayMs`.
 *
 * Server-mode tables and the import dialogs search with this value, so a burst
 * of typing sends one request after the last change, not one per keystroke.
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}
