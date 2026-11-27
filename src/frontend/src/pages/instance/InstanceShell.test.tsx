import { afterEach, describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { ThemeProvider } from '@mui/material/styles';
import InstanceShell from './InstanceShell';
import theme from '../../theme';
import {
  __resetOperatorNoticeForTests,
  type OperatorNotice as OperatorNoticeData,
} from '../../config/operatorNotice';

const renderShell = (onSignOut?: () => void) =>
  render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={['/instance']}>
        <InstanceShell title="Organisationen" onSignOut={onSignOut}>
          <p>Page content</p>
        </InstanceShell>
      </MemoryRouter>
    </ThemeProvider>,
  );

// The operator notice stands directly above the legal links, outside main
// (spec 095, C9).
const operatorNotice: OperatorNoticeData = {
  version: 1,
  logos: [{ src: 'programme.svg', alt: { de: 'Logo des Programms' }, height: 48 }],
  text: { de: 'Dieses Angebot wird gefoerdert.' },
  links: [{ label: 'example.org/programme', url: 'https://example.org/programme' }],
};

function expectNoticeAboveLegalLinks() {
  const region = screen.getByRole('region', { name: 'Hinweis des Betreibers' });
  const nav = screen.getByRole('navigation', { name: 'Rechtliches' });
  // Same parent, the notice right before the row of links.
  expect(region.nextElementSibling).toBe(nav);
  expect(nav.compareDocumentPosition(region) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  expect(screen.getByRole('main')).not.toContainElement(region);
  return region;
}

// The brand row is a header landmark, not part of the main card, so no text
// stands outside a landmark. The page keeps exactly one main.
describe('InstanceShell', () => {
  it.each([
    ['on the sign-in, without a session', undefined],
    ['on the console pages, with a session', () => {}],
  ])('puts the brand row in the banner and the child in main, %s', (_label, onSignOut) => {
    renderShell(onSignOut);

    const banner = screen.getByRole('banner');
    expect(within(banner).getByText('Clavis')).toBeInTheDocument();
    expect(within(banner).getAllByRole('button').length).toBeGreaterThan(0);

    const main = screen.getByRole('main');
    expect(within(main).getByText('Page content')).toBeInTheDocument();
    expect(within(main).getByRole('heading', { level: 1, name: 'Organisationen' })).toBeInTheDocument();

    expect(screen.getAllByRole('banner')).toHaveLength(1);
    expect(screen.getAllByRole('main')).toHaveLength(1);
    expect(banner.contains(main)).toBe(false);
  });
});

describe('InstanceShell operator notice', () => {
  afterEach(() => {
    __resetOperatorNoticeForTests(null);
  });

  it.each([
    ['on the sign-in, without a session', undefined],
    ['on the console pages, with a session', () => {}],
  ])('shows the notice above the legal links, outside main, %s', (_label, onSignOut) => {
    __resetOperatorNoticeForTests(operatorNotice);
    renderShell(onSignOut);
    expectNoticeAboveLegalLinks();
  });

  it('shows no notice region without a notice', () => {
    renderShell();
    expect(screen.queryByRole('region', { name: 'Hinweis des Betreibers' })).toBeNull();
  });
});
