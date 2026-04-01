import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { axe } from 'vitest-axe';
import UserEditDialog from './UserEditDialog';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';
import type { UserResponse } from '../../api/types';

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

const targetUser: UserResponse = {
  id: 'u1',
  email: 'alice@example.com',
  display_name: 'Alice',
  role: 'participant',
  organization_id: 'org-1',
  is_active: true,
  totp_enabled: false,
  created_at: '2026-01-01T00:00:00Z',
  last_login: null,
};

const onSuccess = vi.fn();
const onClose = vi.fn();

const renderDialog = (user: UserResponse | null = targetUser, open = true) =>
  render(
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
        <UserEditDialog
          open={open}
          user={user}
          orgSlug="test-org"
          onSuccess={onSuccess}
          onClose={onClose}
        />
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('UserEditDialog', () => {
  beforeEach(() => vi.resetAllMocks());

  it('populates display name from passed user', () => {
    renderDialog();
    expect(screen.getByDisplayValue('Alice')).toBeInTheDocument();
  });

  it('role dropdown offers every assignable role, org_admin included', async () => {
    renderDialog();
    // The MUI Select trigger shows the current role value text
    await userEvent.click(screen.getByText('Teilnehmende'));
    expect(screen.getByRole('option', { name: /teilnehmende/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /spielleitung/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /erstellung/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Verwaltung' })).toBeInTheDocument();
  });

  it('shows an org_admin their own role and never demotes them on a name-only save', async () => {
    const admin: UserResponse = { ...targetUser, id: 'u2', role: 'org_admin', display_name: 'Erika' };
    vi.mocked(apiClient.put).mockResolvedValueOnce({ data: admin });
    renderDialog(admin);

    // The dropdown must state the role the user actually holds. Preloading
    // "Teilnehmer" would strip the admin rights on any save.
    expect(screen.getByRole('combobox')).toHaveTextContent('Verwaltung');

    const nameField = screen.getByDisplayValue('Erika');
    await userEvent.clear(nameField);
    await userEvent.type(nameField, 'Erika Beispiel');
    await userEvent.click(screen.getByRole('button', { name: /speichern/i }));

    await waitFor(() => expect(vi.mocked(apiClient.put)).toHaveBeenCalledOnce());
    expect(vi.mocked(apiClient.put)).toHaveBeenCalledWith(
      '/api/orgs/test-org/users/u2',
      expect.objectContaining({ display_name: 'Erika Beispiel', role: 'org_admin' }),
    );
  });

  // Picking a role is a privilege change and must be confirmed before it is even
  // staged. The E2E suite guards the same requirement.
  it('staging a role change opens the confirm and does not apply it yet', async () => {
    renderDialog();
    await userEvent.click(screen.getByText('Teilnehmende'));
    await userEvent.click(screen.getByRole('option', { name: /spielleitung/i }));

    expect(await screen.findByRole('dialog', { name: /rolle ändern/i })).toBeInTheDocument();
    // Still the old role until the confirm is answered.
    expect(screen.getByText('Teilnehmende')).toBeInTheDocument();
  });

  it('cancelling the confirm keeps the previous role', async () => {
    renderDialog();
    await userEvent.click(screen.getByText('Teilnehmende'));
    await userEvent.click(screen.getByRole('option', { name: /spielleitung/i }));
    await userEvent.click(
      within(await screen.findByRole('dialog', { name: /rolle ändern/i })).getByRole('button', {
        name: /abbrechen/i,
      }),
    );

    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: /rolle ändern/i })).not.toBeInTheDocument(),
    );
    expect(screen.getByText('Teilnehmende')).toBeInTheDocument();
  });

  it('confirming the role change lets save send the new role', async () => {
    vi.mocked(apiClient.put).mockResolvedValueOnce({ data: { ...targetUser, role: 'coordinator' } });
    renderDialog();
    await userEvent.click(screen.getByText('Teilnehmende'));
    await userEvent.click(screen.getByRole('option', { name: /spielleitung/i }));
    await userEvent.click(
      within(await screen.findByRole('dialog', { name: /rolle ändern/i })).getByRole('button', {
        name: /bestätigen/i,
      }),
    );

    // The confirm keeps the edit dialog aria-hidden while it closes, so wait for
    // it to go before reaching for Speichern.
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: /rolle ändern/i })).not.toBeInTheDocument(),
    );
    await userEvent.click(await screen.findByRole('button', { name: /speichern/i }));
    await waitFor(() =>
      expect(vi.mocked(apiClient.put)).toHaveBeenCalledWith(
        '/api/orgs/test-org/users/u1',
        expect.objectContaining({ role: 'coordinator' }),
      ),
    );
  });

  it('submit calls updateUser with delta', async () => {
    vi.mocked(apiClient.put).mockResolvedValueOnce({
      data: { ...targetUser, display_name: 'Alice Updated' },
    });
    renderDialog();

    const nameField = screen.getByDisplayValue('Alice');
    await userEvent.clear(nameField);
    await userEvent.type(nameField, 'Alice Updated');
    await userEvent.click(screen.getByRole('button', { name: /speichern/i }));

    await waitFor(() => expect(vi.mocked(apiClient.put)).toHaveBeenCalledOnce());
    expect(vi.mocked(apiClient.put)).toHaveBeenCalledWith(
      '/api/orgs/test-org/users/u1',
      expect.objectContaining({ display_name: 'Alice Updated' }),
    );
  });

  it('calls onSuccess after successful submit', async () => {
    vi.mocked(apiClient.put).mockResolvedValueOnce({ data: targetUser });
    renderDialog();
    await userEvent.click(screen.getByRole('button', { name: /speichern/i }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledOnce());
  });

  it('email field is editable and included in the update', async () => {
    vi.mocked(apiClient.put).mockResolvedValueOnce({ data: targetUser });
    renderDialog();

    const emailField = screen.getByDisplayValue('alice@example.com');
    await userEvent.clear(emailField);
    await userEvent.type(emailField, 'alice2@example.com');
    await userEvent.click(screen.getByRole('button', { name: /speichern/i }));

    await waitFor(() =>
      expect(vi.mocked(apiClient.put)).toHaveBeenCalledWith(
        '/api/orgs/test-org/users/u1',
        expect.objectContaining({ email: 'alice2@example.com' }),
      ),
    );
  });

  it('shows the role capability panel', () => {
    renderDialog();
    expect(screen.getByText('Was diese Rolle darf')).toBeInTheDocument();
    expect(screen.getByText('Statistiken sehen')).toBeInTheDocument();
    expect(screen.getByText('Läufe steuern')).toBeInTheDocument();
    expect(screen.getByText('Personen verwalten')).toBeInTheDocument();
  });

  // The panel row carries its allowed/denied state as visually-hidden text
  // appended to the label — strip the label to read the state back.
  const capabilityState = (label: string) => screen.getByText(label).textContent?.replace(label, '');

  it('capability panel previews a hovered role before it is selected', async () => {
    renderDialog();
    expect(capabilityState('Inhalte verwalten')).toBe('nicht erlaubt');

    await userEvent.click(screen.getByRole('combobox'));
    await userEvent.hover(screen.getByRole('option', { name: /erstellung/i }));

    expect(capabilityState('Inhalte verwalten')).toBe('erlaubt');

    // Dismissing without choosing leaves the selection and the panel untouched —
    // the preview commits nothing.
    await userEvent.keyboard('{Escape}');
    expect(screen.getByRole('combobox')).toHaveTextContent('Teilnehmende');
    expect(capabilityState('Inhalte verwalten')).toBe('nicht erlaubt');
  });

  it('locks role + email and sends neither for a guest user', async () => {
    const guest: UserResponse = {
      ...targetUser,
      id: 'g1',
      role: 'guest',
      email: null,
      display_name: 'Guest',
    };
    vi.mocked(apiClient.put).mockResolvedValueOnce({ data: guest });
    renderDialog(guest);

    expect(screen.getByText(/hochstufen/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /speichern/i }));
    await waitFor(() => expect(vi.mocked(apiClient.put)).toHaveBeenCalledOnce());
    const body = vi.mocked(apiClient.put).mock.calls[0][1] as Record<string, unknown>;
    expect(body).not.toHaveProperty('role');
    expect(body).not.toHaveProperty('email');
    expect(body).toHaveProperty('display_name', 'Guest');
  });

  it('has no axe accessibility violations on initial render', async () => {
    const { container } = renderDialog();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
