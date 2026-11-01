import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { ThemeProvider } from '@mui/material/styles';
import InstanceShell from './InstanceShell';
import theme from '../../theme';

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
