import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, useLocation, useSearchParams } from 'react-router';
import { axe } from 'vitest-axe';
import ChallengeCreate from './ChallengeCreate';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';

// The TipTap editor needs layout APIs jsdom lacks (getClientRects, elementFromPoint).
// It has its own tests, so this page test swaps it for a plain textarea with the same contract.
vi.mock('../../components/RichTextEditor', () => ({
  default: ({ value, onChange, ariaLabel, id }: { value: string; onChange: (v: string) => void; ariaLabel?: string; id?: string }) => (
    <textarea id={id} aria-label={ariaLabel} value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

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

// Renders the ?open= a rallye-station challenge create carries back, so a
// test can assert on it without needing RallyeDetail's own dependencies.
const RallyeRoutePlaceholder = () => {
  const [params] = useSearchParams();
  const state = useLocation().state as { challengeCreated?: boolean } | null;
  return (
    <div>
      <div>Rallye detail open={params.get('open')}</div>
      <div>challengeCreated={String(state?.challengeCreated === true)}</div>
    </div>
  );
};

// Renders the navigation state a library create passes on.
const ChallengeRoutePlaceholder = () => {
  const state = useLocation().state as { created?: boolean } | null;
  return (
    <div>
      <div>Challenge detail</div>
      <div>created={String(state?.created === true)}</div>
    </div>
  );
};

const renderPage = (initialEntry = '/challenges/new') =>
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
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
          <Route path="/challenges/new" element={<ChallengeCreate />} />
          <Route path="/challenges/:challengeId" element={<ChallengeRoutePlaceholder />} />
          <Route path="/rallyes/:rallyeId" element={<RallyeRoutePlaceholder />} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('ChallengeCreate (library)', () => {
  beforeEach(() => vi.resetAllMocks());

  // Every type a creator can choose, audio included, so an audio challenge is
  // reachable. "Kreativ" is not a type: a media task takes a text instead.
  it('renders the six challenge type toggles and no "Kreativ"', () => {
    renderPage();
    for (const label of ['Foto', 'Video', 'Audio', 'Text', 'QR-Code', 'Quiz']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
    expect(screen.queryByRole('button', { name: 'Kreativ' })).not.toBeInTheDocument();
  });

  it('hides ScoringRulesEditor until a challenge type is selected', async () => {
    renderPage();
    expect(screen.queryByLabelText(/Maximale Punkte/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Foto' }));
    expect(screen.getByLabelText(/Maximale Punkte/)).toBeInTheDocument();
  });

  // The button does not wait on the DE title/instructions: it enables once a
  // type is picked, and a click on the still-incomplete form reveals the
  // missing fields instead of staying inert.
  it('enables submit once a type is selected; a click on the incomplete form flags the missing fields', async () => {
    renderPage();
    const submit = screen.getByRole('button', { name: /aufgabe erstellen/i });
    expect(submit).toBeDisabled();

    await userEvent.click(screen.getByRole('button', { name: 'Text' }));
    expect(submit).toBeEnabled();

    const title = screen.getByLabelText(/Titel \(Deutsch\)/);
    expect(title).toHaveAttribute('aria-invalid', 'false');

    await userEvent.click(submit);

    expect(title).toHaveAttribute('aria-invalid', 'true');
    expect(title).toHaveFocus();
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  // `<p></p>` has visible markup but no text. MultilingualField's own required
  // check uses isRichTextEmpty, and the page guard has to agree, or a click
  // saves an instructions field that looks filled but isn't.
  it('treats markup-only instructions (<p></p>) as missing on a create click', async () => {
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: 'Text' }));
    await userEvent.type(screen.getByLabelText(/Titel \(Deutsch\)/), 'Frage');
    const instructions = screen.getByRole('textbox', { name: 'Anleitung (Deutsch)' });
    await userEvent.type(instructions, '<p></p>');
    expect(screen.queryByText('Pflichtfeld: bitte auf Deutsch ausfüllen.')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /aufgabe erstellen/i }));

    expect(screen.getByText('Pflichtfeld: bitte auf Deutsch ausfüllen.')).toBeInTheDocument();
    expect(instructions).toHaveFocus();
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it('creates a library challenge (no station) and navigates to its detail', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        id: 'ch-new',
        organization_id: 'org-1',
        challenge_type: 'text',
        title: { de: 'Frage' },
        instructions: { de: 'Antworte' },
        scoring_rules: { max_points: 10 },
        time_estimate_minutes: null,
        created_at: '',
        updated_at: '',
      },
    });

    renderPage();
    await userEvent.click(screen.getByRole('button', { name: 'Text' }));
    await userEvent.type(screen.getByLabelText(/Titel \(Deutsch\)/), 'Frage');
    await userEvent.type(screen.getByRole('textbox', { name: 'Anleitung (Deutsch)' }), 'Antworte');
    await userEvent.type(screen.getByLabelText(/Lösung \/ Referenzantwort/), 'Die Antwort ist 42.');
    await userEvent.click(screen.getByRole('button', { name: /aufgabe erstellen/i }));

    await waitFor(() => {
      expect(apiClient.post).toHaveBeenCalledWith(
        '/api/orgs/test-org/challenges/',
        expect.objectContaining({
          challenge_type: 'text',
          scoring_rules: expect.objectContaining({ max_points: 10 }),
          suggested_solution: 'Die Antwort ist 42.',
        }),
      );
    });
    await waitFor(() => {
      expect(screen.getByText('Challenge detail')).toBeInTheDocument();
    });
    // The page that follows is told what happened.
    expect(screen.getByText('created=true')).toBeInTheDocument();
  });

  // rallyeStationId is the rallye-station link_id: the round trip carries it
  // back as ?open= so RallyeDetail keeps the station expanded. Opened from a
  // rallye, the back link leads to the rallye and says so.
  it('names the way back to the rallye when opened from one', () => {
    renderPage('/challenges/new?rallyeId=r1&rallyeStationId=rs1');
    expect(screen.getByRole('link', { name: /Zurück zur Rallye/ })).toHaveAttribute('href', '/rallyes/r1');
    expect(screen.queryByRole('link', { name: /Zurück zur Übersicht/ })).not.toBeInTheDocument();
  });

  it('creates+matches a challenge to a rallye-station when ?rallyeId & ?rallyeStationId are present', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        link_id: 'link-1',
        rallye_station_id: 'rs1',
        sort_order: 0,
        challenge: {
          id: 'ch-new',
          organization_id: 'org-1',
          challenge_type: 'text',
          title: { de: 'Frage' },
          instructions: { de: 'Antworte' },
          scoring_rules: { max_points: 10 },
          time_estimate_minutes: null,
          created_at: '',
          updated_at: '',
        },
      },
    });

    renderPage('/challenges/new?rallyeId=r1&rallyeStationId=rs1');
    await userEvent.click(screen.getByRole('button', { name: 'Text' }));
    await userEvent.type(screen.getByLabelText(/Titel \(Deutsch\)/), 'Frage');
    await userEvent.type(screen.getByRole('textbox', { name: 'Anleitung (Deutsch)' }), 'Antworte');
    await userEvent.click(screen.getByRole('button', { name: /aufgabe erstellen/i }));

    await waitFor(() => {
      expect(apiClient.post).toHaveBeenCalledWith(
        '/api/orgs/test-org/rallyes/r1/stations/rs1/challenges',
        expect.objectContaining({ challenge_type: 'text' }),
      );
    });
    await waitFor(() => {
      expect(screen.getByText('Rallye detail open=rs1')).toBeInTheDocument();
    });
    // The rallye page is told what happened.
    expect(screen.getByText('challengeCreated=true')).toBeInTheDocument();
  });

  it('has no axe accessibility violations on initial render', async () => {
    const { container } = renderPage();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
