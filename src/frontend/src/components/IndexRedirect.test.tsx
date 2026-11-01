/**
 * An index redirect never replaces a URL that changed after
 * it rendered. A tap whose navigation is still pending wins.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

const navigateMock = vi.fn();
vi.mock('react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router')>()),
  useNavigate: () => navigateMock,
}));

import IndexRedirect from './IndexRedirect';

describe('<IndexRedirect>', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    window.history.replaceState(null, '', '/runs/r1/participant');
  });

  it('navigates to its target with replace while the URL still equals its location', () => {
    render(
      <MemoryRouter initialEntries={['/runs/r1/participant']}>
        <IndexRedirect to="stations" />
      </MemoryRouter>,
    );

    expect(navigateMock).toHaveBeenCalledTimes(1);
    expect(navigateMock).toHaveBeenCalledWith('stations', { replace: true });
  });

  it('does nothing when the URL already changed to another path', () => {
    // The router still renders for the old location (the pending transition),
    // while the browser already shows the tapped target.
    window.history.replaceState(null, '', '/runs/r1/participant/team/leaderboard');

    render(
      <MemoryRouter initialEntries={['/runs/r1/participant']}>
        <IndexRedirect to="stations" />
      </MemoryRouter>,
    );

    expect(navigateMock).not.toHaveBeenCalled();
  });
});
