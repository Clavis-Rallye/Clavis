import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'vitest-axe';
import { ThemeProvider } from '@mui/material/styles';
import OrganizationEditDialog from './OrganizationEditDialog';
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

const updatedFixture = {
  id: 'org-1',
  name: 'Stadtverwaltung Augsburg neu',
  slug: 'stadtverwaltung-augsburg',
  content_languages: ['de', 'en'],
  created_at: '2026-01-01T00:00:00Z',
  is_archived: false,
};

// English-first organization — used to prove the dialog keeps the saved
// order instead of reshuffling into the fixed de/en checkbox order.
const orgFixtureEnDe: InstanceOrganizationListItem = {
  ...orgFixture,
  content_languages: ['en', 'de'],
};

const renderDialog = (org: InstanceOrganizationListItem | null = orgFixture, open = true) =>
  render(
    <ThemeProvider theme={theme}>
      <OrganizationEditDialog open={open} org={org} onSuccess={onSuccess} onClose={onClose} />
    </ThemeProvider>,
  );

describe('OrganizationEditDialog', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('renders name and languages prefilled from the organization, with the slug read-only', () => {
    renderDialog();
    expect(screen.getByLabelText(/name der organisation/i)).toHaveValue('Stadtverwaltung Augsburg');
    expect(screen.getByLabelText('Deutsch')).toBeChecked();
    expect(screen.getByLabelText('Englisch')).not.toBeChecked();
    const slugField = screen.getByLabelText(/^kurzname/i);
    expect(slugField).toHaveValue('stadtverwaltung-augsburg');
    expect(slugField).toBeDisabled();
  });

  // The city carries de, en, hi and th. Every language shows by its own name,
  // not as another "Englisch".
  it('names every carried language by its own name', () => {
    renderDialog({ ...orgFixture, content_languages: ['de', 'en', 'hi', 'th'] });
    expect(screen.getByLabelText('Deutsch')).toBeChecked();
    expect(screen.getByLabelText('Englisch')).toBeChecked();
    expect(screen.getByLabelText('Hindi')).toBeChecked();
    expect(screen.getByLabelText('Thailändisch')).toBeChecked();
    expect(screen.getAllByRole('checkbox')).toHaveLength(4);
  });

  it('rejects a blank name', async () => {
    renderDialog();
    await userEvent.clear(screen.getByLabelText(/name der organisation/i));
    await userEvent.click(screen.getByRole('button', { name: 'Speichern' }));

    expect(screen.getByText('Dieses Feld ist erforderlich.')).toBeInTheDocument();
    expect(vi.mocked(apiClient.patch)).not.toHaveBeenCalled();
  });

  it('requires at least one content language', async () => {
    renderDialog();
    // Deutsch is checked from the fixture — uncheck it, leave nothing selected.
    await userEvent.click(screen.getByLabelText('Deutsch'));
    await userEvent.click(screen.getByRole('button', { name: 'Speichern' }));

    expect(screen.getByText('Dieses Feld ist erforderlich.')).toBeInTheDocument();
    expect(vi.mocked(apiClient.patch)).not.toHaveBeenCalled();
  });

  it('submits the changed name and content languages, and calls onSuccess', async () => {
    vi.mocked(apiClient.patch).mockResolvedValueOnce({ data: updatedFixture });
    renderDialog();

    await userEvent.clear(screen.getByLabelText(/name der organisation/i));
    await userEvent.type(
      screen.getByLabelText(/name der organisation/i),
      'Stadtverwaltung Augsburg neu',
    );
    await userEvent.click(screen.getByLabelText('Englisch'));
    await userEvent.click(screen.getByRole('button', { name: 'Speichern' }));

    await waitFor(() => expect(vi.mocked(apiClient.patch)).toHaveBeenCalledOnce());
    expect(vi.mocked(apiClient.patch)).toHaveBeenCalledWith(
      '/api/instance/organizations/stadtverwaltung-augsburg',
      {
        name: 'Stadtverwaltung Augsburg neu',
        content_languages: ['de', 'en'],
      },
    );
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(updatedFixture));
  });

  // The organization's own language order (here English first) must survive a
  // save untouched, never reshuffled into the fixed de/en checkbox order,
  // since content_languages[0] decides the admin-invitation-mail language
  // server-side.
  it('a rename alone keeps the organization’s language order (["en", "de"])', async () => {
    vi.mocked(apiClient.patch).mockResolvedValueOnce({ data: updatedFixture });
    renderDialog(orgFixtureEnDe);

    await userEvent.clear(screen.getByLabelText(/name der organisation/i));
    await userEvent.type(
      screen.getByLabelText(/name der organisation/i),
      'Stadtverwaltung Augsburg neu',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Speichern' }));

    await waitFor(() => expect(vi.mocked(apiClient.patch)).toHaveBeenCalledOnce());
    expect(vi.mocked(apiClient.patch)).toHaveBeenCalledWith(
      '/api/instance/organizations/stadtverwaltung-augsburg',
      {
        name: 'Stadtverwaltung Augsburg neu',
        content_languages: ['en', 'de'],
      },
    );
  });

  it('unchecking a language removes it from its place (sends ["en"])', async () => {
    vi.mocked(apiClient.patch).mockResolvedValueOnce({ data: updatedFixture });
    renderDialog(orgFixtureEnDe);

    await userEvent.click(screen.getByLabelText('Deutsch'));
    await userEvent.click(screen.getByRole('button', { name: 'Speichern' }));

    await waitFor(() => expect(vi.mocked(apiClient.patch)).toHaveBeenCalledOnce());
    expect(vi.mocked(apiClient.patch)).toHaveBeenCalledWith(
      '/api/instance/organizations/stadtverwaltung-augsburg',
      {
        name: 'Stadtverwaltung Augsburg',
        content_languages: ['en'],
      },
    );
  });

  it('checking a language back on appends it at the end (sends ["en", "de"])', async () => {
    vi.mocked(apiClient.patch).mockResolvedValueOnce({ data: updatedFixture });
    renderDialog(orgFixtureEnDe);

    await userEvent.click(screen.getByLabelText('Deutsch'));
    await userEvent.click(screen.getByLabelText('Deutsch'));
    await userEvent.click(screen.getByRole('button', { name: 'Speichern' }));

    await waitFor(() => expect(vi.mocked(apiClient.patch)).toHaveBeenCalledOnce());
    expect(vi.mocked(apiClient.patch)).toHaveBeenCalledWith(
      '/api/instance/organizations/stadtverwaltung-augsburg',
      {
        name: 'Stadtverwaltung Augsburg',
        content_languages: ['en', 'de'],
      },
    );
  });

  it('shows the blank-name error inline on the name field', async () => {
    vi.mocked(apiClient.patch).mockRejectedValueOnce(
      new ApiError(422, 'Organization name must not be blank', 'ORG_NAME_BLANK'),
    );
    renderDialog();
    await userEvent.click(screen.getByRole('button', { name: 'Speichern' }));

    await waitFor(() =>
      expect(screen.getByLabelText(/name der organisation/i)).toHaveAccessibleDescription(
        /darf nicht leer sein/i,
      ),
    );
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it('shows an unexpected API error inline', async () => {
    vi.mocked(apiClient.patch).mockRejectedValueOnce(new ApiError(500, 'Server error'));
    renderDialog();
    await userEvent.click(screen.getByRole('button', { name: 'Speichern' }));

    await waitFor(() =>
      expect(screen.getByText(/fehlgeschlagen/i)).toBeInTheDocument(),
    );
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
