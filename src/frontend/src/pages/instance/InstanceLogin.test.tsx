import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import { ThemeProvider } from '@mui/material/styles';
import InstanceLogin from './InstanceLogin';
import theme from '../../theme';
import apiClient, { ApiError } from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const renderPage = (initialEntry = '/instance/login') =>
  render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/instance/login" element={<InstanceLogin />} />
          <Route path="/instance" element={<div>Operator console</div>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );

const fillPassword = async (email = 'operator@example.com', password = 'correct horse') => {
  await userEvent.type(screen.getByLabelText(/e-mail-adresse/i), email);
  await userEvent.type(screen.getByLabelText(/^passwort[\s*]*$/i), password);
};

// Every element that carries text of its own stands inside a landmark (the
// brand row in the banner, the rest in main or a nav).
const LANDMARKS = 'header, main, nav, footer, aside, [role="banner"], [role="main"], [role="navigation"]';
const expectAllTextInLandmarks = (container: HTMLElement) => {
  const outside: string[] = [];
  container.querySelectorAll('*').forEach((el) => {
    const own = Array.from(el.childNodes)
      .filter((n) => n.nodeType === Node.TEXT_NODE)
      .map((n) => n.textContent?.trim() ?? '')
      .join('');
    if (own && !el.closest(LANDMARKS)) outside.push(`${el.tagName}: ${own}`);
  });
  expect(outside).toEqual([]);
};

// What speaks twice. A refusal must be announced once: by the focus move
// (the focused element's own text, or the elements its aria-describedby names).
// A live region or role=alert that holds the same text would say it again.
const LIVE = '[role="alert"], [role="status"], [role="log"], [aria-live]:not([aria-live="off"])';
const isLive = (el: Element) => el.matches(LIVE);
const liveHolding = (root: ParentNode, text: string) =>
  Array.from(root.querySelectorAll(LIVE)).filter((el) => el.textContent?.includes(text));
const describedText = (el: HTMLElement) =>
  (el.getAttribute('aria-describedby') ?? '')
    .split(/\s+/)
    .filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? '')
    .join(' ');

// (a) the focused element keeps the announcement, (b) it is no live region and holds none
// with the error text, (c) no live element anywhere in the page holds the error text.
const expectSpokenOnce = async (text: string) => {
  await waitFor(() => expect(document.activeElement).not.toBe(document.body));
  const focused = document.activeElement as HTMLElement;
  expect(`${focused.textContent} ${describedText(focused)}`).toContain(text);
  expect(isLive(focused) && focused.textContent?.includes(text)).toBeFalsy();
  expect(liveHolding(focused, text)).toHaveLength(0);
  for (const id of (focused.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean)) {
    const target = document.getElementById(id);
    if (target) {
      expect(isLive(target) && target.textContent?.includes(text)).toBeFalsy();
      expect(liveHolding(target, text)).toHaveLength(0);
    }
  }
  expect(liveHolding(document.body, text)).toHaveLength(0);
};

const SETUP_REQUIRED = {
  data: { status: 'totp_setup_required', totp_setup_required: true, provisioning_uri: null },
};
const toCodeStep = async () => {
  vi.mocked(apiClient.post).mockResolvedValueOnce(SETUP_REQUIRED);
  await fillPassword();
  await userEvent.click(screen.getByRole('button', { name: /^anmelden$/i }));
  await waitFor(() => expect(screen.getByLabelText(/totp-code/i)).toBeInTheDocument());
  await userEvent.type(screen.getByLabelText(/totp-code/i), '000000');
};

const FIRST_TEXT = 'Anmeldung fehlgeschlagen. Bitte E-Mail-Adresse, Passwort und Code prüfen.';
const RATE_TEXT = 'Zu viele Anmeldeversuche. Bitte warte etwa eine Minute und versuche es erneut.';
const CODE_TEXT = 'Der Code stimmt nicht. Bitte den aktuellen Code aus der Authenticator-App eingeben.';
const DISABLED_TEXT = 'Dieses Betreiberkonto ist deaktiviert. Eine Anmeldung ist nicht möglich.';
const SERVER_TEXT =
  'Die Anmeldung konnte gerade nicht geprüft werden. Bitte versuche es in einem Moment erneut.';
const NO_ANSWER_TEXT =
  'Der Server antwortet nicht, die Anmeldung konnte nicht geprüft werden. Bitte prüfe die Verbindung und versuche es erneut.';
const SETUP_TEXT =
  'Die Einrichtung lässt sich in diesem Schritt nicht abschließen. Bitte lade die Seite neu und melde dich erneut an.';

describe('InstanceLogin', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  // The minimal shell (brand, no organization navigation) plus the
  // three fields of the login form.
  it('renders the login form with email, password and TOTP code inside a minimal shell', () => {
    renderPage();
    expect(screen.getByRole('heading', { name: 'Betreiber-Anmeldung' })).toBeInTheDocument();
    expect(screen.getByLabelText(/e-mail-adresse/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^passwort[\s*]*$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/totp-code/i)).toBeInTheDocument();
    // No organization navigation (e.g. no rallye/run/admin links) in the shell.
    expect(screen.queryByRole('navigation', { name: 'Betreiber-Navigation' })).not.toBeInTheDocument();
    // The legal links are the only navigation on the login page.
    expect(screen.getByRole('navigation', { name: 'Rechtliches' })).toBeInTheDocument();
  });

  it('shows the TOTP setup screen with the QR code and provisioning URI on first login', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        status: 'totp_setup_required',
        totp_setup_required: true,
        provisioning_uri: 'otpauth://totp/Clavis:operator@example.com?secret=ABC&issuer=Clavis',
      },
    });
    renderPage();
    await fillPassword();
    await userEvent.click(screen.getByRole('button', { name: /^anmelden$/i }));

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Zwei-Faktor-Schutz einrichten' })).toBeInTheDocument(),
    );
    expect(screen.getByDisplayValue(/otpauth:\/\/totp/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/totp-code/i)).toBeInTheDocument();
  });

  // The set-up address can be copied, and the read-only field offers no "Eingabe löschen".
  it('offers to copy the set-up address', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const uri = 'otpauth://totp/Clavis:operator@example.com?secret=ABC&issuer=Clavis';
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { status: 'totp_setup_required', totp_setup_required: true, provisioning_uri: uri },
    });
    renderPage();
    await fillPassword();
    await userEvent.click(screen.getByRole('button', { name: /^anmelden$/i }));

    await userEvent.click(await screen.findByRole('button', { name: 'Einrichtungsadresse kopieren' }));

    expect(writeText).toHaveBeenCalledWith(uri);
    expect(await screen.findByRole('button', { name: 'Kopiert' })).toBeInTheDocument();
  });

  // A later unconfirmed login: the secret was already handed out once, so this
  // answer carries no provisioning URI. The screen still asks for the code, but
  // shows neither the QR code nor the URI text.
  it('shows the code-only confirmation screen when the provisioning URI was already handed out', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        status: 'totp_setup_required',
        totp_setup_required: true,
        provisioning_uri: null,
      },
    });
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { status: 'success', email: 'operator@example.com' },
    });
    renderPage();
    await fillPassword();
    await userEvent.click(screen.getByRole('button', { name: /^anmelden$/i }));

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'TOTP-Code eingeben' })).toBeInTheDocument(),
    );
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/einrichtungsadresse/i)).not.toBeInTheDocument();

    await userEvent.type(screen.getByLabelText(/totp-code/i), '123456');
    await userEvent.click(screen.getByRole('button', { name: /code bestätigen/i }));

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith('/api/instance/auth/totp/confirm', {
        email: 'operator@example.com',
        password: 'correct horse',
        totp_code: '123456',
      }),
    );
    await waitFor(() => expect(screen.getByText('Operator console')).toBeInTheDocument());
  });

  it('confirms the TOTP code and navigates to the operator console on success', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        status: 'totp_setup_required',
        totp_setup_required: true,
        provisioning_uri: 'otpauth://totp/Clavis:operator@example.com?secret=ABC&issuer=Clavis',
      },
    });
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { status: 'success', email: 'operator@example.com' },
    });
    renderPage();
    await fillPassword();
    await userEvent.click(screen.getByRole('button', { name: /^anmelden$/i }));
    await waitFor(() => expect(screen.getByLabelText(/totp-code/i)).toBeInTheDocument());

    await userEvent.type(screen.getByLabelText(/totp-code/i), '123456');
    await userEvent.click(screen.getByRole('button', { name: /code bestätigen/i }));

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith('/api/instance/auth/totp/confirm', {
        email: 'operator@example.com',
        password: 'correct horse',
        totp_code: '123456',
      }),
    );
    await waitFor(() => expect(screen.getByText('Operator console')).toBeInTheDocument());
  });

  // At the set-up confirmation, address and password are already
  // checked. A wrong code names the code, not all three.
  it('names the code alone when the set-up confirmation fails', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        status: 'totp_setup_required',
        totp_setup_required: true,
        provisioning_uri: 'otpauth://totp/Clavis:operator@example.com?secret=ABC&issuer=Clavis',
      },
    });
    vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(401, 'Invalid credentials'));
    renderPage();
    await fillPassword();
    await userEvent.click(screen.getByRole('button', { name: /^anmelden$/i }));
    await waitFor(() => expect(screen.getByLabelText(/totp-code/i)).toBeInTheDocument());

    await userEvent.type(screen.getByLabelText(/totp-code/i), '000000');
    await userEvent.click(screen.getByRole('button', { name: /code bestätigen/i }));

    expect(await screen.findByText(/Der Code stimmt nicht/)).toBeInTheDocument();
    expect(screen.queryByText(/E-Mail-Adresse, Passwort und Code/)).toBeNull();
  });

  it('shows a generic message on wrong credentials, never revealing which factor failed', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(401, 'Invalid credentials'));
    renderPage();
    await fillPassword('operator@example.com', 'wrong');
    await userEvent.click(screen.getByRole('button', { name: /^anmelden$/i }));

    await waitFor(() =>
      expect(
        screen.getByText(/anmeldung fehlgeschlagen\. bitte e-mail-adresse, passwort und code prüfen/i),
      ).toBeInTheDocument(),
    );
  });

  // A refused first step: the focus rests on the error text, the three
  // fields it names carry aria-invalid and point to it, and it is announced once.
  it('moves the focus to the error and marks the three named fields after a refused first step', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(401, 'Invalid credentials'));
    renderPage();
    await fillPassword('operator@example.com', 'wrong');
    await userEvent.type(screen.getByLabelText(/totp-code/i), '123456');
    await userEvent.click(screen.getByRole('button', { name: /^anmelden$/i }));

    const text = await screen.findByText(/Anmeldung fehlgeschlagen/);
    const container = text.closest('[id]') as HTMLElement;
    await waitFor(() => expect(container).toHaveFocus());
    expect(document.body).not.toHaveFocus();
    for (const field of [
      screen.getByLabelText(/e-mail-adresse/i),
      screen.getByLabelText(/^passwort[\s*]*$/i),
      screen.getByLabelText(/totp-code/i),
    ]) {
      expect(field).toHaveAttribute('aria-invalid', 'true');
      expect(field.getAttribute('aria-describedby')).toContain(container.id);
    }
    // The focus move is the one announcement. No live region repeats it.
    await expectSpokenOnce(FIRST_TEXT);
  });

  it('leaves the code field out of the error when no code was entered, and clears a field on change', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(401, 'Invalid credentials'));
    renderPage();
    await fillPassword('operator@example.com', 'wrong');
    await userEvent.click(screen.getByRole('button', { name: /^anmelden$/i }));
    await screen.findByText(/Anmeldung fehlgeschlagen/);

    expect(screen.getByLabelText(/totp-code/i)).not.toHaveAttribute('aria-invalid', 'true');
    const email = screen.getByLabelText(/e-mail-adresse/i);
    expect(email).toHaveAttribute('aria-invalid', 'true');
    await userEvent.type(email, 'x');
    expect(email).not.toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByLabelText(/^passwort[\s*]*$/i)).toHaveAttribute('aria-invalid', 'true');
  });

  // A refused code step: the focus goes to the code field, selected.
  it('puts the focus on the selected code field and marks it alone after a refused code step', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { status: 'totp_setup_required', totp_setup_required: true, provisioning_uri: null },
    });
    vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(401, 'Invalid credentials'));
    renderPage();
    await fillPassword();
    await userEvent.click(screen.getByRole('button', { name: /^anmelden$/i }));
    await waitFor(() => expect(screen.getByLabelText(/totp-code/i)).toBeInTheDocument());
    await userEvent.type(screen.getByLabelText(/totp-code/i), '000000');
    await userEvent.click(screen.getByRole('button', { name: /code bestätigen/i }));

    const text = await screen.findByText(/Der Code stimmt nicht/);
    const container = text.closest('[id]') as HTMLElement;
    const codeField = screen.getByLabelText(/totp-code/i) as HTMLInputElement;
    await waitFor(() => expect(codeField).toHaveFocus());
    expect(codeField.selectionStart).toBe(0);
    expect(codeField.selectionEnd).toBe(6);
    expect(codeField).toHaveAttribute('aria-invalid', 'true');
    expect(codeField.getAttribute('aria-describedby')).toContain(container.id);
    // The focused code field points to the text. No live region repeats it.
    await expectSpokenOnce(CODE_TEXT);

    await userEvent.type(codeField, '1');
    expect(codeField).not.toHaveAttribute('aria-invalid', 'true');
  });

  // Every refusal names its cause, is spoken once, puts the focus on
  // one exact target and marks exactly the fields the mapping lists. The mocked detail text
  // differs from every text of the page and must never show.
  type Target = 'error' | 'code';
  type Row = {
    step: 'first step' | 'code step';
    status: number;
    errorCode?: string;
    text: string;
    focus: Target;
    marked: ('email' | 'password' | 'code')[];
  };
  const FIRST_MARKED: Row['marked'] = ['email', 'password'];
  const rows: Row[] = [
    { step: 'first step', status: 401, text: FIRST_TEXT, focus: 'error', marked: FIRST_MARKED },
    { step: 'first step', status: 429, text: RATE_TEXT, focus: 'error', marked: FIRST_MARKED },
    { step: 'first step', status: 403, errorCode: 'INSTANCE_ADMIN_DISABLED', text: DISABLED_TEXT, focus: 'error', marked: [] },
    { step: 'first step', status: 403, text: FIRST_TEXT, focus: 'error', marked: FIRST_MARKED },
    { step: 'first step', status: 500, text: SERVER_TEXT, focus: 'error', marked: [] },
    { step: 'first step', status: 503, text: SERVER_TEXT, focus: 'error', marked: [] },
    { step: 'first step', status: 0, text: NO_ANSWER_TEXT, focus: 'error', marked: [] },
    { step: 'first step', status: 400, text: FIRST_TEXT, focus: 'error', marked: FIRST_MARKED },
    { step: 'first step', status: 409, text: FIRST_TEXT, focus: 'error', marked: FIRST_MARKED },
    { step: 'first step', status: 404, text: FIRST_TEXT, focus: 'error', marked: FIRST_MARKED },
    { step: 'code step', status: 401, text: CODE_TEXT, focus: 'code', marked: ['code'] },
    { step: 'code step', status: 429, text: RATE_TEXT, focus: 'code', marked: ['code'] },
    { step: 'code step', status: 403, errorCode: 'INSTANCE_ADMIN_DISABLED', text: DISABLED_TEXT, focus: 'error', marked: [] },
    { step: 'code step', status: 403, text: CODE_TEXT, focus: 'code', marked: ['code'] },
    { step: 'code step', status: 500, text: SERVER_TEXT, focus: 'error', marked: [] },
    { step: 'code step', status: 503, text: SERVER_TEXT, focus: 'error', marked: [] },
    { step: 'code step', status: 0, text: NO_ANSWER_TEXT, focus: 'error', marked: [] },
    { step: 'code step', status: 400, text: SETUP_TEXT, focus: 'error', marked: [] },
    { step: 'code step', status: 409, text: SETUP_TEXT, focus: 'error', marked: [] },
    { step: 'code step', status: 404, text: CODE_TEXT, focus: 'code', marked: ['code'] },
  ];
  describe.each(rows)(
    'refusal in the $step with status $status $errorCode',
    ({ step, status, errorCode, text, focus, marked }) => {
      it('shows its text, speaks once, focuses one target and marks exactly its fields', async () => {
        const detail = `DETAIL-${step}-${status}-${errorCode ?? 'none'}`;
        renderPage();
        if (step === 'code step') {
          await toCodeStep();
          vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(status, detail, errorCode));
          await userEvent.click(screen.getByRole('button', { name: /code bestätigen/i }));
        } else {
          vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(status, detail, errorCode));
          await fillPassword('operator@example.com', 'wrong');
          await userEvent.click(screen.getByRole('button', { name: /^anmelden$/i }));
        }
        await screen.findByText(text);
        await expectSpokenOnce(text);

        // the exact focus target
        if (focus === 'error') {
          await waitFor(() =>
            expect(document.activeElement).toBe(document.getElementById('instance-login-error')),
          );
        } else {
          const codeField = screen.getByLabelText(/totp-code/i) as HTMLInputElement;
          await waitFor(() => expect(codeField).toHaveFocus());
          expect(codeField.selectionStart).toBe(0);
          expect(codeField.selectionEnd).toBe(codeField.value.length);
          expect(codeField.value).toHaveLength(6);
        }

        // exactly the marked fields carry aria-invalid
        const fieldFor = {
          email: () => screen.getByLabelText(/e-mail-adresse/i),
          password: () => screen.getByLabelText(/^passwort[\s*]*$/i),
          code: () => screen.getByLabelText(/totp-code/i),
        };
        const expected = marked.map((f) => fieldFor[f]());
        const actual = Array.from(document.querySelectorAll('[aria-invalid="true"]'));
        expect(actual).toHaveLength(expected.length);
        for (const el of expected) expect(actual).toContain(el);

        // the detail text of the server is never on the page
        expect(document.body.textContent).not.toContain(detail);
        expect(document.body.textContent).not.toContain('DETAIL-');
      });
    },
  );

  // A second refusal in a row moves the focus again and is spoken once again.
  it('moves the focus to the error again on a second refusal of the first step', async () => {
    vi.mocked(apiClient.post).mockRejectedValue(new ApiError(401, 'Invalid credentials'));
    renderPage();
    await fillPassword('operator@example.com', 'wrong');
    await userEvent.click(screen.getByRole('button', { name: /^anmelden$/i }));
    await screen.findByText(FIRST_TEXT);
    await expectSpokenOnce(FIRST_TEXT);

    // the click moves the focus to the button, away from the error
    await userEvent.click(screen.getByRole('button', { name: /^anmelden$/i }));
    await waitFor(() => expect(apiClient.post).toHaveBeenCalledTimes(2));
    await screen.findByText(FIRST_TEXT);
    await waitFor(() => expect(document.activeElement).toBe(document.getElementById('instance-login-error')));
    await expectSpokenOnce(FIRST_TEXT);
  });

  it('moves the focus to the code field again on a second refusal of the code step', async () => {
    renderPage();
    await toCodeStep();
    vi.mocked(apiClient.post).mockRejectedValue(new ApiError(401, 'Invalid credentials'));
    await userEvent.click(screen.getByRole('button', { name: /code bestätigen/i }));
    await screen.findByText(CODE_TEXT);
    const codeField = screen.getByLabelText(/totp-code/i) as HTMLInputElement;
    await waitFor(() => expect(codeField).toHaveFocus());
    await expectSpokenOnce(CODE_TEXT);

    await userEvent.click(screen.getByRole('button', { name: /code bestätigen/i }));
    await waitFor(() => expect(apiClient.post).toHaveBeenCalledTimes(3));
    await screen.findByText(CODE_TEXT);
    await waitFor(() => expect(codeField).toHaveFocus());
    expect(codeField.selectionStart).toBe(0);
    expect(codeField.selectionEnd).toBe(6);
    await expectSpokenOnce(CODE_TEXT);
  });

  // The notices are not refusals: no focus move, role=alert stays.
  it('keeps role=alert on the session notices and moves no focus', () => {
    for (const reason of ['session_revoked', 'session_expired']) {
      const { unmount } = renderPage(`/instance/login?reason=${reason}`);
      expect(screen.getByRole('alert')).toHaveTextContent(
        reason === 'session_revoked'
          ? 'Deine Sitzung wurde aus Sicherheitsgründen beendet'
          : 'Deine Sitzung ist abgelaufen',
      );
      expect(document.body).toHaveFocus();
      unmount();
    }
  });

  it('shows the rate-limit message on a 429', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(429, 'Too many requests'));
    renderPage();
    await fillPassword();
    await userEvent.click(screen.getByRole('button', { name: /^anmelden$/i }));

    await waitFor(() =>
      expect(screen.getByText(/zu viele anmeldeversuche/i)).toBeInTheDocument(),
    );
  });

  it('navigates straight to the operator console on a successful password + code login', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { status: 'success', email: 'operator@example.com' },
    });
    renderPage();
    await fillPassword();
    await userEvent.type(screen.getByLabelText(/totp-code/i), '654321');
    await userEvent.click(screen.getByRole('button', { name: /^anmelden$/i }));

    expect(vi.mocked(apiClient.post)).toHaveBeenCalledWith('/api/instance/auth/login', {
      email: 'operator@example.com',
      password: 'correct horse',
      totp_code: '654321',
    });
    await waitFor(() => expect(screen.getByText('Operator console')).toBeInTheDocument());
  });

  // A password change ends the operator's other sessions. The interceptor lands
  // them here with reason=session_revoked and the page explains it through its
  // own register.
  it('shows the session-revoked notice when reason=session_revoked', () => {
    renderPage('/instance/login?reason=session_revoked');

    expect(
      screen.getByText('Deine Sitzung wurde aus Sicherheitsgründen beendet. Bitte melde dich neu an.'),
    ).toBeInTheDocument();
  });

  // An operator whose session ran out is told so.
  it('shows the session-expired notice when reason=session_expired', () => {
    renderPage('/instance/login?reason=session_expired');
    expect(screen.getByText('Deine Sitzung ist abgelaufen. Bitte melde dich neu an.')).toBeInTheDocument();
  });

  it('shows no notice without the reason parameter', () => {
    renderPage();

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  // The sign-in has one banner and one main, and no text outside a landmark.
  it('keeps all text of the password step inside landmarks, with one banner and one main', () => {
    const { container } = renderPage();

    expect(screen.getAllByRole('banner')).toHaveLength(1);
    expect(screen.getAllByRole('main')).toHaveLength(1);
    expectAllTextInLandmarks(container);
  });

  it('keeps all text of the second-factor step inside landmarks, with one banner and one main', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        status: 'totp_setup_required',
        totp_setup_required: true,
        provisioning_uri: 'otpauth://totp/Clavis:operator@example.com?secret=ABC&issuer=Clavis',
      },
    });
    const { container } = renderPage();
    await fillPassword();
    await userEvent.click(screen.getByRole('button', { name: /^anmelden$/i }));
    await waitFor(() => expect(screen.getByLabelText(/totp-code/i)).toBeInTheDocument());

    expect(screen.getAllByRole('banner')).toHaveLength(1);
    expect(screen.getAllByRole('main')).toHaveLength(1);
    expectAllTextInLandmarks(container);
  });

  it('has no axe accessibility violations on the password form', async () => {
    const { container } = renderPage();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('has no axe accessibility violations on the TOTP setup screen', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        status: 'totp_setup_required',
        totp_setup_required: true,
        provisioning_uri: 'otpauth://totp/Clavis:operator@example.com?secret=ABC&issuer=Clavis',
      },
    });
    const { container } = renderPage();
    await fillPassword();
    await userEvent.click(screen.getByRole('button', { name: /^anmelden$/i }));
    await waitFor(() => expect(screen.getByLabelText(/totp-code/i)).toBeInTheDocument());

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});

// The pressed button and the field with the focus keep it while the request
// runs. jsdom does not drop the focus of an element that turns disabled, as Chrome does, so
// these tests assert the structure that dropped it: no disabled attribute on the element with
// the focus, the busy state of the button in aria-disabled, and a submit that refuses a
// second request by itself.
describe('InstanceLogin — the focus while the request runs', () => {
  type Reply = { data: unknown };
  const pending = () => {
    let resolve!: (v: Reply) => void;
    let reject!: (e: unknown) => void;
    const promise = new Promise<Reply>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    return { promise, resolve, reject };
  };
  const SUCCESS = { data: { status: 'success', email: 'operator@example.com' } };

  beforeEach(() => {
    vi.resetAllMocks();
  });

  const expectButtonBusy = (name: RegExp) => {
    const button = screen.getByRole('button', { name });
    expect(button).toHaveFocus();
    expect(document.body).not.toHaveFocus();
    expect(button).not.toBeDisabled();
    expect(button).not.toHaveAttribute('disabled');
    expect(button).toHaveAttribute('aria-disabled', 'true');
    return button;
  };
  // Nothing in the form is natively disabled while the request runs, and the field that
  // has the focus is read-only.
  const expectFieldsKeepTheFocus = (focused: HTMLElement) => {
    expect(focused).toHaveFocus();
    expect(document.body).not.toHaveFocus();
    expect(focused).not.toHaveAttribute('disabled');
    expect(focused).toHaveAttribute('readonly');
    for (const input of document.querySelectorAll('input')) expect(input).not.toBeDisabled();
  };

  it('the submit button of the first step keeps the focus and its name, and sends once', async () => {
    const call = pending();
    vi.mocked(apiClient.post).mockReturnValueOnce(call.promise);
    const user = userEvent.setup();
    renderPage();
    await fillPassword();
    await user.click(screen.getByRole('button', { name: /^anmelden$/i }));

    const button = expectButtonBusy(/^anmelden$/i);
    await user.click(button);
    act(() => {
      button.click();
      button.click();
    });
    await user.keyboard('{Enter}');
    expect(apiClient.post).toHaveBeenCalledTimes(1);
    expectButtonBusy(/^anmelden$/i);

    await act(async () => call.reject(new ApiError(401, 'Invalid credentials')));
    const text = await screen.findByText(FIRST_TEXT);
    await waitFor(() => expect(text.closest('[id]')).toHaveFocus());
    expect(apiClient.post).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['e-mail', /e-mail-adresse/i],
    ['password', /^passwort[\s*]*$/i],
    ['code', /totp-code/i],
  ])('an Enter in the %s field of the first step keeps the focus in the field and sends once', async (_n, label) => {
    const call = pending();
    vi.mocked(apiClient.post).mockReturnValueOnce(call.promise);
    const user = userEvent.setup();
    renderPage();
    await fillPassword();
    const field = screen.getByLabelText(label);
    await user.click(field);
    await user.keyboard('{Enter}');

    expect(apiClient.post).toHaveBeenCalledTimes(1);
    expectFieldsKeepTheFocus(field);
    await user.keyboard('{Enter}');
    await user.keyboard('{Enter}');
    expect(apiClient.post).toHaveBeenCalledTimes(1);
    expectFieldsKeepTheFocus(field);

    await act(async () => call.reject(new ApiError(401, 'Invalid credentials')));
    const text = await screen.findByText(FIRST_TEXT);
    await waitFor(() => expect(text.closest('[id]')).toHaveFocus());
  });

  it('the first step hands the focus to the code field of the next step', async () => {
    const call = pending();
    vi.mocked(apiClient.post).mockReturnValueOnce(call.promise);
    const user = userEvent.setup();
    renderPage();
    await fillPassword();
    await user.click(screen.getByRole('button', { name: /^anmelden$/i }));
    expectButtonBusy(/^anmelden$/i);

    await act(async () => call.resolve(SETUP_REQUIRED));
    const codeField = await screen.findByLabelText(/totp-code/i);
    // The page heading takes the focus, as on a route change: the set-up text
    // and the QR code stay in reading order above the code field.
    const heading = screen.getByRole('heading', { level: 1 });
    expect(heading).toHaveFocus();
    expect(heading).toHaveAttribute('tabindex', '-1');
    expect(heading.closest('main')).not.toBeNull();
    expect(codeField).not.toHaveFocus();
    expect(document.body).not.toHaveFocus();
  });

  // The request has ended and its result is queued, but React has not rendered it yet: the
  // DOM still shows the busy button with the handler of the busy render. The handler
  // itself has to refuse a press here, because no native disabled attribute does.
  const pressBeforeTheResultRenders = async (settle: () => void, press: () => void) => {
    const env = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
    env.IS_REACT_ACT_ENVIRONMENT = false;
    try {
      settle();
      for (let i = 0; i < 10; i++) await Promise.resolve();
      // The render of the result has not committed: the old busy button is still there.
      expect(screen.getByRole('button', { name: /^anmelden$|code bestätigen/i })).toHaveAttribute(
        'aria-disabled',
        'true',
      );
      press();
    } finally {
      env.IS_REACT_ACT_ENVIRONMENT = true;
    }
    await act(async () => {});
  };

  it('a press between the result and its render sends no second sign-in', async () => {
    const call = pending();
    vi.mocked(apiClient.post).mockReturnValueOnce(call.promise);
    const user = userEvent.setup();
    renderPage();
    await fillPassword();
    await user.click(screen.getByRole('button', { name: /^anmelden$/i }));
    const button = expectButtonBusy(/^anmelden$/i);

    await pressBeforeTheResultRenders(() => call.resolve(SETUP_REQUIRED), () => button.click());
    expect(apiClient.post).toHaveBeenCalledTimes(1);
  });

  it('an Enter-submit between the result and its render sends no second sign-in', async () => {
    const call = pending();
    vi.mocked(apiClient.post).mockReturnValueOnce(call.promise);
    const user = userEvent.setup();
    renderPage();
    await fillPassword();
    const field = screen.getByLabelText(/e-mail-adresse/i);
    await user.click(field);
    await user.keyboard('{Enter}');
    expectFieldsKeepTheFocus(field);

    await pressBeforeTheResultRenders(
      () => call.resolve(SETUP_REQUIRED),
      () => field.closest('form')!.requestSubmit(),
    );
    expect(apiClient.post).toHaveBeenCalledTimes(1);
  });

  it('a press between the result and its render sends no second code confirmation', async () => {
    const user = userEvent.setup();
    renderPage();
    await toCodeStep();
    const call = pending();
    vi.mocked(apiClient.post).mockReturnValueOnce(call.promise);
    await user.click(screen.getByRole('button', { name: /code bestätigen/i }));
    const button = expectButtonBusy(/code bestätigen/i);

    await pressBeforeTheResultRenders(() => call.reject(new ApiError(401, 'Invalid code')), () => button.click());
    expect(apiClient.post).toHaveBeenCalledTimes(2);
  });

  it('two clicks in one tick from idle send one sign-in', async () => {
    const call = pending();
    vi.mocked(apiClient.post).mockReturnValueOnce(call.promise);
    renderPage();
    await fillPassword();
    const button = screen.getByRole('button', { name: /^anmelden$/i });
    expect(button).not.toHaveAttribute('aria-disabled', 'true');
    act(() => {
      button.click();
      button.click();
    });
    expect(apiClient.post).toHaveBeenCalledTimes(1);
    await act(async () => call.reject(new ApiError(401, 'Invalid credentials')));
  });

  it('two clicks in one tick from idle send one code confirmation', async () => {
    renderPage();
    await toCodeStep();
    const call = pending();
    vi.mocked(apiClient.post).mockReturnValueOnce(call.promise);
    const button = screen.getByRole('button', { name: /code bestätigen/i });
    act(() => {
      button.click();
      button.click();
    });
    expect(apiClient.post).toHaveBeenCalledTimes(2);
    await act(async () => call.reject(new ApiError(401, 'Invalid code')));
  });

  it('the hand-over focuses the code field when the page has no heading', async () => {
    const call = pending();
    vi.mocked(apiClient.post).mockReturnValueOnce(call.promise);
    const user = userEvent.setup();
    renderPage();
    await fillPassword();
    await user.click(screen.getByRole('button', { name: /^anmelden$/i }));
    // The shell renders the heading; hide it from the lookup the way a page without one would be.
    const spy = vi.spyOn(document, 'querySelector').mockImplementation(() => null);
    try {
      await act(async () => call.resolve(SETUP_REQUIRED));
    } finally {
      spy.mockRestore();
    }
    expect(await screen.findByLabelText(/totp-code/i)).toHaveFocus();
  });

  it('the submit button of the code step keeps the focus and its name, and sends once', async () => {
    const user = userEvent.setup();
    renderPage();
    await toCodeStep();
    const call = pending();
    vi.mocked(apiClient.post).mockReturnValueOnce(call.promise);
    await user.click(screen.getByRole('button', { name: /code bestätigen/i }));

    const button = expectButtonBusy(/code bestätigen/i);
    await user.click(button);
    act(() => {
      button.click();
      button.click();
    });
    await user.keyboard('{Enter}');
    expect(apiClient.post).toHaveBeenCalledTimes(2);
    expectButtonBusy(/code bestätigen/i);

    await act(async () => call.reject(new ApiError(401, 'Invalid code')));
    await screen.findByText(CODE_TEXT);
    await waitFor(() => expect(screen.getByLabelText(/totp-code/i)).toHaveFocus());
    expect(apiClient.post).toHaveBeenCalledTimes(2);
  });

  it('an Enter in the code field of the code step keeps the focus in the field and sends once', async () => {
    const user = userEvent.setup();
    renderPage();
    await toCodeStep();
    const call = pending();
    vi.mocked(apiClient.post).mockReturnValueOnce(call.promise);
    const field = screen.getByLabelText(/totp-code/i);
    await user.click(field);
    await user.keyboard('{Enter}');

    expect(apiClient.post).toHaveBeenCalledTimes(2);
    expectFieldsKeepTheFocus(field);
    await user.keyboard('{Enter}');
    await user.keyboard('{Enter}');
    expect(apiClient.post).toHaveBeenCalledTimes(2);
    expectFieldsKeepTheFocus(field);

    await act(async () => call.reject(new ApiError(401, 'Invalid code')));
    await screen.findByText(CODE_TEXT);
    await waitFor(() => expect(field).toHaveFocus());
  });

  it('a success of the code step still navigates to the console', async () => {
    const user = userEvent.setup();
    renderPage();
    await toCodeStep();
    const call = pending();
    vi.mocked(apiClient.post).mockReturnValueOnce(call.promise);
    await user.click(screen.getByRole('button', { name: /code bestätigen/i }));
    await act(async () => call.resolve(SUCCESS));
    expect(await screen.findByText('Operator console')).toBeInTheDocument();
  });
});
