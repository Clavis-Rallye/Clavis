import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import ChallengeDetail from './ChallengeDetail';
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

// A fully configured quiz: every key the confirmation has to name is set.
const quizChallenge = {
  id: 'ch-1',
  organization_id: 'org-1',
  challenge_type: 'quiz',
  title: { de: 'Wie hoch ist der Turm?' },
  instructions: { de: '<p>Antworte</p>' },
  scoring_rules: {
    max_points: 10,
    auto_approve: true,
    options: ['70 m', '98 m'],
    correct_option: '98 m',
  },
  time_estimate_minutes: null,
  suggested_solution: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  created_by_user_id: 'u1',
  created_by_display_name: 'Admin',
};

const renderDetail = (state?: unknown) =>
  render(
    <MemoryRouter initialEntries={[{ pathname: '/challenges/ch-1', state }]}>
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
          <Route path="/challenges/:challengeId" element={<ChallengeDetail />} />
          <Route path="/challenges" element={<div>List page</div>} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

const loaded = async () => {
  vi.mocked(apiClient.get).mockResolvedValue({ data: quizChallenge });
  const { container } = renderDetail();
  await waitFor(() => expect(screen.getByLabelText(/Maximale Punkte/)).toBeInTheDocument());
  return container;
};

const typeSelect = () => screen.getByRole('combobox', { name: 'Aufgabentyp' });

const pickType = async (label: string) => {
  await userEvent.click(typeSelect());
  const options = await screen.findAllByRole('option');
  const option = options.find((o) => o.textContent === label);
  expect(option).toBeDefined();
  await userEvent.click(option!);
};

describe('ChallengeDetail (type change)', () => {
  beforeEach(() => vi.resetAllMocks());

  it('offers the type as a select with the six types and the current one preselected', async () => {
    await loaded();
    expect(typeSelect()).toHaveTextContent('Quiz');

    await userEvent.click(typeSelect());
    const options = await screen.findAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual([
      'Foto',
      'Video',
      'Audio',
      'Text',
      'QR-Code',
      'Quiz',
    ]);
  });

  it('names what the new type discards before saving', async () => {
    await loaded();
    await pickType('Text');

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('Aufgabentyp ändern');
    expect(dialog).toHaveTextContent('Der Typ wechselt von Quiz zu Text.');
    expect(dialog).toHaveTextContent('Diese Einstellungen der Bewertung werden dabei verworfen:');
    expect(dialog).toHaveTextContent('Antwortoptionen');
    expect(dialog).toHaveTextContent('Richtige Antwort');
    expect(dialog).toHaveTextContent('Automatisch bewerten');
    expect(dialog).toHaveTextContent('Bereits gestartete Läufe behalten ihre Kopie der Aufgabe.');
  });

  it('saves the new type with the scoring rules pruned to it once confirmed', async () => {
    await loaded();
    vi.mocked(apiClient.put).mockResolvedValueOnce({
      data: { ...quizChallenge, challenge_type: 'text', scoring_rules: { max_points: 10 } },
    });

    await pickType('Text');
    await userEvent.click(screen.getByRole('button', { name: 'Typ ändern' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(typeSelect()).toHaveTextContent('Text');

    await userEvent.click(screen.getByRole('button', { name: 'Speichern' }));

    await waitFor(() => expect(apiClient.put).toHaveBeenCalled());
    const [url, body] = vi.mocked(apiClient.put).mock.calls[0] as [
      string,
      { challenge_type: string; scoring_rules: Record<string, unknown> },
    ];
    expect(url).toBe('/api/orgs/test-org/challenges/ch-1');
    expect(body.challenge_type).toBe('text');
    // The quiz keys are gone, max_points survives: what the confirmation promised.
    expect(body.scoring_rules).toEqual({ max_points: 10 });
    expect(body.scoring_rules.options).toBeUndefined();
  });

  it('keeps the previous type when the change is cancelled', async () => {
    await loaded();
    await pickType('Text');

    await userEvent.click(screen.getByRole('button', { name: 'Abbrechen' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    expect(typeSelect()).toHaveTextContent('Quiz');
    // Nothing changed, so there is nothing to save and the quiz keys stay.
    expect(screen.getByRole('button', { name: 'Speichern' })).toBeDisabled();
    expect(screen.getByLabelText(/Richtige Antwort/)).toBeInTheDocument();
    expect(apiClient.put).not.toHaveBeenCalled();
  });

  // `<p></p>` looks like the field has content, but MultilingualField treats it
  // as empty. The save guard has to use the same isRichTextEmpty check, or a
  // click quietly saves a wiped-out instructions field.
  it('treats markup-only instructions (<p></p>) as missing on a save click', async () => {
    await loaded();
    const instructions = screen.getByRole('textbox', { name: 'Anleitung (Deutsch)' });
    await userEvent.clear(instructions);
    await userEvent.type(instructions, '<p></p>');
    expect(screen.queryByText('Pflichtfeld: bitte auf Deutsch ausfüllen.')).not.toBeInTheDocument();

    const save = screen.getByRole('button', { name: 'Speichern' });
    expect(save).toBeEnabled();
    await userEvent.click(save);

    expect(screen.getByText('Pflichtfeld: bitte auf Deutsch ausfüllen.')).toBeInTheDocument();
    expect(instructions).toHaveFocus();
    expect(apiClient.put).not.toHaveBeenCalled();
  });

  // Archiving a challenge in use names both the affected stations and the way
  // back, and does not fall back to the plain (no-usage) wording.
  it('the archive confirmation for a challenge in use names the stations and the way back', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        ...quizChallenge,
        usage: { station_ids: ['s1'], station_titles: [{ de: 'Rathausplatz' }], rallye_ids: [], rallye_titles: [] },
      },
    });
    renderDetail();
    await waitFor(() => expect(screen.getByLabelText(/Maximale Punkte/)).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Archivieren' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Rathausplatz/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Archivierte anzeigen/)).toBeInTheDocument();
  });

  it('has no axe accessibility violations once loaded', async () => {
    const container = await loaded();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});

// The reference answer sits in the rich-text editor. An emptied
// editor clears the stored answer: the API takes null as "clear".
describe('ChallengeDetail (reference answer)', () => {
  beforeEach(() => vi.resetAllMocks());

  it('edits the reference answer in the editor and sends null once it is emptied', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { ...quizChallenge, suggested_solution: '<p><strong>98 m</strong></p>' },
    });
    renderDetail();
    const editor = await screen.findByRole('textbox', { name: 'Lösung / Referenzantwort (optional)' });
    expect(editor).toHaveValue('<p><strong>98 m</strong></p>');

    vi.mocked(apiClient.put).mockResolvedValueOnce({ data: { ...quizChallenge, suggested_solution: null } });
    await userEvent.clear(editor);
    await userEvent.click(screen.getByRole('button', { name: 'Speichern' }));

    await waitFor(() => expect(apiClient.put).toHaveBeenCalled());
    const [, body] = vi.mocked(apiClient.put).mock.calls[0] as [string, Record<string, unknown>];
    expect(body.suggested_solution).toBeNull();
  });
});

describe('ChallengeDetail (notice after create)', () => {
  beforeEach(() => vi.resetAllMocks());

  it('says that the challenge was created when ChallengeCreate navigated here', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: quizChallenge });
    renderDetail({ created: true });
    await waitFor(() => expect(screen.getByLabelText(/Maximale Punkte/)).toBeInTheDocument());
    expect(screen.getByText('Aufgabe erstellt.')).toBeInTheDocument();
  });

  it('shows no notice on a plain visit', async () => {
    await loaded();
    expect(screen.queryByText('Aufgabe erstellt.')).not.toBeInTheDocument();
  });
});
