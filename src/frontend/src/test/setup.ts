import { expect, afterEach, beforeEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import * as matchers from '@testing-library/jest-dom/matchers';
// verbatimModuleSyntax flags vitest-axe's re-export of `toHaveNoViolations`
// as type-only. A namespace import and an `unknown` cast reach the function.
// The type augmentation is in `./vitest-axe.d.ts`.
import * as axeMatchers from 'vitest-axe/matchers';
import i18n from '../i18n';

// jsdom has no scrollIntoView. The stub keeps components that scroll a ref
// into view on mount from throwing.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

expect.extend(matchers);
const { toHaveNoViolations } = axeMatchers as unknown as {
  toHaveNoViolations: (results: unknown) => { pass: boolean; message: () => string };
};
expect.extend({ toHaveNoViolations });

// The assertions expect German labels. In jsdom the LanguageDetector would
// fall back to English (no localStorage value, no navigator language).
// German is set at load and again before each test, since a test may change it.
void i18n.changeLanguage('de');
beforeEach(async () => {
  if (i18n.language !== 'de') {
    await i18n.changeLanguage('de');
  }
});

afterEach(() => {
  cleanup();
});
