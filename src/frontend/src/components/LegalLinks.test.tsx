import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import LegalLinks from './LegalLinks';
import '../i18n';

// setup.ts pins the test language to German, so the labels below are German.
function renderLegalLinks() {
  return render(
    <MemoryRouter>
      <LegalLinks />
    </MemoryRouter>,
  );
}

describe('LegalLinks', () => {
  it('renders one quiet nav with the three legal pages', () => {
    renderLegalLinks();
    expect(screen.getByRole('navigation', { name: 'Rechtliches' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Impressum' })).toHaveAttribute(
      'href',
      '/legal/impressum',
    );
    expect(screen.getByRole('link', { name: 'Datenschutz' })).toHaveAttribute(
      'href',
      '/legal/privacy',
    );
    expect(screen.getByRole('link', { name: 'Barrierefreiheit' })).toHaveAttribute(
      'href',
      '/legal/accessibility',
    );
  });

  // The row also carries the bundle's licence notices.
  it('links the third-party licences as a plain link to the file', () => {
    renderLegalLinks();
    const link = screen.getByRole('link', { name: 'Lizenzen' });
    // A plain anchor, not a router link: the build emits a text file, not a route.
    expect(link.tagName).toBe('A');
    expect(link).toHaveAttribute('href', '/third-party-licenses.txt');
    expect(link).not.toHaveAttribute('target');
  });
});
