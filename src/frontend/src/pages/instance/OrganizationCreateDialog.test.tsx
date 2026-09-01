import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'vitest-axe';
import { ThemeProvider } from '@mui/material/styles';
import OrganizationCreateDialog from './OrganizationCreateDialog';
import theme from '../../theme';
import apiClient, { ApiError } from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const onSuccess = vi.fn();
const onClose = vi.fn();

const renderDialog = (open = true) =>
  render(
    <ThemeProvider theme={theme}>
      <OrganizationCreateDialog open={open} onSuccess={onSuccess} onClose={onClose} />
    </ThemeProvider>,
  );

// The slug field holds the suggestion made from the name, so
// a test that wants its own slug empties the field first.
const typeSlug = async (value: string) => {
  const field = screen.getByLabelText(/^kurzname/i);
  await userEvent.clear(field);
  await userEvent.type(field, value);
};

const orgFixture = {
  id: 'org-1',
  name: 'Stadtverwaltung Augsburg',
  slug: 'stadtverwaltung-augsburg',
  content_languages: ['de'],
  created_at: '2026-01-01T00:00:00Z',
  is_archived: false,
};

describe('OrganizationCreateDialog', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('renders all form fields when open', () => {
    renderDialog();
    expect(screen.getByLabelText(/name der organisation/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^kurzname/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Deutsch')).toBeInTheDocument();
    expect(screen.getByLabelText('Englisch')).toBeInTheDocument();
    expect(screen.getByLabelText(/e-mail-adresse der ersten verwaltung/i)).toBeInTheDocument();
  });

  it('rejects a slug with uppercase or invalid characters', async () => {
    renderDialog();
    await userEvent.type(screen.getByLabelText(/name der organisation/i), 'Test Org');
    await typeSlug('Not Valid!');
    await userEvent.type(
      screen.getByLabelText(/e-mail-adresse der ersten verwaltung/i),
      'admin@example.com',
    );
    await userEvent.click(screen.getByRole('button', { name: /organisation anlegen/i }));

    expect(
      screen.getByText(/darf nur kleine buchstaben, ziffern und bindestriche/i),
    ).toBeInTheDocument();
    expect(vi.mocked(apiClient.post)).not.toHaveBeenCalled();
  });

  it('rejects an invalid email address', async () => {
    renderDialog();
    await userEvent.type(screen.getByLabelText(/name der organisation/i), 'Test Org');
    await typeSlug('test-org');
    // The form carries `noValidate` so the browser's own type="email" check
    // never intercepts this before our onSubmit handler runs — the operator
    // always sees the app's own inline message, not a browser bubble.
    await userEvent.type(
      screen.getByLabelText(/e-mail-adresse der ersten verwaltung/i),
      'not-an-email',
    );
    await userEvent.click(screen.getByRole('button', { name: /organisation anlegen/i }));

    expect(screen.getByText(/ungültige e-mail-adresse/i)).toBeInTheDocument();
    expect(vi.mocked(apiClient.post)).not.toHaveBeenCalled();
  });

  it('requires at least one content language', async () => {
    renderDialog();
    await userEvent.type(screen.getByLabelText(/name der organisation/i), 'Test Org');
    await typeSlug('test-org');
    // Deutsch is checked by default — uncheck it, leave nothing selected.
    await userEvent.click(screen.getByLabelText('Deutsch'));
    await userEvent.type(
      screen.getByLabelText(/e-mail-adresse der ersten verwaltung/i),
      'admin@example.com',
    );
    await userEvent.click(screen.getByRole('button', { name: /organisation anlegen/i }));

    expect(screen.getByText('Dieses Feld ist erforderlich.')).toBeInTheDocument();
    expect(vi.mocked(apiClient.post)).not.toHaveBeenCalled();
  });

  it('submits the payload the backend expects and calls onSuccess', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: orgFixture });
    renderDialog();
    await userEvent.type(screen.getByLabelText(/name der organisation/i), 'Stadtverwaltung Augsburg');
    await typeSlug('stadtverwaltung-augsburg');
    await userEvent.click(screen.getByLabelText('Englisch'));
    await userEvent.type(
      screen.getByLabelText(/e-mail-adresse der ersten verwaltung/i),
      'admin@example.com',
    );
    await userEvent.click(screen.getByRole('button', { name: /organisation anlegen/i }));

    await waitFor(() => expect(vi.mocked(apiClient.post)).toHaveBeenCalledOnce());
    expect(vi.mocked(apiClient.post)).toHaveBeenCalledWith('/api/instance/organizations', {
      name: 'Stadtverwaltung Augsburg',
      slug: 'stadtverwaltung-augsburg',
      content_languages: ['de', 'en'],
      first_admin_email: 'admin@example.com',
    });
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(orgFixture));
  });

  it('shows the taken-slug error inline on the slug field', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      new ApiError(409, "Organization slug 'test-org' already exists", 'ORG_SLUG_TAKEN'),
    );
    renderDialog();
    await userEvent.type(screen.getByLabelText(/name der organisation/i), 'Test Org');
    await typeSlug('test-org');
    await userEvent.type(
      screen.getByLabelText(/e-mail-adresse der ersten verwaltung/i),
      'admin@example.com',
    );
    await userEvent.click(screen.getByRole('button', { name: /organisation anlegen/i }));

    await waitFor(() =>
      expect(screen.getByLabelText(/^kurzname/i)).toHaveAccessibleDescription(/bereits vergeben/i),
    );
    expect(onSuccess).not.toHaveBeenCalled();
  });

  // The taken-slug error shows once, at the field. A second attempt that stops
  // at the client check must not keep the first attempt's server error on
  // screen.
  it('shows a field error once and drops it on the next attempt', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      new ApiError(409, "Organization slug 'test-org' already exists", 'ORG_SLUG_TAKEN'),
    );
    renderDialog();
    await userEvent.type(screen.getByLabelText(/name der organisation/i), 'Test Org');
    await typeSlug('test-org');
    await userEvent.type(
      screen.getByLabelText(/e-mail-adresse der ersten verwaltung/i),
      'admin@example.com',
    );
    await userEvent.click(screen.getByRole('button', { name: /organisation anlegen/i }));

    await waitFor(() =>
      expect(screen.getByLabelText(/^kurzname/i)).toHaveAccessibleDescription(/bereits vergeben/i),
    );
    expect(screen.getAllByText(/bereits vergeben/i)).toHaveLength(1);

    // Second attempt with an invalid slug: the client check stops it.
    await userEvent.clear(screen.getByLabelText(/^kurzname/i));
    await typeSlug('Not Valid!');
    await userEvent.click(screen.getByRole('button', { name: /organisation anlegen/i }));

    expect(screen.queryByText(/bereits vergeben/i)).not.toBeInTheDocument();
    expect(
      screen.getByText(/darf nur kleine buchstaben, ziffern und bindestriche/i),
    ).toBeInTheDocument();
    expect(vi.mocked(apiClient.post)).toHaveBeenCalledOnce();
  });

  it('shows the pending-invitation error inline on the email field', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      new ApiError(409, "Email already has a pending invitation", 'EMAIL_HAS_PENDING_INVITATION'),
    );
    renderDialog();
    await userEvent.type(screen.getByLabelText(/name der organisation/i), 'Test Org');
    await typeSlug('test-org');
    await userEvent.type(
      screen.getByLabelText(/e-mail-adresse der ersten verwaltung/i),
      'admin@example.com',
    );
    await userEvent.click(screen.getByRole('button', { name: /organisation anlegen/i }));

    await waitFor(() =>
      expect(screen.getByLabelText(/e-mail-adresse der ersten verwaltung/i)).toHaveAccessibleDescription(
        /offene einladung/i,
      ),
    );
    expect(onSuccess).not.toHaveBeenCalled();
  });

  // The slug follows the name until the operator edits it.
  describe('slug suggestion from the name', () => {
    const nameField = () => screen.getByLabelText(/name der organisation/i);
    const slugField = () => screen.getByLabelText(/^kurzname/i);
    const emailField = () => screen.getByLabelText(/e-mail-adresse der ersten verwaltung/i);
    const save = () => userEvent.click(screen.getByRole('button', { name: /organisation anlegen/i }));

    it('fills the slug while the name is typed', async () => {
      renderDialog();
      await userEvent.type(nameField(), 'Stadt München');
      expect(slugField()).toHaveValue('stadt-muenchen');
    });

    it('keeps following every change of the name, also deletions', async () => {
      renderDialog();
      await userEvent.type(nameField(), 'Test Org');
      expect(slugField()).toHaveValue('test-org');
      await userEvent.type(nameField(), '{Backspace}{Backspace}{Backspace}');
      expect(slugField()).toHaveValue('test');
      await userEvent.clear(nameField());
      expect(slugField()).toHaveValue('');
    });

    it('stops following the name once the slug field is edited', async () => {
      renderDialog();
      await userEvent.type(nameField(), 'Test Org');
      await userEvent.type(slugField(), 'x');
      expect(slugField()).toHaveValue('test-orgx');
      await userEvent.type(nameField(), ' Zwei');
      expect(slugField()).toHaveValue('test-orgx');
    });

    it('stays off also when the slug field was emptied by hand', async () => {
      renderDialog();
      await userEvent.type(nameField(), 'Test Org');
      await userEvent.clear(slugField());
      await userEvent.type(nameField(), ' Zwei');
      expect(slugField()).toHaveValue('');
    });

    it('follows the name again when the dialog is opened again', async () => {
      const view = renderDialog();
      await userEvent.type(nameField(), 'Test Org');
      await userEvent.clear(slugField());
      await userEvent.click(screen.getByRole('button', { name: /abbrechen/i }));
      expect(onClose).toHaveBeenCalled();
      view.rerender(
        <ThemeProvider theme={theme}>
          <OrganizationCreateDialog open={false} onSuccess={onSuccess} onClose={onClose} />
        </ThemeProvider>,
      );
      view.rerender(
        <ThemeProvider theme={theme}>
          <OrganizationCreateDialog open onSuccess={onSuccess} onClose={onClose} />
        </ThemeProvider>,
      );
      await userEvent.type(nameField(), 'Neue Stadt');
      expect(slugField()).toHaveValue('neue-stadt');
    });

    it('follows the name again after a successful save', async () => {
      vi.mocked(apiClient.post).mockResolvedValueOnce({ data: orgFixture });
      renderDialog();
      await userEvent.type(nameField(), 'Test Org');
      await typeSlug('eigener-slug');
      await userEvent.type(emailField(), 'admin@example.com');
      await save();
      await waitFor(() => expect(onSuccess).toHaveBeenCalled());
      await userEvent.type(nameField(), 'Zweite Org');
      expect(slugField()).toHaveValue('zweite-org');
    });

    it('gives an empty slug for a name of signs only and shows the missing-slug message', async () => {
      renderDialog();
      await userEvent.type(nameField(), '!!! ???');
      expect(slugField()).toHaveValue('');
      await userEvent.type(emailField(), 'admin@example.com');
      await save();
      expect(slugField()).toHaveAccessibleDescription('Dieses Feld ist erforderlich.');
      expect(vi.mocked(apiClient.post)).not.toHaveBeenCalled();
    });

    it('sends the suggestion as the slug of the request', async () => {
      vi.mocked(apiClient.post).mockResolvedValueOnce({ data: orgFixture });
      renderDialog();
      await userEvent.type(nameField(), 'Stadt München');
      await userEvent.type(emailField(), 'admin@example.com');
      await save();
      await waitFor(() => expect(vi.mocked(apiClient.post)).toHaveBeenCalledOnce());
      expect(vi.mocked(apiClient.post)).toHaveBeenCalledWith(
        '/api/instance/organizations',
        expect.objectContaining({ name: 'Stadt München', slug: 'stadt-muenchen' }),
      );
    });

    it('shows the taken-slug message at the field for a suggested slug', async () => {
      vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(409, 'taken', 'ORG_SLUG_TAKEN'));
      renderDialog();
      await userEvent.type(nameField(), 'Test Org');
      await userEvent.type(emailField(), 'admin@example.com');
      await save();
      await waitFor(() => expect(slugField()).toHaveAccessibleDescription(/bereits vergeben/i));
    });

    it('still rejects a hand-typed slug with invalid characters', async () => {
      renderDialog();
      await userEvent.type(nameField(), 'Test Org');
      await typeSlug('Not Valid!');
      await userEvent.type(emailField(), 'admin@example.com');
      await save();
      expect(screen.getByText(/darf nur kleine buchstaben, ziffern und bindestriche/i)).toBeInTheDocument();
      expect(vi.mocked(apiClient.post)).not.toHaveBeenCalled();
    });
  });

  it('has no axe accessibility violations on initial render', async () => {
    const { container } = renderDialog();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
