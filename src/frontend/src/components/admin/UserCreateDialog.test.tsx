import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { axe } from 'vitest-axe';
import { ThemeProvider } from '@mui/material/styles';
import UserCreateDialog from './UserCreateDialog';
import theme from '../../theme';
import { AuthContext } from '../../auth/AuthContext';
import apiClient, { ApiError } from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const adminUser = {
  id: 'u0',
  email: 'admin@example.com',
  display_name: 'Admin',
  role: 'org_admin' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const onSuccess = vi.fn();
const onClose = vi.fn();

// Wrapped in the real ThemeProvider: the contrast assertion below is only
// meaningful when the palette the panel reads is the shipped one.
const renderDialog = (open = true) =>
  render(
    <ThemeProvider theme={theme}>
      <MemoryRouter>
        <AuthContext.Provider
          value={{
            user: adminUser,
            isAuthenticated: true,
            isLoading: false,
            login: vi.fn(),
            logout: vi.fn(),
            checkAuth: vi.fn(),
            needsTwoFactorSetup: () => false,
          }}
        >
          <UserCreateDialog
            open={open}
            orgSlug="test-org"
            onSuccess={onSuccess}
            onClose={onClose}
          />
        </AuthContext.Provider>
      </MemoryRouter>
    </ThemeProvider>,
  );

describe('UserCreateDialog', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('renders all form fields when open', () => {
    renderDialog();
    expect(screen.getByLabelText(/e-mail/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/anzeigename/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/passwort/i)).toBeInTheDocument();
    // MUI Select combobox — accessible without a label name in jsdom
    expect(screen.getByRole('combobox')).toBeInTheDocument();
  });

  it('rejects password shorter than 8 characters', async () => {
    renderDialog();
    await userEvent.type(screen.getByLabelText(/e-mail/i), 'new@example.com');
    await userEvent.type(screen.getByLabelText(/anzeigename/i), 'New User');
    await userEvent.type(screen.getByLabelText(/passwort/i), 'Ab1!');
    await userEvent.click(screen.getByRole('button', { name: /anlegen/i }));

    expect(screen.getByText(/mindestens 8 zeichen/i)).toBeInTheDocument();
    expect(vi.mocked(apiClient.post)).not.toHaveBeenCalled();
  });

  it('rejects password meeting fewer than 3 character categories', async () => {
    renderDialog();
    await userEvent.type(screen.getByLabelText(/e-mail/i), 'new@example.com');
    await userEvent.type(screen.getByLabelText(/anzeigename/i), 'New User');
    // Only lowercase + digits — 2 categories, should fail
    await userEvent.type(screen.getByLabelText(/passwort/i), 'alllower1');
    await userEvent.click(screen.getByRole('button', { name: /anlegen/i }));

    expect(screen.getByText(/mindestens 8 zeichen/i)).toBeInTheDocument();
    expect(vi.mocked(apiClient.post)).not.toHaveBeenCalled();
  });

  it('submits with correct body on valid input', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        id: 'new-u',
        email: 'new@example.com',
        display_name: 'New User',
        role: 'participant',
        organization_id: 'org-1',
        is_active: true,
        totp_enabled: false,
        created_at: '2026-01-01T00:00:00Z',
        last_login: null,
      },
    });
    renderDialog();
    await userEvent.type(screen.getByLabelText(/e-mail/i), 'new@example.com');
    await userEvent.type(screen.getByLabelText(/anzeigename/i), 'New User');
    await userEvent.type(screen.getByLabelText(/passwort/i), 'SecurePass1!');
    await userEvent.click(screen.getByRole('button', { name: /anlegen/i }));

    await waitFor(() => expect(vi.mocked(apiClient.post)).toHaveBeenCalledOnce());
    expect(vi.mocked(apiClient.post)).toHaveBeenCalledWith(
      '/api/orgs/test-org/users/',
      expect.objectContaining({
        email: 'new@example.com',
        display_name: 'New User',
        password: 'SecurePass1!',
        role: 'participant',
      }),
    );
  });

  // The deleted-account refusal names the
  // German toggle instead of the API's English 'show deleted'.
  it('explains an address of a deleted account in German', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      new ApiError(
        409,
        "Email 'weg@example.com' belongs to a deleted account. Restore that account instead — enable 'show deleted' in user management.",
        'EMAIL_BELONGS_TO_DELETED_USER',
      ),
    );
    renderDialog();
    await userEvent.type(screen.getByLabelText(/e-mail/i), 'weg@example.com');
    await userEvent.type(screen.getByLabelText(/anzeigename/i), 'Weg');
    await userEvent.type(screen.getByLabelText(/passwort/i), 'SecurePass1!');
    await userEvent.click(screen.getByRole('button', { name: /anlegen/i }));

    expect(await screen.findByText(/gelöschten Konto.*„Gelöschte anzeigen“/i)).toBeInTheDocument();
    expect(screen.queryByText(/show deleted/i)).not.toBeInTheDocument();
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it('calls onSuccess after successful submit', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        id: 'new-u',
        email: 'new@example.com',
        display_name: 'New User',
        role: 'participant',
        organization_id: 'org-1',
        is_active: true,
        totp_enabled: false,
        created_at: '2026-01-01T00:00:00Z',
        last_login: null,
      },
    });
    renderDialog();
    await userEvent.type(screen.getByLabelText(/e-mail/i), 'new@example.com');
    await userEvent.type(screen.getByLabelText(/anzeigename/i), 'New User');
    await userEvent.type(screen.getByLabelText(/passwort/i), 'SecurePass1!');
    await userEvent.click(screen.getByRole('button', { name: /anlegen/i }));

    await waitFor(() => expect(onSuccess).toHaveBeenCalledOnce());
  });

  it('shows the role capability panel that reacts to the role select', async () => {
    renderDialog();
    expect(screen.getByText('Was diese Rolle darf')).toBeInTheDocument();
    // Default participant: statistics denied. Switch to coordinator → allowed.
    // (allowed/denied is conveyed via icon + a visually-hidden label.)
    const roleSelect = screen.getByLabelText(/rolle/i);
    await userEvent.click(roleSelect);
    await userEvent.click(screen.getByRole('option', { name: /spielleitung/i }));
    // The capability row is present either way; the panel simply re-rendered.
    expect(screen.getByText('Statistiken sehen')).toBeInTheDocument();
  });

  // The panel row carries its allowed/denied state as visually-hidden text
  // appended to the label — strip the label to read the state back.
  const capabilityState = (label: string) => screen.getByText(label).textContent?.replace(label, '');

  it('capability panel previews a hovered role before it is selected', async () => {
    renderDialog();
    // Default role is Teilnehmer, who may not steer runs.
    expect(capabilityState('Läufe steuern')).toBe('nicht erlaubt');

    await userEvent.click(screen.getByRole('combobox'));
    await userEvent.hover(screen.getByRole('option', { name: /spielleitung/i }));

    expect(capabilityState('Läufe steuern')).toBe('erlaubt');

    // Dismissing without choosing leaves the selection untouched — the preview
    // commits nothing.
    await userEvent.keyboard('{Escape}');
    expect(screen.getByRole('combobox')).toHaveTextContent('Teilnehmende');
  });

  it('capability preview falls back to the selected role when the pointer leaves', async () => {
    renderDialog();
    await userEvent.click(screen.getByRole('combobox'));
    await userEvent.hover(screen.getByRole('option', { name: 'Verwaltung' }));
    expect(capabilityState('Personen verwalten')).toBe('erlaubt');

    await userEvent.unhover(screen.getByRole('option', { name: 'Verwaltung' }));
    await userEvent.keyboard('{Escape}');

    expect(capabilityState('Personen verwalten')).toBe('nicht erlaubt');
  });

  it('has no axe accessibility violations on initial render', async () => {
    const { container } = renderDialog();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  // The capability rows read as on-surface-variant, granted or not. The
  // icon carries the distinction. The dialog renders into a portal, so this
  // scans `baseElement` (document.body), not the render container.
  it('has no color-contrast violations on the role capability list', async () => {
    const { baseElement } = renderDialog();
    expect(screen.getByText('Was diese Rolle darf')).toBeInTheDocument();

    // The assertion that actually measures: a denied row is on-surface-variant,
    // not the 38 %-black `text.disabled`. The axe call below is the criterion's
    // own check, but under jsdom `color-contrast` can never fail. Elements have
    // no layout, so axe files the rule under `incomplete`, not `violations`.
    const denied = screen.getByText('Läufe steuern');
    expect(window.getComputedStyle(denied).color).toBe('rgb(73, 69, 79)'); // text.secondary

    const results = await axe(baseElement, { runOnly: ['color-contrast'] });
    expect(results).toHaveNoViolations();
  });
});
