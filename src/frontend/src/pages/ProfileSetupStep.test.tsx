import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router';
import ProfileSetupStep from './ProfileSetupStep';
import NoRunsAvailable from './NoRunsAvailable';
import { AuthProvider } from '../auth/AuthContext';
import { WizardStateProvider } from '../wizard/WizardStateContext';
import apiClient from '../api/client';
import {
  DEFAULT_INSTANCE_CONFIG,
  __resetInstanceConfigForTests,
} from '../config/instanceConfig';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    default: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
    },
  };
});

const renderStepAt = (path: string) =>
  render(
    <AuthProvider>
      <WizardStateProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route
              path="/register"
              element={<ProfileSetupStep guestModeDefault={false} />}
            />
            <Route path="/no-runs" element={<NoRunsAvailable />} />
          </Routes>
        </MemoryRouter>
      </WizardStateProvider>
    </AuthProvider>,
  );

// Direct /register flow with an org preselected via URL, so the credential
// fields render instead of the OrganizationPicker.
const renderStep = () => renderStepAt('/register?org=demo');

// A probe for the /2fa-setup route that renders location.state.from, so a test
// can assert on the landing carried across the 2FA hop.
const FromStateProbe = () => {
  const location = useLocation();
  const from =
    (location.state as { from?: string } | null)?.from ?? '(none)';
  return <div data-testid="from-state">{from}</div>;
};

const renderStepWithCode = (code: string) =>
  render(
    <AuthProvider>
      <WizardStateProvider>
        <MemoryRouter initialEntries={[`/join/${code}/profile`]}>
          <Routes>
            <Route
              path="/join/:code/profile"
              element={<ProfileSetupStep guestModeDefault={false} />}
            />
            <Route path="/2fa-setup" element={<FromStateProbe />} />
          </Routes>
        </MemoryRouter>
      </WizardStateProvider>
    </AuthProvider>,
  );

describe('<ProfileSetupStep>', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    vi.mocked(apiClient.get).mockRejectedValue(new Error('Not authenticated'));
    __resetInstanceConfigForTests();
  });

  // The display name is required for assistive technology only. The browser's own
  // validation bubble must not appear, because the wizard validates the field itself.
  it('marks the display name field as required for assistive tech only', () => {
    renderStep();
    const input = screen.getByLabelText('Anzeigename');
    expect(input).toHaveAttribute('id', 'profile-username');
    expect(input).toHaveAttribute('aria-required', 'true');
    expect(input).not.toHaveAttribute('required');
  });

  // Chrome warns "Password field is not contained in a form". Password managers
  // only offer to save credentials for fields inside a <form>.
  it('renders the credential fields inside a <form> element', () => {
    renderStep();
    const password = screen.getByLabelText('Passwort');
    const passwordConfirm = screen.getByLabelText('Passwort bestätigen');
    const email = screen.getByLabelText('E-Mail-Adresse');
    expect(password.closest('form')).not.toBeNull();
    expect(passwordConfirm.closest('form')).not.toBeNull();
    expect(email.closest('form')).not.toBeNull();
    expect(password).toHaveAttribute('autocomplete', 'new-password');
    expect(passwordConfirm).toHaveAttribute('autocomplete', 'new-password');
    expect(email).toHaveAttribute('autocomplete', 'email');
  });

  it('submits via the form (Enter key path) and runs validation', async () => {
    renderStep();
    const password = screen.getByLabelText('Passwort');
    // Submitting the empty form must run the same validation as the
    // Continue button — proves onSubmit is wired to onContinue.
    fireEvent.submit(password.closest('form') as HTMLFormElement);
    await waitFor(() => {
      expect(
        screen.getByText('Bitte mindestens 2 Zeichen eingeben.'),
      ).toBeInTheDocument();
    });
    // No registration request may leave while validation fails.
    expect(vi.mocked(apiClient.post)).not.toHaveBeenCalled();
  });

  it('keeps Back and guest-toggle as non-submit buttons', () => {
    renderStep();
    const buttons = screen.getAllByRole('button');
    const submitButtons = buttons.filter(
      (b) => b.getAttribute('type') === 'submit',
    );
    expect(submitButtons).toHaveLength(1);
    expect(submitButtons[0]).toHaveTextContent(/Weiter|Continue/);
  });

  // On a single-organization instance the organization step is skipped:
  // no picker, the one organization is preselected.
  it('skips the organization picker when single_org_slug is set', () => {
    __resetInstanceConfigForTests({
      ...DEFAULT_INSTANCE_CONFIG,
      single_org_slug: 'einzige-org',
    });
    renderStepAt('/register');

    // Credential fields render directly — the picker headline never does.
    expect(screen.getByLabelText('E-Mail-Adresse')).toBeInTheDocument();
    expect(
      screen.queryByRole('heading', { name: 'Wähle deine Organisation' }),
    ).not.toBeInTheDocument();
  });

  it('registers into the single organization and lands on the no-runs page (codeless registration)', async () => {
    __resetInstanceConfigForTests({
      ...DEFAULT_INSTANCE_CONFIG,
      single_org_slug: 'einzige-org',
    });
    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValueOnce({ data: {} });
    renderStepAt('/register');

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Anzeigename'), 'Alex');
    await user.type(screen.getByLabelText('E-Mail-Adresse'), 'alex@example.com');
    await user.type(screen.getByLabelText('Passwort'), 'SecurePassword123!');
    await user.type(screen.getByLabelText(/passwort bestätigen/i), 'SecurePassword123!');
    await user.click(screen.getByRole('button', { name: /weiter/i }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/api/auth/register', {
        organization_slug: 'einzige-org',
        email: 'alex@example.com',
        password: 'SecurePassword123!',
        display_name: 'Alex',
      });
    });
    // Codeless registration on a single-org instance: the no-runs landing,
    // not /dashboard.
    expect(
      await screen.findByRole('heading', { name: 'Noch in keinem Lauf' }),
    ).toBeInTheDocument();
  });

  // A participant with a join code who must set up 2FA must not lose the join flow.
  // The computed landing (not /dashboard) travels to TwoFactorSetup as state.from.
  it('carries the computed landing to /2fa-setup as state.from when registration requires 2FA', async () => {
    sessionStorage.setItem('clavis_wizard_org_slug', 'demo');
    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValueOnce({ data: { requires_2fa: true } });

    renderStepWithCode('ABC123');

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Anzeigename'), 'Alex');
    await user.type(screen.getByLabelText('E-Mail-Adresse'), 'alex@example.com');
    await user.type(screen.getByLabelText('Passwort'), 'SecurePassword123!');
    await user.type(screen.getByLabelText(/passwort bestätigen/i), 'SecurePassword123!');
    await user.click(screen.getByRole('button', { name: /weiter/i }));

    await waitFor(() => {
      expect(screen.getByTestId('from-state')).toHaveTextContent(
        '/join/ABC123/team/find',
      );
    });
  });

  // A name that was typed but not yet confirmed with "Weiter" survives leaving
  // the step and coming back.
  it('keeps a typed, unconfirmed name when the step is left and opened again', async () => {
    const tree = (
      <AuthProvider>
        <WizardStateProvider>
          <MemoryRouter initialEntries={['/join/ABC123/profile']}>
            <Routes>
              <Route path="/join/:code/profile" element={<ProfileSetupStep guestModeDefault />} />
            </Routes>
          </MemoryRouter>
        </WizardStateProvider>
      </AuthProvider>
    );
    const first = render(tree);
    fireEvent.change(screen.getByLabelText('Anzeigename'), { target: { value: 'TEST Handy-Gast' } });
    first.unmount();

    render(tree);
    expect(screen.getByLabelText('Anzeigename')).toHaveValue('TEST Handy-Gast');
  });

  // "Neues Konto erstellen" on the entry step opens this step with the guest
  // switch off, "Als Gast mitmachen" with it on.
  it('starts in account mode with ?account=1 and in guest mode without it', () => {
    const at = (path: string) =>
      render(
        <AuthProvider>
          <WizardStateProvider>
            <MemoryRouter initialEntries={[path]}>
              <Routes>
                <Route path="/join/:code/profile" element={<ProfileSetupStep guestModeDefault />} />
              </Routes>
            </MemoryRouter>
          </WizardStateProvider>
        </AuthProvider>,
      );

    const account = at('/join/ABC123/profile?account=1');
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
    account.unmount();

    at('/join/ABC123/profile');
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
  });
});
