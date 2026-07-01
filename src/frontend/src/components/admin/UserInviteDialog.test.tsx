import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import UserInviteDialog from './UserInviteDialog';
import apiClient, { ApiError } from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const renderDialog = (props: Partial<React.ComponentProps<typeof UserInviteDialog>> = {}) => {
  const onSuccess = vi.fn();
  const onClose = vi.fn();
  render(
    <UserInviteDialog
      open
      orgSlug="stadt-augsburg"
      mailEnabled
      onSuccess={onSuccess}
      onClose={onClose}
      {...props}
    />,
  );
  return { onSuccess, onClose };
};

describe('UserInviteDialog', () => {
  beforeEach(() => vi.resetAllMocks());

  it('never offers a password field — the invitee sets their own', () => {
    renderDialog();
    expect(screen.queryByLabelText(/passwort/i)).not.toBeInTheDocument();
  });

  it('offers org_admin as an invitable role', async () => {
    renderDialog();
    await userEvent.click(screen.getByLabelText('Rolle'));
    expect(screen.getByRole('option', { name: 'Verwaltung' })).toBeInTheDocument();
  });

  // The invite dialog shows what the chosen role may do, like "Person anlegen".
  it('shows what the chosen role may do', () => {
    renderDialog();
    expect(screen.getByText('Was diese Rolle darf')).toBeInTheDocument();
  });

  it('rejects a malformed address before calling the API', async () => {
    renderDialog();

    await userEvent.type(screen.getByLabelText(/e-mail-adresse/i), 'keine-adresse');
    await userEvent.click(screen.getByRole('button', { name: /einladung senden/i }));

    expect(vi.mocked(apiClient.post)).not.toHaveBeenCalled();
  });

  it('sends the invitation and reports the created record', async () => {
    const invitation = {
      id: 'inv-1',
      email: 'kollegin@augsburg.example',
      role: 'coordinator',
      display_name: null,
      language: 'de',
      invited_by_name: 'Admin',
      created_at: '2026-07-27T12:00:00Z',
      expires_at: '2026-08-03T12:00:00Z',
      mail_delivered: true,
    };
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: invitation });
    const { onSuccess } = renderDialog();

    await userEvent.type(screen.getByLabelText(/e-mail-adresse/i), 'kollegin@augsburg.example');
    await userEvent.click(screen.getByRole('button', { name: /einladung senden/i }));

    await waitFor(() =>
      expect(vi.mocked(apiClient.post)).toHaveBeenCalledWith(
        '/api/orgs/stadt-augsburg/invitations/',
        expect.objectContaining({ email: 'kollegin@augsburg.example', role: 'coordinator' }),
      ),
    );
    expect(onSuccess).toHaveBeenCalledWith(invitation);
  });

  it('warns up front when the deployment cannot send mail', () => {
    renderDialog({ mailEnabled: false });
    expect(screen.getByText(/kein e-mail-versand eingerichtet/i)).toBeInTheDocument();
  });

  // An uncoded refusal reads in German,
  // never as the API's English detail.
  it('surfaces an uncoded conflict from the API in German', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      new ApiError(409, "Email 'besetzt@augsburg.example' already belongs to a user"),
    );
    renderDialog();

    await userEvent.type(screen.getByLabelText(/e-mail-adresse/i), 'besetzt@augsburg.example');
    await userEvent.click(screen.getByRole('button', { name: /einladung senden/i }));

    expect(await screen.findByText(/Das hat nicht geklappt/i)).toBeInTheDocument();
    expect(screen.queryByText(/already belongs to a user/i)).not.toBeInTheDocument();
  });

  it('names the German toggle for an address of a deleted account', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      new ApiError(
        409,
        "Email 'weg@augsburg.example' belongs to a deleted account. Restore that account instead — enable 'show deleted' in user management.",
        'EMAIL_BELONGS_TO_DELETED_USER',
      ),
    );
    renderDialog();

    await userEvent.type(screen.getByLabelText(/e-mail-adresse/i), 'weg@augsburg.example');
    await userEvent.click(screen.getByRole('button', { name: /einladung senden/i }));

    expect(await screen.findByText(/gelöschten Konto.*„Gelöschte anzeigen“/i)).toBeInTheDocument();
    expect(screen.queryByText(/show deleted/i)).not.toBeInTheDocument();
  });

  // The three answers EMAIL_BELONGS_TO_USER /
  // EMAIL_BELONGS_TO_INACTIVE_USER can carry, and what the dialog does with each.
  it('offers "send password link" for an active account in the same organization (A5)', async () => {
    const userId = '11111111-1111-1111-1111-111111111111';
    vi.mocked(apiClient.post)
      .mockRejectedValueOnce(new ApiError(409, userId, 'EMAIL_BELONGS_TO_USER'))
      .mockResolvedValueOnce({ data: { message: 'sent' } });
    renderDialog();

    await userEvent.type(screen.getByLabelText(/e-mail-adresse/i), 'kollegin@augsburg.example');
    await userEvent.click(screen.getByRole('button', { name: /einladung senden/i }));

    const resetButton = await screen.findByRole('button', { name: /passwort-link senden/i });
    await userEvent.click(resetButton);

    await waitFor(() =>
      expect(vi.mocked(apiClient.post)).toHaveBeenCalledWith(
        `/api/orgs/stadt-augsburg/users/${userId}/send-password-reset`,
      ),
    );
    expect(await screen.findByText(/passwort-link an kollegin@augsburg.example verschickt/i)).toBeInTheDocument();
  });

  it('gives a neutral text for an address that belongs to another organization (A5)', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      new ApiError(409, "Email 'kollegin@augsburg.example' already belongs to a user", 'EMAIL_BELONGS_TO_USER'),
    );
    renderDialog();

    await userEvent.type(screen.getByLabelText(/e-mail-adresse/i), 'kollegin@augsburg.example');
    await userEvent.click(screen.getByRole('button', { name: /einladung senden/i }));

    expect(await screen.findByText(/gehört bereits zu einem konto/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /passwort-link senden/i })).not.toBeInTheDocument();
  });

  it('gives a neutral text for a deactivated account in the same organization', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      new ApiError(
        409,
        "Email 'kollegin@augsburg.example' belongs to a deactivated account in this organization",
        'EMAIL_BELONGS_TO_INACTIVE_USER',
      ),
    );
    renderDialog();

    await userEvent.type(screen.getByLabelText(/e-mail-adresse/i), 'kollegin@augsburg.example');
    await userEvent.click(screen.getByRole('button', { name: /einladung senden/i }));

    expect(await screen.findByText(/gehört zu einem deaktivierten konto/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /passwort-link senden/i })).not.toBeInTheDocument();
  });

  it('drops the remedy for address A once the field is edited to address B', async () => {
    const userId = '11111111-1111-1111-1111-111111111111';
    vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(409, userId, 'EMAIL_BELONGS_TO_USER'));
    renderDialog();

    const emailField = screen.getByLabelText(/e-mail-adresse/i);
    await userEvent.type(emailField, 'besetzt-a@augsburg.example');
    await userEvent.click(screen.getByRole('button', { name: /einladung senden/i }));

    await screen.findByRole('button', { name: /passwort-link senden/i });

    await userEvent.type(emailField, 'b');

    expect(screen.queryByRole('button', { name: /passwort-link senden/i })).not.toBeInTheDocument();
  });

  it('ignores a stale reset answer once the collision has moved to a different address', async () => {
    const userIdA = '11111111-1111-1111-1111-111111111111';
    const userIdB = '22222222-2222-2222-2222-222222222222';
    let resolveResetA: (value: { data: { message: string } }) => void = () => {};
    const resetAPromise = new Promise<{ data: { message: string } }>((resolve) => {
      resolveResetA = resolve;
    });

    vi.mocked(apiClient.post)
      .mockRejectedValueOnce(new ApiError(409, userIdA, 'EMAIL_BELONGS_TO_USER'))
      .mockImplementationOnce(() => resetAPromise)
      .mockRejectedValueOnce(new ApiError(409, userIdB, 'EMAIL_BELONGS_TO_USER'));

    renderDialog();
    const emailField = screen.getByLabelText(/e-mail-adresse/i);

    await userEvent.type(emailField, 'besetzt-a@augsburg.example');
    await userEvent.click(screen.getByRole('button', { name: /einladung senden/i }));
    await userEvent.click(await screen.findByRole('button', { name: /passwort-link senden/i }));

    await userEvent.clear(emailField);
    await userEvent.type(emailField, 'besetzt-b@augsburg.example');
    await userEvent.click(screen.getByRole('button', { name: /einladung senden/i }));
    await screen.findByRole('button', { name: /passwort-link senden/i });

    await waitFor(() => expect(vi.mocked(apiClient.post)).toHaveBeenCalledTimes(3));
    await act(async () => {
      resolveResetA({ data: { message: 'sent' } });
      await resetAPromise;
    });

    expect(screen.getByRole('button', { name: /passwort-link senden/i })).toBeInTheDocument();
    expect(screen.queryByText(/verschickt/i)).not.toBeInTheDocument();
  });
});
