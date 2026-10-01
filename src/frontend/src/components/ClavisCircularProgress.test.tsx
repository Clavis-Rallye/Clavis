/**
 * No spinner is unnamed. Without `ariaLabel` it falls back to a generic
 * localized name, and an explicit label wins. The @material/web import is
 * stubbed so jsdom never upgrades the custom element. The asserts read the host
 * attributes, because the inner element passes `aria-label` on to its
 * `role="progressbar"` root.
 */
import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';

vi.mock('@material/web/progress/circular-progress.js', () => ({}));

import ClavisCircularProgress from './ClavisCircularProgress';

function renderSpinner(props: { ariaLabel?: string } = {}) {
  const { container } = render(<ClavisCircularProgress indeterminate {...props} />);
  return container.querySelector('md-circular-progress') as HTMLElement;
}

describe('<ClavisCircularProgress> accessible name', () => {
  it('falls back to the generic localized name when ariaLabel is missing', () => {
    const spinner = renderSpinner();

    expect(spinner).toHaveAttribute('aria-label', 'Lädt');
    expect(spinner).not.toHaveAttribute('aria-hidden', 'true');
  });

  it('prefers an explicit ariaLabel over the fallback', () => {
    const spinner = renderSpinner({ ariaLabel: 'Teamziel wird geladen' });

    expect(spinner).toHaveAttribute('aria-label', 'Teamziel wird geladen');
  });
});
