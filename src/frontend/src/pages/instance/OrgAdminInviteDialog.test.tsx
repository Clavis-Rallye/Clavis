import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'vitest-axe';
import { ThemeProvider } from '@mui/material/styles';
import OrgAdminInviteDialog from './OrgAdminInviteDialog';
import theme from '../../theme';
import apiClient, { ApiError } from '../../api/client';
import type { InstanceOrganizationListItem } from '../../api/types';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  };
});

const onSuccess = vi.fn();
const onClose = vi.fn();

const orgFixture: InstanceOrganizationListItem = {
  name: 'Stadtverwaltung Augsburg',
  slug: 'stadtverwaltung-augsburg',
  content_languages: ['de'],
  created_at: '2026-01-01T00:00:00Z',
  is_archived: false,
  user_count: 12,
  rallye_count: 3,
  run_count: 5,
  org_admin_count: 2,
  pending_admin_invitation_count: 1,
  active_run_count: 1,
  last_activity_at: null,
};

const invitationFixture = {
  id: 'invitation-1',
  email: 'neue.admin@augsburg.example',
  language: 'de',
  invited_by_name: 'Clavis',
  created_at: '2026-03-01T00:00:00Z',
  expires_at: '2026-03-08T00:00:00Z',
  mail_delivered: true,
};

const renderDialog = (org: InstanceOrganizationListItem | null = orgFixture, open = true) =>
  render(
    <ThemeProvider theme={theme}>
      <OrgAdminInviteDialog open={open} org={org} onSuccess={onSuccess} onClose={onClose} />
    </ThemeProvider>,
  );

describe('OrgAdminInviteDialog', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('renders the email field and names the organization in the intro', () => {
    renderDialog();
    expect(screen.getByLabelText(/e-mail-adresse/i)).toBeInTheDocument();
    expect(screen.getByText(/stadtverwaltung augsburg/i)).toBeInTheDocument();
  });

  it('rejects an empty email address', async () => {
    renderDialog();
    await userEvent.click(screen.getByRole('button', { name: /einladung senden/i }));

    expect(screen.getByText('Dieses Feld ist erforderlich.')).toBeInTheDocument();
    expect(vi.mocked(apiClient.post)).not.toHaveBeenCalled();
  });

  it('rejects an invalid email address', async () => {
    renderDialog();
    await userEvent.type(screen.getByLabelText(/e-mail-adresse/i), 'not-an-email');
    await userEvent.click(screen.getByRole('button', { name: /einladung senden/i }));

    expect(screen.getByText(/ungültige e-mail-adresse/i)).toBeInTheDocument();
    expect(vi.mocked(apiClient.post)).not.toHaveBeenCalled();
  });

  it('submits the payload the backend expects and calls onSuccess', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: invitationFixture });
    renderDialog();

    await userEvent.type(screen.getByLabelText(/e-mail-adresse/i), 'neue.admin@augsburg.example');
    await userEvent.click(screen.getByRole('button', { name: /einladung senden/i }));

    await waitFor(() => expect(vi.mocked(apiClient.post)).toHaveBeenCalledOnce());
    expect(vi.mocked(apiClient.post)).toHaveBeenCalledWith(
      '/api/instance/organizations/stadtverwaltung-augsburg/admin-invitations',
      { email: 'neue.admin@augsburg.example' },
    );
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(invitationFixture));
  });

  it('shows the pending-invitation error inline on the email field', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      new ApiError(409, 'Email already has a pending invitation', 'EMAIL_HAS_PENDING_INVITATION'),
    );
    renderDialog();

    await userEvent.type(screen.getByLabelText(/e-mail-adresse/i), 'neue.admin@augsburg.example');
    await userEvent.click(screen.getByRole('button', { name: /einladung senden/i }));

    await waitFor(() =>
      expect(screen.getByLabelText(/e-mail-adresse/i)).toHaveAccessibleDescription(/offene einladung/i),
    );
    expect(onSuccess).not.toHaveBeenCalled();
  });

  // An address that already has an account can never accept. The operator
  // reads that at the field, once.
  it('shows the existing-account refusal once, at the email field', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      new ApiError(409, "Email 'x' already belongs to an account", 'EMAIL_HAS_ACCOUNT'),
    );
    renderDialog();

    await userEvent.type(screen.getByLabelText(/e-mail-adresse/i), 'vergeben@augsburg.example');
    await userEvent.click(screen.getByRole('button', { name: /einladung senden/i }));

    await waitFor(() =>
      expect(screen.getByLabelText(/e-mail-adresse/i)).toHaveAccessibleDescription(
        /gehört bereits zu einem Konto/i,
      ),
    );
    expect(screen.getAllByText(/gehört bereits zu einem Konto/i)).toHaveLength(1);
    expect(onSuccess).not.toHaveBeenCalled();
  });

  // The invitation can take over from current admins, whom the operator picks
  // from the organization's list.
  it('sends the admins the invited person takes over from', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [
          { id: 'a1', email: 'weg@augsburg.example', display_name: 'Ausgeschieden', is_active: true, last_login: null },
          { id: 'a2', email: 'bleibt@augsburg.example', display_name: 'Bleibt', is_active: true, last_login: null },
        ],
      },
    });
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: invitationFixture });
    renderDialog();

    await userEvent.click(await screen.findByLabelText('Ausgeschieden (weg@augsburg.example)'));
    await userEvent.type(screen.getByLabelText(/e-mail-adresse/i), 'neue.admin@augsburg.example');
    await userEvent.click(screen.getByRole('button', { name: /einladung senden/i }));

    await waitFor(() =>
      expect(vi.mocked(apiClient.post)).toHaveBeenCalledWith(
        '/api/instance/organizations/stadtverwaltung-augsburg/admin-invitations',
        { email: 'neue.admin@augsburg.example', replace_admin_ids: ['a1'] },
      ),
    );
    expect(screen.getByText(/werden deaktiviert, sobald die Einladung angenommen ist/)).toBeInTheDocument();
  });

  it('shows an unexpected API error inline', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(500, 'Server error'));
    renderDialog();

    await userEvent.type(screen.getByLabelText(/e-mail-adresse/i), 'neue.admin@augsburg.example');
    await userEvent.click(screen.getByRole('button', { name: /einladung senden/i }));

    await waitFor(() => expect(screen.getByText(/fehlgeschlagen/i)).toBeInTheDocument());
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it('renders nothing when no organization is given', () => {
    const { container } = renderDialog(null);
    expect(container).toBeEmptyDOMElement();
  });

  it('has no axe accessibility violations on initial render', async () => {
    const { container } = renderDialog();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
