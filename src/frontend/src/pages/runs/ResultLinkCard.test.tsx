import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import ResultLinkCard from './ResultLinkCard';
import apiClient from '../../api/client';
import type { ResultLinkStatus } from '../../api/types';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const PUBLISH_URL = '/api/orgs/test-org/runs/run-1/results/publish';

const publishedLink: ResultLinkStatus = {
  status: 'published',
  url: 'https://public.example.com/results#abc123',
  published_at: '2026-09-27T10:00:00Z',
  expires_at: '2026-12-26T10:00:00Z',
};

const mockLinkStatus = (status: ResultLinkStatus) => {
  vi.mocked(apiClient.get).mockImplementation((url: string) => {
    if (url.includes('/results/link')) return Promise.resolve({ data: status });
    return Promise.resolve({ data: {} });
  });
};

const renderCard = (props: { gradingComplete?: boolean; scored?: number; total?: number } = {}) =>
  render(
    <MemoryRouter initialEntries={['/runs/run-1']}>
      <Routes>
        <Route
          path="/runs/:runId"
          element={
            <ResultLinkCard
              orgSlug="test-org"
              runId="run-1"
              scored={props.scored ?? 4}
              total={props.total ?? 5}
              gradingComplete={props.gradingComplete ?? true}
            />
          }
        />
        <Route path="/runs/:runId/results" element={<div>Closing-Stub</div>} />
      </Routes>
    </MemoryRouter>,
  );

describe('ResultLinkCard', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(apiClient.post).mockResolvedValue({ data: {} });
    vi.mocked(apiClient.delete).mockResolvedValue({ data: undefined });
  });

  it('unpublished with open grading: status line, open status, disabled publish action', async () => {
    mockLinkStatus({ status: 'unpublished', url: null, published_at: null, expires_at: null });
    renderCard({ gradingComplete: false });

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Ergebnisse' })).toBeInTheDocument(),
    );
    expect(screen.getByText('4 von 5 bewertet')).toBeInTheDocument();
    // Open grading is the blue chip with the hourglass.
    const openChip = screen.getByText('Bewertung läuft noch').closest('.MuiChip-root');
    expect(openChip).not.toHaveClass('MuiChip-colorWarning');
    expect(openChip?.querySelector('[data-testid="HourglassEmptyIcon"]')).toHaveAttribute(
      'aria-hidden',
      'true',
    );
    expect(
      screen.getByRole('button', { name: 'Ergebnis veröffentlichen' }),
    ).toBeDisabled();
    expect(screen.getByText(/sobald alle Einreichungen bewertet sind/)).toBeInTheDocument();
  });

  it('publish needs the confirm dialog; cancel fires no request', async () => {
    mockLinkStatus({ status: 'unpublished', url: null, published_at: null, expires_at: null });
    vi.mocked(apiClient.post).mockResolvedValue({
      data: {
        url: 'https://public.example.com/results#abc123',
        slug: 'abc123',
        published_at: '2026-09-27T10:00:00Z',
        expires_at: '2026-12-26T10:00:00Z',
      },
    });
    renderCard({ gradingComplete: true });

    const publish = await screen.findByRole('button', { name: 'Ergebnis veröffentlichen' });
    expect(publish).toBeEnabled();
    await userEvent.click(publish);

    // The dialog names what becomes public and how long the link lives
    // before anything is sent.
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Teamnamen/)).toBeInTheDocument();
    expect(within(dialog).getByText(/90 Tagen/)).toBeInTheDocument();
    expect(apiClient.post).not.toHaveBeenCalled();

    await userEvent.click(within(dialog).getByRole('button', { name: 'Abbrechen' }));
    expect(apiClient.post).not.toHaveBeenCalled();
    // Let the dialog finish its exit transition before reopening — an exiting
    // MUI dialog would still match findByRole and swallow the next click.
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    // Confirming publishes and the card switches to the published state.
    await userEvent.click(screen.getByRole('button', { name: 'Ergebnis veröffentlichen' }));
    const dialog2 = await screen.findByRole('dialog');
    await userEvent.click(within(dialog2).getByRole('button', { name: 'Ergebnis veröffentlichen' }));

    await waitFor(() => expect(apiClient.post).toHaveBeenCalledWith(PUBLISH_URL));
    // While a dialog is open MUI hides the rest of the app from accessibility
    // queries — wait for the unmount before role-querying the card.
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(await screen.findByText('https://public.example.com/results#abc123')).toBeInTheDocument();
    expect(screen.getByText(/Gültig bis/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Freigabe zurückziehen' })).toBeInTheDocument();
  });

  // The publish button the focus came from is gone once the card shows the
  // published state, so the dialog cannot hand the focus back. The focus goes
  // to the public link, not to the document body.
  it('after the first publication the focus stands on the public link', async () => {
    mockLinkStatus({ status: 'unpublished', url: null, published_at: null, expires_at: null });
    vi.mocked(apiClient.post).mockResolvedValue({
      data: {
        url: 'https://public.example.com/results#abc123',
        slug: 'abc123',
        published_at: '2026-09-27T10:00:00Z',
        expires_at: '2026-12-26T10:00:00Z',
      },
    });
    renderCard({ gradingComplete: true });

    await userEvent.click(await screen.findByRole('button', { name: 'Ergebnis veröffentlichen' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Ergebnis veröffentlichen' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    const publicLink = screen.getByRole('link', {
      name: 'https://public.example.com/results#abc123',
    });
    expect(publicLink).toHaveFocus();
  });

  it('a second publication hands the focus back to "Erneut veröffentlichen"', async () => {
    mockLinkStatus(publishedLink);
    vi.mocked(apiClient.post).mockResolvedValue({
      data: {
        url: 'https://public.example.com/results#abc123',
        slug: 'abc123',
        published_at: '2026-09-27T10:00:00Z',
        expires_at: '2026-12-26T10:00:00Z',
      },
    });
    renderCard();

    await userEvent.click(await screen.findByRole('button', { name: 'Erneut veröffentlichen' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Erneut veröffentlichen' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Erneut veröffentlichen' })).toHaveFocus();
  });

  it('a failed publication leaves the focus on the publish button', async () => {
    mockLinkStatus({ status: 'unpublished', url: null, published_at: null, expires_at: null });
    vi.mocked(apiClient.post).mockRejectedValue(new Error('boom'));
    renderCard({ gradingComplete: true });

    await userEvent.click(await screen.findByRole('button', { name: 'Ergebnis veröffentlichen' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Ergebnis veröffentlichen' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Ergebnis veröffentlichen' })).toHaveFocus();
  });

  it('published: link with copy button, expiry, republish and revoke', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: {
        url: 'https://public.example.com/results#abc123',
        slug: 'abc123',
        published_at: '2026-09-27T10:00:00Z',
        expires_at: '2026-12-26T10:00:00Z',
      },
    });
    mockLinkStatus(publishedLink);
    renderCard();

    expect(await screen.findByText('https://public.example.com/results#abc123')).toBeInTheDocument();
    expect(screen.getByLabelText('Link kopieren')).toBeInTheDocument();
    expect(screen.getByText(/Gültig bis/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Freigabe zurückziehen' })).toBeInTheDocument();
    // A re-publish replaces the snapshot under the same slug.
    expect(screen.getByRole('button', { name: 'Erneut veröffentlichen' })).toBeInTheDocument();
    // The initial publish action is gone while a link is live.
    expect(
      screen.queryByRole('button', { name: 'Ergebnis veröffentlichen' }),
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Erneut veröffentlichen' }));
    // The second publication says that the link stays and the standings are replaced.
    const dialog = await screen.findByRole('dialog', { name: 'Ergebnis erneut veröffentlichen' });
    expect(within(dialog).getByText(/Der Link bleibt derselbe/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Erneut veröffentlichen' }));
    await waitFor(() => expect(apiClient.post).toHaveBeenCalledWith(PUBLISH_URL));
  });

  it('published but grading reopened: republish is disabled and fires no request', async () => {
    mockLinkStatus(publishedLink);
    renderCard({ gradingComplete: false });

    expect(await screen.findByText('https://public.example.com/results#abc123')).toBeInTheDocument();
    const republish = screen.getByRole('button', { name: 'Erneut veröffentlichen' });
    expect(republish).toBeDisabled();
    expect(screen.getByText(/sobald alle Einreichungen bewertet sind/)).toBeInTheDocument();

    // userEvent refuses to click a `pointer-events: none` disabled button, so
    // dispatch the click directly to prove the disabled attribute itself
    // blocks the handler.
    fireEvent.click(republish);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it('revoke needs the confirm dialog and returns the card to the unpublished state', async () => {
    mockLinkStatus(publishedLink);
    renderCard();

    await screen.findByText('https://public.example.com/results#abc123');
    await userEvent.click(screen.getByRole('button', { name: 'Freigabe zurückziehen' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/nicht mehr abrufbar/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Abbrechen' }));
    expect(apiClient.delete).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Freigabe zurückziehen' }));
    const dialog2 = await screen.findByRole('dialog');
    await userEvent.click(within(dialog2).getByRole('button', { name: 'Freigabe zurückziehen' }));

    await waitFor(() => expect(apiClient.delete).toHaveBeenCalledWith(PUBLISH_URL));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(await screen.findByText(/Freigabe wurde zurückgezogen/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ergebnis veröffentlichen' })).toBeEnabled();
  });

  it('revoked: notice plus a fresh publish action', async () => {
    mockLinkStatus({ status: 'revoked', url: null, published_at: null, expires_at: null });
    renderCard({ gradingComplete: true });

    expect(await screen.findByText(/Freigabe wurde zurückgezogen/)).toBeInTheDocument();
    expect(screen.getByText('Bewertung abgeschlossen')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ergebnis veröffentlichen' })).toBeEnabled();
  });

  it('links to the closing view', async () => {
    mockLinkStatus({ status: 'unpublished', url: null, published_at: null, expires_at: null });
    renderCard();

    await userEvent.click(await screen.findByRole('button', { name: 'Abschlussansicht öffnen' }));
    expect(screen.getByText('Closing-Stub')).toBeInTheDocument();
  });

  it('has no axe accessibility violations on the published state', async () => {
    mockLinkStatus(publishedLink);
    const { container } = renderCard();

    await screen.findByText('https://public.example.com/results#abc123');
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
