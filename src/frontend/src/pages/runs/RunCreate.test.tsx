import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import { createTheme } from '@mui/material/styles';
import RunCreate from './RunCreate';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';

// The organization's automatic-start switch is read by its own
// hook. Mocked, so the queued API answers below stay in their order.
const autoStart = { value: true as boolean | null };
vi.mock('../../hooks/useAutoStartRuns', () => ({
  AUTO_START_SETTING: 'auto_start_runs',
  useAutoStartRuns: () => autoStart.value,
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
  email: 'coord@example.com',
  display_name: 'Coordinator',
  role: 'coordinator' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const rallyeFixture = (id: string, titleDe: string) => ({
  id,
  organization_id: 'org-1',
  title: { de: titleDe },
  description: { de: '' },
  is_published: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
});

const runFixture = {
  id: 'run-new',
  organization_id: 'org-1',
  rallye_id: 'r1',
  name: 'Testlauf',
  state: 'configured',
  join_code: 'XYZ999',
  scheduled_for: null,
  started_at: null,
  paused_at: null,
  ended_at: null,
  settings: {},
  max_teams: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const renderCreate = () =>
  render(
    <MemoryRouter initialEntries={['/runs/new']}>
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
          <Route path="/runs/new" element={<RunCreate />} />
          <Route path="/runs/:runId" element={<div>Run detail</div>} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('RunCreate', () => {
  beforeEach(() => vi.resetAllMocks());

  // "Zusammenfassung" must not break inside the word at 390 and
  // 320 px. jsdom lays nothing out, so the test reads the rules of the labels:
  // no break inside a word, and a smaller label with the full step width below sm.
  it('the step labels never break inside a word and fit the step below sm', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [rallyeFixture('r1', 'Altstadt')], total: 1, skip: 0, limit: 100 },
    });
    const { container } = renderCreate();
    await screen.findByText('Altstadt');

    const stepper = container.querySelector('.MuiStepper-root')!;
    const cls = Array.from(stepper.classList).find((c) => c.startsWith('css-'))!;
    const css = Array.from(document.querySelectorAll('style'))
      .map((s) => s.textContent ?? '')
      .join('\n')
      .replace(/\s+/g, '');
    const rules = (sel: string) =>
      Array.from(css.matchAll(new RegExp(`\\.${cls}${sel}\\{([^}]*)\\}`, 'g'))).map((m) => m[1]).join(';');
    const label = rules('\\.MuiStepLabel-label');
    expect(label).not.toContain('overflow-wrap:anywhere');
    expect(label).not.toContain('word-break:break-all');
    // Below sm: a smaller label, inside a media query for narrow windows.
    expect(css).toMatch(new RegExp(`@media[^{]*max-width[^{]*\\{[^@]*\\.${cls}\\.MuiStepLabel-label\\{[^}]*font-size`));
    // The size is the theme's overline token, not a literal.
    const size = String(createTheme().typography.overline.fontSize).replace(/\s+/g, '');
    expect(css).toContain(`.${cls}.MuiStepLabel-label{font-size:${size}`);
    expect(css).not.toContain('font-size:0.6875rem');
    expect(screen.getByText('Zusammenfassung')).toBeInTheDocument();
  });

  it('step 1 disables "Weiter" button until rallye is selected', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [rallyeFixture('r1', 'Altstadt-Rallye')],
        total: 1,
        skip: 0,
        limit: 100,
      },
    });
    renderCreate();

    // Wait for rallyes to load
    await waitFor(() => expect(screen.getByText('Altstadt-Rallye')).toBeInTheDocument());

    const nextBtn = screen.getByRole('button', { name: 'Weiter' });
    expect(nextBtn).toBeDisabled();

    // Select a rallye
    await userEvent.click(screen.getByText('Altstadt-Rallye'));
    expect(nextBtn).toBeEnabled();
  });

  it('step 2 disables "Weiter" button until name is filled', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [rallyeFixture('r1', 'Altstadt-Rallye')],
        total: 1,
        skip: 0,
        limit: 100,
      },
    });
    renderCreate();

    await waitFor(() => expect(screen.getByText('Altstadt-Rallye')).toBeInTheDocument());
    await userEvent.click(screen.getByText('Altstadt-Rallye'));
    await userEvent.click(screen.getByRole('button', { name: 'Weiter' }));

    const reviewBtn = screen.getByRole('button', { name: 'Weiter' });
    expect(reviewBtn).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/^Name/), 'Mein Testlauf');
    expect(reviewBtn).toBeEnabled();
  });

  it('step 3 review shows selected rallye and name', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [rallyeFixture('r1', 'Altstadt-Rallye')],
        total: 1,
        skip: 0,
        limit: 100,
      },
    });
    renderCreate();

    await waitFor(() => expect(screen.getByText('Altstadt-Rallye')).toBeInTheDocument());
    await userEvent.click(screen.getByText('Altstadt-Rallye'));
    await userEvent.click(screen.getByRole('button', { name: 'Weiter' }));
    await userEvent.type(screen.getByLabelText(/^Name/), 'Mein Testlauf');
    await userEvent.click(screen.getByRole('button', { name: 'Weiter' }));

    // Summary step: both rallye title and name should be visible
    expect(screen.getByText('Altstadt-Rallye')).toBeInTheDocument();
    expect(screen.getByText('Mein Testlauf')).toBeInTheDocument();
  });

  // A row of the summary is labelled by its content, not by the name of
  // a step ("Rallye auswählen") or by an input prompt.
  it('summary rows carry labels of their own', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [rallyeFixture('r1', 'Altstadt-Rallye')], total: 1, skip: 0, limit: 100 },
    });
    renderCreate();

    await userEvent.click(await screen.findByText('Altstadt-Rallye'));
    await userEvent.click(screen.getByRole('button', { name: 'Weiter' }));
    await userEvent.type(screen.getByLabelText(/^Name/), 'Mein Testlauf');
    await userEvent.type(screen.getByLabelText(/Maximale Teamanzahl/), '12');
    fireEvent.change(screen.getByLabelText(/Geplante Startzeit/), { target: { value: '2026-11-25T10:00' } });
    await userEvent.click(screen.getByRole('button', { name: 'Weiter' }));

    const rows = Array.from(screen.getByTestId('summary-rows').children);
    const labels = rows.map((row) => row.querySelector('strong')?.textContent);
    expect(labels).toEqual(['Rallye:', 'Name:', 'Maximale Teamanzahl:', 'Geplante Startzeit:']);
    expect(rows[0]).toHaveTextContent('Rallye: Altstadt-Rallye');
    // Not the step name, which stays the prompt of step 1.
    expect(rows[0]).not.toHaveTextContent('Rallye auswählen');
  });

  // The start-time help follows the switch.
  it.each([
    [true, 'Optional. Mit Startzeit startet der Lauf dann von selbst. Ohne Startzeit startest du ihn von Hand.'],
    [false, 'Optional. Die Startzeit ist nur ein Plan. Den Lauf startest du von Hand.'],
  ])('with automatic start %s the start-time help says what happens', async (on, help) => {
    autoStart.value = on;
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [rallyeFixture('r1', 'Altstadt-Rallye')], total: 1, skip: 0, limit: 100 },
    });
    renderCreate();

    await waitFor(() => expect(screen.getByText('Altstadt-Rallye')).toBeInTheDocument());
    await userEvent.click(screen.getByText('Altstadt-Rallye'));
    await userEvent.click(screen.getByRole('button', { name: 'Weiter' }));

    expect(screen.getByText(help)).toBeInTheDocument();
    autoStart.value = true;
  });

  it('submit calls POST and navigates to run detail on success', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [rallyeFixture('r1', 'Altstadt-Rallye')],
        total: 1,
        skip: 0,
        limit: 100,
      },
    });
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: runFixture });
    renderCreate();

    await waitFor(() => expect(screen.getByText('Altstadt-Rallye')).toBeInTheDocument());
    await userEvent.click(screen.getByText('Altstadt-Rallye'));
    await userEvent.click(screen.getByRole('button', { name: 'Weiter' }));
    await userEvent.type(screen.getByLabelText(/^Name/), 'Testlauf');
    await userEvent.click(screen.getByRole('button', { name: 'Weiter' }));
    await userEvent.click(screen.getByRole('button', { name: 'Lauf erstellen' }));

    await waitFor(() => {
      expect(apiClient.post).toHaveBeenCalledWith(
        '/api/orgs/test-org/runs/',
        expect.objectContaining({ rallye_id: 'r1', name: 'Testlauf' }),
      );
    });
    await waitFor(() => {
      expect(screen.getByText('Run detail')).toBeInTheDocument();
    });
  });

  it('500 error stays on step 3 and surfaces the error detail', async () => {
    const { ApiError } = await import('../../api/client');
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [rallyeFixture('r1', 'Altstadt-Rallye')],
        total: 1,
        skip: 0,
        limit: 100,
      },
    });
    vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(500, 'Serverfehler'));
    renderCreate();

    await waitFor(() => expect(screen.getByText('Altstadt-Rallye')).toBeInTheDocument());
    await userEvent.click(screen.getByText('Altstadt-Rallye'));
    await userEvent.click(screen.getByRole('button', { name: 'Weiter' }));
    await userEvent.type(screen.getByLabelText(/^Name/), 'Fehlerlauf');
    await userEvent.click(screen.getByRole('button', { name: 'Weiter' }));
    await userEvent.click(screen.getByRole('button', { name: 'Lauf erstellen' }));

    await waitFor(() => {
      expect(screen.getByText('Serverfehler')).toBeInTheDocument();
    });
    // Still on step 3 (review/submit button still present)
    expect(screen.getByRole('button', { name: 'Lauf erstellen' })).toBeInTheDocument();
  });

  // The review step says what "Lauf erstellen" does and when teams can join.
  // It must not promise the sentence "Der Teilnahme-Code wird verfügbar, sobald
  // der Durchlauf gestartet wurde": the run page shows code and QR code before
  // the start, and joining opens with the start.
  it.each([
    { planned: '', auto: true, expected: /Teams können beitreten, sobald du den Lauf startest\./ },
    { planned: '2026-11-25T10:00', auto: true, expected: /Der Lauf startet zur geplanten Zeit von selbst\./ },
    { planned: '2026-11-25T10:00', auto: false, expected: /sobald du den Lauf startest\./ },
  ])('review says when teams can join (planned "$planned", auto start $auto)', async ({ planned, auto, expected }) => {
    autoStart.value = auto;
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [rallyeFixture('r1', 'Altstadt-Rallye')], total: 1, skip: 0, limit: 100 },
    });
    renderCreate();

    await userEvent.click(await screen.findByText('Altstadt-Rallye'));
    await userEvent.click(screen.getByRole('button', { name: 'Weiter' }));
    await userEvent.type(screen.getByLabelText(/^Name/), 'Mein Testlauf');
    if (planned) fireEvent.change(screen.getByLabelText(/Geplante Startzeit/), { target: { value: planned } });
    await userEvent.click(screen.getByRole('button', { name: 'Weiter' }));

    const sentence = screen.getByTestId('after-create');
    expect(sentence).toHaveTextContent(/^Nach dem Erstellen siehst du Beitrittscode und QR-Code/);
    expect(sentence).toHaveTextContent(expected);
    expect(sentence).not.toHaveTextContent(/Teilnahme-Code wird verfügbar/);
    autoStart.value = true;
  });

  it('has no axe accessibility violations on initial render', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [rallyeFixture('r1', 'Altstadt-Rallye')],
        total: 1,
        skip: 0,
        limit: 100,
      },
    });
    const { container } = renderCreate();
    await waitFor(() => expect(screen.getByText('Altstadt-Rallye')).toBeInTheDocument());
    // MUI v9 Stepper renders <ol> with <div> children, tripping the axe `list` rule.
    // Upstream issue, not actionable from our code.
    const results = await axe(container, { rules: { list: { enabled: false } } });
    expect(results).toHaveNoViolations();
  });
});
