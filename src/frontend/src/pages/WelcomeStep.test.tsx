import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';
import WelcomeStep from './WelcomeStep';
import * as publicRuns from '../services/publicRuns';
import { AuthProvider } from '../auth/AuthContext';
import { WizardStateProvider } from '../wizard/WizardStateContext';

vi.mock('../services/publicRuns', () => ({
  fetchByCode: vi.fn(),
}));

const mockFetchByCode = vi.mocked(publicRuns.fetchByCode);

function LocationDisplay() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

interface RenderOptions {
  initialEntries?: string[];
  routePath?: string;
}

function renderWelcome({
  initialEntries = ['/'],
  routePath = '/',
}: RenderOptions = {}) {
  return render(
    <I18nextProvider i18n={i18n}>
      <AuthProvider>
        <WizardStateProvider>
          <MemoryRouter initialEntries={initialEntries}>
            <Routes>
              <Route path={routePath} element={<WelcomeStep />} />
              <Route path="/auth-choice" element={<LocationDisplay />} />
              <Route path="/join/:code/profile" element={<LocationDisplay />} />
              <Route path="/join/:code/auth-choice" element={<LocationDisplay />} />
            </Routes>
          </MemoryRouter>
        </WizardStateProvider>
      </AuthProvider>
    </I18nextProvider>,
  );
}

describe('<WelcomeStep>', () => {
  beforeEach(() => {
    mockFetchByCode.mockReset();
  });

  it('renders without a runCode and does not show a rallye name', async () => {
    renderWelcome();
    expect(await screen.findByRole('heading', { level: 1 })).toBeInTheDocument();
    expect(mockFetchByCode).not.toHaveBeenCalled();
  });

  it('shows the rallye name when fetchByCode resolves', async () => {
    mockFetchByCode.mockResolvedValueOnce({
      run_id: 'r1',
      rallye_name: 'Augsburg Explorer',
      organization_slug: 'augsburg',
    });

    renderWelcome({
      initialEntries: ['/join/ABC123'],
      routePath: '/join/:code',
    });

    expect(await screen.findByText(/Augsburg Explorer/)).toBeInTheDocument();
    expect(mockFetchByCode).toHaveBeenCalledWith('ABC123', undefined, expect.anything());
  });

  it('renders silently without a rallye name when the lookup returns null', async () => {
    mockFetchByCode.mockResolvedValueOnce(null);

    renderWelcome({
      initialEntries: ['/join/UNKNOWN'],
      routePath: '/join/:code',
    });

    await waitFor(() => expect(mockFetchByCode).toHaveBeenCalled());
    // Heading still rendered; no error UI.
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument();
  });

  it('navigates to /auth-choice on Continue when no runCode is present', async () => {
    renderWelcome();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /continue|weiter/i })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: /continue|weiter/i }));
    await waitFor(() =>
      expect(screen.getByTestId('loc')).toHaveTextContent('/auth-choice'),
    );
  });

  it('navigates to /join/:code/auth-choice on Continue when a runCode is present', async () => {
    mockFetchByCode.mockResolvedValueOnce(null);

    renderWelcome({
      initialEntries: ['/join/ABC123'],
      routePath: '/join/:code',
    });

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /continue|weiter/i })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: /continue|weiter/i }));
    await waitFor(() =>
      expect(screen.getByTestId('loc')).toHaveTextContent('/join/ABC123/auth-choice'),
    );
  });

  it('preserves the query string through Continue', async () => {
    mockFetchByCode.mockResolvedValueOnce(null);
    renderWelcome({
      initialEntries: ['/join/ABC123?team=7K2X&lang=de'],
      routePath: '/join/:code',
    });

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /continue|weiter/i })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: /continue|weiter/i }));
    await waitFor(() =>
      expect(screen.getByTestId('loc')).toHaveTextContent(
        '/join/ABC123/auth-choice?team=7K2X&lang=de',
      ),
    );
  });

  it('changes the i18next language when the dropdown selection changes', async () => {
    mockFetchByCode.mockResolvedValueOnce(null);
    const changeSpy = vi.spyOn(i18n, 'changeLanguage');
    // Make sure we start from a known language so the dropdown selection
    // actually represents a *change*, not just confirming the default.
    await i18n.changeLanguage('de');
    changeSpy.mockClear();

    renderWelcome({
      initialEntries: ['/join/ABC123'],
      routePath: '/join/:code',
    });

    // Open the language dropdown and pick English. The UI re-renders into the new
    // locale at once, because language does not wait for Continue.
    const trigger = screen.getByRole('combobox', { name: /language|sprache/i });
    fireEvent.mouseDown(trigger);
    const targetOption = await screen.findByRole('option', { name: /English/i });
    fireEvent.click(targetOption);

    // The contract: changeLanguage('en') fires before the user reaches the next
    // route. The test checks the call before it clicks Continue.
    await waitFor(() =>
      expect(changeSpy).toHaveBeenCalledWith('en'),
    );

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /continue|weiter/i })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: /continue|weiter/i }));

    // After Continue the destination route renders. changeLanguage was called
    // before the click.
    expect(
      await screen.findByTestId('loc'),
    ).toHaveTextContent('/join/ABC123/auth-choice');
  });

  it('spans the form column with the primary button', async () => {
    renderWelcome();
    const button = screen.getByRole('button', { name: /continue|weiter/i });
    expect(button).toHaveStyle({ width: '100%', alignSelf: 'stretch' });
  });

  it('labels the progress indicator with step number and total (de)', async () => {
    await i18n.changeLanguage('de');
    renderWelcome();
    expect(
      await screen.findByRole('progressbar', { name: 'Schritt 1 von 4' }),
    ).toBeInTheDocument();
  });

  // The with-code path has its own, longer step count (wizardSteps.ts:
  // WITH_CODE_TOTAL = 7, welcome = step 1). The label must reflect that flow,
  // not the no-code total.
  it('labels the progress indicator with the with-code step total (de)', async () => {
    mockFetchByCode.mockResolvedValueOnce(null);
    await i18n.changeLanguage('de');
    renderWelcome({
      initialEntries: ['/join/ABC123'],
      routePath: '/join/:code',
    });
    expect(
      await screen.findByRole('progressbar', { name: 'Schritt 1 von 7' }),
    ).toBeInTheDocument();
  });

  it('labels the progress indicator with step number and total (en)', async () => {
    await i18n.changeLanguage('en');
    renderWelcome();
    expect(
      await screen.findByRole('progressbar', { name: 'Step 1 of 4' }),
    ).toBeInTheDocument();
    await i18n.changeLanguage('de');
  });

  it('uses SPA navigation, not a full reload', async () => {
    // If Continue triggered window.location.reload, the MemoryRouter could not render
    // the destination route. A successful destination render proves SPA-style navigation.
    mockFetchByCode.mockResolvedValueOnce(null);
    renderWelcome({
      initialEntries: ['/join/ABC123'],
      routePath: '/join/:code',
    });

    await waitFor(() => expect(mockFetchByCode).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /continue|weiter/i })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: /continue|weiter/i }));
    expect(
      await screen.findByTestId('loc'),
    ).toHaveTextContent('/join/ABC123/auth-choice');
  });

  // The run of an archived organization is refused on the first page, in the words
  // the last join step already uses, and offers no way on.
  describe('a run of an archived organization', () => {
    const archived = () =>
      mockFetchByCode.mockImplementation(async (_code, _signal, refusal) => {
        refusal?.onOrgArchived?.();
        return null;
      });
    const open = (entry = '/join/ARCH01') =>
      renderWelcome({ initialEntries: [entry], routePath: '/join/:code' });

    it('says the run is no longer available and offers no way on', async () => {
      archived();
      open();

      expect(
        await screen.findByText('Dieser Lauf ist nicht mehr verfügbar, weil seine Organisation archiviert wurde.'),
      ).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Weiter' })).toBeNull();
    });

    // The answer is not known yet, so Weiter must not lead on.
    it('does not let the person go on while the first lookup is pending', async () => {
      let answer: (run: null) => void = () => {};
      mockFetchByCode.mockImplementation(
        (_code, _signal, refusal) =>
          new Promise((resolve) => {
            answer = () => {
              refusal?.onOrgArchived?.();
              resolve(null);
            };
          }),
      );
      open();

      await waitFor(() => expect(mockFetchByCode).toHaveBeenCalled());
      const continueButton = screen.getByRole('button', { name: 'Weiter' });
      expect(continueButton).toBeDisabled();
      fireEvent.click(continueButton);
      expect(screen.queryByTestId('loc')).toBeNull();

      await act(async () => answer(null));
      expect(
        await screen.findByText(/nicht mehr verfügbar/),
      ).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Weiter' })).toBeNull();
    });

    it('enables Weiter once the lookup answered for an unknown code', async () => {
      let answer: () => void = () => {};
      mockFetchByCode.mockImplementation(
        () => new Promise((resolve) => { answer = () => resolve(null); }),
      );
      open('/join/NOPE99');

      await waitFor(() => expect(mockFetchByCode).toHaveBeenCalled());
      expect(screen.getByRole('button', { name: 'Weiter' })).toBeDisabled();
      await act(async () => answer());
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Weiter' })).toBeEnabled(),
      );
    });

    it('keeps the plain page for an unknown code', async () => {
      mockFetchByCode.mockResolvedValue(null);
      open('/join/NOPE99');

      await waitFor(() => expect(mockFetchByCode).toHaveBeenCalled());
      expect(screen.queryByText(/nicht mehr verfügbar/)).toBeNull();
      expect(screen.getByRole('button', { name: 'Weiter' })).toBeEnabled();
    });
  });

  // A run that cannot be joined is refused on the first step, and a run that
  // starts while the guest waits opens the way.
  describe('a run that cannot be joined yet', () => {
    const run = (state: string, scheduled_for: string | null = null) => ({
      run_id: 'r1',
      rallye_name: 'Augsburg Explorer',
      organization_slug: 'augsburg',
      state,
      scheduled_for,
    });
    const open = () => renderWelcome({ initialEntries: ['/join/ABC123'], routePath: '/join/:code' });

    afterEach(() => vi.useRealTimers());

    // The notice says it. "Weiter" stays usable: this step is the only way to the
    // login for a participant whose session ran out.
    it('says that a run has not started and leaves the way on open', async () => {
      mockFetchByCode.mockResolvedValue(run('configured') as never);
      open();

      expect(await screen.findByText(/Der Lauf hat noch nicht begonnen/)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Weiter' })).toBeEnabled();
      fireEvent.click(screen.getByRole('button', { name: 'Weiter' }));
      expect(await screen.findByTestId('loc')).toHaveTextContent('/join/ABC123/auth-choice');
    });

    it('names the planned start of a scheduled run', async () => {
      mockFetchByCode.mockResolvedValue(run('scheduled', '2026-12-01T09:30:00Z') as never);
      open();

      expect(await screen.findByText(/Der Lauf startet am 01\.12\.2026/)).toBeInTheDocument();
    });

    it('refuses an ended run', async () => {
      mockFetchByCode.mockResolvedValue(run('ended') as never);
      open();

      expect(await screen.findByText(/Dieser Lauf ist beendet/)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Weiter' })).toBeEnabled();
    });

    it('lets an active run through without a notice', async () => {
      mockFetchByCode.mockResolvedValue(run('active') as never);
      open();

      await screen.findByText(/Augsburg Explorer/);
      expect(screen.queryByRole('status')).toBeNull();
      expect(screen.getByRole('button', { name: 'Weiter' })).toBeEnabled();
    });

    it('notices the start while the guest waits', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      mockFetchByCode.mockResolvedValueOnce(run('configured') as never);
      mockFetchByCode.mockResolvedValue(run('active') as never);
      open();
      expect(await screen.findByText(/Der Lauf hat noch nicht begonnen/)).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(36_000);
      });

      expect(await screen.findByText(/Der Lauf hat begonnen/)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Weiter' })).toBeEnabled();
    });
  });
});
