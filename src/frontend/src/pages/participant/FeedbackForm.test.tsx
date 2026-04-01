import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import FeedbackForm from './FeedbackForm';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const authUser = {
  id: 'u1',
  email: 'participant@example.com',
  display_name: 'Teilnehmer',
  role: 'participant' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const renderForm = () =>
  render(
    <MemoryRouter initialEntries={['/runs/run-1/participant/feedback']}>
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
        <Routes>
          <Route
            path="/runs/:runId/participant/feedback"
            element={<FeedbackForm />}
          />
          <Route
            path="/runs/:runId/participant/team/leaderboard"
            element={<div>Leaderboard page</div>}
          />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('FeedbackForm', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorage.clear();
  });

  // This device remembers the sent feedback and offers no second form.
  it('shows the thank-you instead of a second form on the same device', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { id: 'fb-1' } });
    const first = renderForm();
    fireEvent.click(screen.getByLabelText('4 Sterne'));
    await userEvent.click(screen.getByRole('button', { name: 'Feedback absenden' }));
    expect(await screen.findByText('Danke für dein Feedback!')).toBeInTheDocument();
    first.unmount();

    renderForm();
    expect(screen.getByText('Danke für dein Feedback!')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Feedback absenden' })).not.toBeInTheDocument();
  });

  // Non-text contrast: the empty star needs 3:1 on white.
  it('draws the empty stars at 3:1 or better on white', () => {
    const { container } = renderForm();
    const empty = container.querySelector('.MuiRating-iconEmpty');
    expect(empty).not.toBeNull();
    const m = getComputedStyle(empty as Element).color.match(/[\d.]+/g)!.map(Number);
    const [r, g, b] = m;
    const alpha = m.length > 3 ? m[3] : 1;
    const onWhite = [r, g, b].map((v) => v * alpha + 255 * (1 - alpha));
    const lin = (v: number) => {
      const c = v / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    };
    const lum = 0.2126 * lin(onWhite[0]) + 0.7152 * lin(onWhite[1]) + 0.0722 * lin(onWhite[2]);
    expect(1.05 / (lum + 0.05)).toBeGreaterThanOrEqual(3);
  });

  it('names the stars in German', () => {
    renderForm();
    expect(screen.getByRole('radio', { name: '1 Stern' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '5 Sterne' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Keine Bewertung' })).toBeInTheDocument();
    expect(screen.queryByRole('radio', { name: /Star/ })).not.toBeInTheDocument();
  });

  it('submit button disabled until rating > 0', async () => {
    renderForm();
    const submitBtn = screen.getByRole('button', { name: 'Feedback absenden' });
    expect(submitBtn).toBeDisabled();

    // Click 3-star rating
    fireEvent.click(screen.getByLabelText('3 Sterne'));
    expect(submitBtn).toBeEnabled();
  });

  it('successful submit shows thank-you and a way through to the standing', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        id: 'fb-1',
        run_id: 'run-1',
        rating: 4,
        comment: null,
        created_at: '2026-01-01T00:00:00Z',
      },
    });

    renderForm();
    fireEvent.click(screen.getByLabelText('4 Sterne'));
    await userEvent.click(screen.getByRole('button', { name: 'Feedback absenden' }));

    await waitFor(() => {
      expect(apiClient.post).toHaveBeenCalledWith(
        '/api/orgs/test-org/runs/run-1/feedback/',
        expect.objectContaining({ rating: 4 }),
      );
    });
    await waitFor(() => {
      expect(screen.getByText('Danke für dein Feedback!')).toBeInTheDocument();
    });
    // Feedback is asked after the run has ended, so the way on is the standing,
    // not the stations — there is nothing left to do at a station.
    expect(screen.getByRole('button', { name: 'Rangliste' })).toBeInTheDocument();
  });

  it('successful submit navigates to the leaderboard when "Rangliste" is clicked', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        id: 'fb-1',
        run_id: 'run-1',
        rating: 5,
        comment: null,
        created_at: '2026-01-01T00:00:00Z',
      },
    });

    renderForm();
    fireEvent.click(screen.getByLabelText('5 Sterne'));
    await userEvent.click(screen.getByRole('button', { name: 'Feedback absenden' }));

    await waitFor(() => screen.getByRole('button', { name: 'Rangliste' }));
    await userEvent.click(screen.getByRole('button', { name: 'Rangliste' }));

    expect(screen.getByText('Leaderboard page')).toBeInTheDocument();
  });

  it('error response keeps the form on screen', async () => {
    const { ApiError } = await import('../../api/client');
    vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(500, 'Serverfehler'));

    renderForm();
    fireEvent.click(screen.getByLabelText('2 Sterne'));
    await userEvent.click(screen.getByRole('button', { name: 'Feedback absenden' }));

    await waitFor(() => {
      expect(screen.getByText('Serverfehler')).toBeInTheDocument();
    });
    // Form is still visible
    expect(screen.getByRole('button', { name: 'Feedback absenden' })).toBeInTheDocument();
    expect(screen.queryByText('Danke für dein Feedback!')).not.toBeInTheDocument();
  });

  // The rate limit answers 429 with a body without "detail", so the client's
  // message would be axios's English text.
  it('names the rate limit in German instead of the raw 429', async () => {
    const { ApiError } = await import('../../api/client');
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      new ApiError(429, 'Request failed with status code 429'),
    );

    renderForm();
    fireEvent.click(screen.getByLabelText('2 Sterne'));
    await userEvent.click(screen.getByRole('button', { name: 'Feedback absenden' }));

    await waitFor(() => {
      expect(
        screen.getByText(
          'Aus diesem Netz kamen gerade sehr viele Rückmeldungen zu diesem Lauf. Bitte versuche es später noch einmal.',
        ),
      ).toBeInTheDocument();
    });
    expect(screen.queryByText(/status code 429/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Feedback absenden' })).toBeInTheDocument();
  });

  // The rating widget cannot take a label. The stars sit in a group that carries
  // the visible label's name, and the label is a plain text element.
  it('groups the stars under the visible "Bewertung" label', () => {
    renderForm();
    const label = screen.getByText('Bewertung');
    expect(label.tagName).not.toBe('LABEL');
    expect(label).not.toHaveAttribute('for');
    const group = screen.getByRole('radiogroup', { name: 'Bewertung' });
    expect(within(group).getByRole('radio', { name: '1 Stern' })).toBeInTheDocument();
    expect(within(group).getByRole('radio', { name: '5 Sterne' })).toBeInTheDocument();
  });

  it('has no axe accessibility violations on initial render', async () => {
    const { container } = renderForm();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
