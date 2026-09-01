import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'vitest-axe';
import { ThemeProvider } from '@mui/material/styles';
import OrganizationDeleteDialog from './OrganizationDeleteDialog';
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
  name: 'Retired Org',
  slug: 'retired-org',
  content_languages: ['de'],
  created_at: '2026-02-01T00:00:00Z',
  is_archived: true,
  user_count: 4,
  rallye_count: 1,
  run_count: 0,
  org_admin_count: 1,
  pending_admin_invitation_count: 0,
  active_run_count: 0,
  last_activity_at: null,
};

const deletionFixture = {
  slug: 'retired-org',
  deletion_due_at: '2026-03-03T00:00:00Z',
  message: 'Organization deletion scheduled',
};

const renderDialog = (org: InstanceOrganizationListItem | null = orgFixture, open = true) =>
  render(
    <ThemeProvider theme={theme}>
      <OrganizationDeleteDialog open={open} org={org} onSuccess={onSuccess} onClose={onClose} />
    </ThemeProvider>,
  );

describe('OrganizationDeleteDialog', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('shows the 30-day removal warning and that restoring cancels it', () => {
    renderDialog();
    expect(
      screen.getByText(/nach 30 tagen werden alle rallyes, läufe, konten und medien/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/wiederherstellung bricht die löschung/i)).toBeInTheDocument();
  });

  it('keeps the submit button disabled until the exact slug is typed', async () => {
    renderDialog();
    const submitButton = screen.getByRole('button', { name: 'Löschung planen' });
    expect(submitButton).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/kurzname zur bestätigung/i), 'retired-or');
    expect(submitButton).toBeDisabled();
    expect(vi.mocked(apiClient.post)).not.toHaveBeenCalled();
  });

  it('accepts the exact slug and sends it to schedule the deletion', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: deletionFixture });
    renderDialog();

    await userEvent.type(screen.getByLabelText(/kurzname zur bestätigung/i), 'retired-org');
    const submitButton = screen.getByRole('button', { name: 'Löschung planen' });
    expect(submitButton).toBeEnabled();
    await userEvent.click(submitButton);

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith('/api/instance/organizations/retired-org/deletion', {
        confirm_slug: 'retired-org',
      }),
    );
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(deletionFixture));
  });

  it('shows the confirmation-mismatch error inline on the slug field', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      new ApiError(422, 'Confirmation slug does not match', 'CONFIRMATION_MISMATCH'),
    );
    renderDialog();

    await userEvent.type(screen.getByLabelText(/kurzname zur bestätigung/i), 'retired-org');
    await userEvent.click(screen.getByRole('button', { name: 'Löschung planen' }));

    await waitFor(() =>
      expect(screen.getByLabelText(/kurzname zur bestätigung/i)).toHaveAccessibleDescription(
        /stimmt nicht mit dem der organisation überein/i,
      ),
    );
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it('shows the not-archived error inline', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      new ApiError(409, 'Organization is not archived', 'ORG_NOT_ARCHIVED'),
    );
    renderDialog();

    await userEvent.type(screen.getByLabelText(/kurzname zur bestätigung/i), 'retired-org');
    await userEvent.click(screen.getByRole('button', { name: 'Löschung planen' }));

    await waitFor(() =>
      expect(screen.getByText(/nur archivierte organisationen können gelöscht werden/i)).toBeInTheDocument(),
    );
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it('shows an unexpected API error inline', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(500, 'Server error'));
    renderDialog();

    await userEvent.type(screen.getByLabelText(/kurzname zur bestätigung/i), 'retired-org');
    await userEvent.click(screen.getByRole('button', { name: 'Löschung planen' }));

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
