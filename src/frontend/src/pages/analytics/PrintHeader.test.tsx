import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import PrintHeader from './PrintHeader';
import { AuthContext } from '../../auth/AuthContext';
import { OrgContext } from '../../contexts/OrgContext';
import '../../i18n';

const authUser = {
  id: 'u1',
  email: 'coord@example.com',
  display_name: 'Spielleitung',
  role: 'coordinator' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const renderHeader = (orgName: string | null) =>
  render(
    <OrgContext.Provider value={{ name: orgName, languages: ['de', 'en'], refresh: vi.fn() }}>
      <AuthContext.Provider
        value={{
          user: authUser,
          isAuthenticated: true,
          isLoading: false,
          login: vi.fn(),
          logout: vi.fn(),
          checkAuth: vi.fn(),
          needsTwoFactorSetup: () => false,
        }}
      >
        <PrintHeader title="Statistik: Rathausrallye" />
      </AuthContext.Provider>
    </OrgContext.Provider>,
  );

// The print report header renders its title as a paragraph in the same look
// (variant h4), not as a second h1 of height 0 on screen. So every page keeps
// exactly one h1 on screen and no tool trips over a hidden heading. The
// wrapper stays print-only, so the title still appears in print.
describe('PrintHeader — one h1 per page', () => {
  it('renders no h1: the print title is a paragraph in the same look', () => {
    const { container } = renderHeader('Stadt Augsburg');

    expect(container.querySelector('h1')).toBeNull();
    const title = screen.getByText('Statistik: Rathausrallye');
    expect(title.tagName).toBe('P');
    expect(title).toHaveClass('MuiTypography-h4');
  });

  it('stays print-only, so the heading block appears in print only', () => {
    const { container } = renderHeader('Stadt Augsburg');

    const wrapper = container.querySelector('header');
    expect(wrapper).toHaveClass('print-only');
    expect(screen.getByText('Stadt Augsburg')).toBeInTheDocument();
    expect(screen.getByText(/Erstellt am/)).toBeInTheDocument();
  });
});
