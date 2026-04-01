import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router';
import { axe } from 'vitest-axe';
import RallyeCreate from './RallyeCreate';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const user = {
  id: 'u1',
  email: 'admin@example.com',
  display_name: 'Admin',
  role: 'org_admin' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

// Renders the navigation state RallyeCreate passes on, so a test can assert
// on it without RallyeDetail's own dependencies.
const DetailPlaceholder = () => {
  const state = useLocation().state as { created?: boolean } | null;
  return (
    <div>
      <div>Detail page</div>
      <div>created={String(state?.created === true)}</div>
    </div>
  );
};

const renderCreate = () =>
  render(
    <MemoryRouter initialEntries={['/rallyes/new']}>
      <AuthContext.Provider
        value={{
          user,
          isAuthenticated: true,
          isLoading: false,
          login: vi.fn(),
          logout: vi.fn(),
          checkAuth: vi.fn(),
          needsTwoFactorSetup: () => false,
        }}
      >
        <Routes>
          <Route path="/rallyes/new" element={<RallyeCreate />} />
          <Route path="/rallyes" element={<div>List page</div>} />
          <Route path="/rallyes/:id" element={<DetailPlaceholder />} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('RallyeCreate', () => {
  beforeEach(() => vi.clearAllMocks());

  // The submit button is enabled from the start. A click on the
  // still-empty DE title flags it instead of the button staying disabled.
  it('is enabled from the start; a click with an empty DE title flags it', async () => {
    renderCreate();
    const submit = screen.getByRole('button', { name: 'Rallye erstellen' });
    expect(submit).toBeEnabled();

    // MultilingualField sets the aria-label "<label> (<language>)" through
    // `slotProps.htmlInput`, so the textbox is found by the visible label
    // "Titel" in its accessible name.
    const title = screen.getByRole('textbox', { name: /Titel/i });
    expect(title).toHaveAttribute('aria-invalid', 'false');

    await userEvent.click(submit);
    expect(title).toHaveAttribute('aria-invalid', 'true');
    expect(title).toHaveFocus();
    expect(apiClient.post).not.toHaveBeenCalled();

    await userEvent.type(title, 'Neue Rallye');
    expect(submit).toBeEnabled();
  });

  it('submits and navigates to detail page on success', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        id: 'r-new',
        organization_id: 'org-1',
        title: { de: 'Neue Rallye' },
        description: { de: '' },
        is_published: false,
        created_at: '',
        updated_at: '',
      },
    });

    renderCreate();
    await userEvent.type(
      screen.getByRole('textbox', { name: /Titel/i }),
      'Neue Rallye',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Rallye erstellen' }));

    await waitFor(() => {
      expect(apiClient.post).toHaveBeenCalledWith(
        '/api/orgs/test-org/rallyes/',
        expect.objectContaining({ title: { de: 'Neue Rallye', en: '' } }),
      );
    });
    await waitFor(() => {
      expect(screen.getByText('Detail page')).toBeInTheDocument();
    });
  });

  it('shows error and keeps user on page on API failure', async () => {
    const { ApiError } = await import('../../api/client');
    vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(500, 'Server boom'));

    renderCreate();
    await userEvent.type(
      screen.getByRole('textbox', { name: /Titel/i }),
      'Bad Rallye',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Rallye erstellen' }));

    await waitFor(() => {
      expect(screen.getByText('Server boom')).toBeInTheDocument();
    });
    // Still on create page
    expect(screen.getByRole('button', { name: 'Rallye erstellen' })).toBeInTheDocument();
  });

  it('cancel button navigates back to list', async () => {
    renderCreate();
    await userEvent.click(screen.getByRole('button', { name: /abbrechen/i }));
    expect(screen.getByText('List page')).toBeInTheDocument();
  });

  it('has no axe accessibility violations on initial render', async () => {
    const { container } = renderCreate();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
