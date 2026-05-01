import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router';
import { I18nextProvider } from 'react-i18next';
import { axe } from 'vitest-axe';
import i18n from '../i18n';
import WelcomeStep from './WelcomeStep';
import * as publicRuns from '../services/publicRuns';
import { AuthProvider } from '../auth/AuthContext';
import { WizardStateProvider } from '../wizard/WizardStateContext';

vi.mock('../services/publicRuns', () => ({
  fetchByCode: vi.fn(),
}));

const mockFetchByCode = vi.mocked(publicRuns.fetchByCode);

function renderAt(path: string, routePath = '/') {
  return render(
    <I18nextProvider i18n={i18n}>
      <AuthProvider>
        <WizardStateProvider>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route path={routePath} element={<WelcomeStep />} />
            </Routes>
          </MemoryRouter>
        </WizardStateProvider>
      </AuthProvider>
    </I18nextProvider>,
  );
}

describe('<WelcomeStep> a11y', () => {
  beforeEach(() => mockFetchByCode.mockReset());

  it('has no axe violations without a runCode', async () => {
    const { container } = renderAt('/');
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('has no axe violations after the rallye name loads', async () => {
    mockFetchByCode.mockResolvedValueOnce({
      run_id: 'r1',
      rallye_name: 'Augsburg Explorer',
      organization_slug: 'augsburg',
    });
    const { container, findByText } = renderAt('/join/ABC123', '/join/:code');
    await findByText(/Augsburg Explorer/);
    await waitFor(async () => {
      const results = await axe(container);
      expect(results).toHaveNoViolations();
    });
  });
});
