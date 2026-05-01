import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { axe } from 'vitest-axe';
import { I18nextProvider } from 'react-i18next';
import { MemoryRouter } from 'react-router';
import i18n from '../i18n';
import OrganizationPicker from './OrganizationPicker';
import * as publicOrganizations from '../services/publicOrganizations';
import { AuthProvider } from '../auth/AuthContext';
import { WizardStateProvider } from '../wizard/WizardStateContext';

vi.mock('../services/publicOrganizations', () => ({
  listAll: vi.fn(),
}));

const mockListAll = vi.mocked(publicOrganizations.listAll);

function renderPicker(onSelect = vi.fn()) {
  render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter>
        <AuthProvider>
          <WizardStateProvider>
            <OrganizationPicker onSelect={onSelect} />
          </WizardStateProvider>
        </AuthProvider>
      </MemoryRouter>
    </I18nextProvider>,
  );
  return onSelect;
}

describe('<OrganizationPicker>', () => {
  beforeEach(() => {
    mockListAll.mockReset();
  });

  afterEach(async () => {
    await i18n.changeLanguage('en');
  });

  it('lists organizations returned by listAll', async () => {
    mockListAll.mockResolvedValueOnce([
      { slug: 'augsburg', name: 'Augsburg' },
      { slug: 'munich', name: 'Munich' },
    ]);
    renderPicker();

    expect(await screen.findByText('Augsburg')).toBeInTheDocument();
    expect(screen.getByText('Munich')).toBeInTheDocument();
  });

  it('renders a subtitle under the heading explaining the choice', async () => {
    await i18n.changeLanguage('en');
    mockListAll.mockResolvedValueOnce([{ slug: 'augsburg', name: 'Augsburg' }]);
    renderPicker();

    await screen.findByText('Augsburg');
    expect(screen.getByText("This determines which rallye you'll join.")).toBeInTheDocument();
  });

  it('renders the German subtitle when the UI language is German', async () => {
    await i18n.changeLanguage('de');
    mockListAll.mockResolvedValueOnce([{ slug: 'augsburg', name: 'Augsburg' }]);
    renderPicker();

    await screen.findByText('Augsburg');
    expect(screen.getByText('Das legt fest, an welcher Rallye du teilnimmst.')).toBeInTheDocument();
  });

  it('uses the localized common.actions.continue key for the button', async () => {
    // The label comes from common.actions.continue. The key tc('continue') does not exist and
    // would fall back to the English "Continue" even in a German UI.
    await i18n.changeLanguage('de');
    mockListAll.mockResolvedValueOnce([{ slug: 'augsburg', name: 'Augsburg' }]);
    renderPicker();

    await screen.findByText('Augsburg');
    expect(screen.getByRole('button', { name: 'Weiter' })).toBeInTheDocument();
  });

  it('disables Continue until an org is selected, then calls onSelect', async () => {
    mockListAll.mockResolvedValueOnce([{ slug: 'augsburg', name: 'Augsburg' }]);
    const onSelect = renderPicker();

    const cta = await screen.findByRole('button', { name: /continue|weiter/i });
    expect(cta).toBeDisabled();

    fireEvent.click(screen.getByText('Augsburg'));
    await waitFor(() => expect(cta).toBeEnabled());

    fireEvent.click(cta);
    expect(onSelect).toHaveBeenCalledWith({ slug: 'augsburg', name: 'Augsburg' });
  });
  // A slow or failing server is not an empty installation.
  it('says that loading failed and offers a retry, which loads again', async () => {
    mockListAll.mockRejectedValueOnce(new Error('timeout'));
    mockListAll.mockResolvedValueOnce([{ slug: 'stadt-augsburg', name: 'Stadt Augsburg' }]);
    renderPicker();

    expect(await screen.findByText(/konnten nicht geladen werden/)).toBeInTheDocument();
    expect(screen.queryByText(/noch keine Organisation eingerichtet/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Erneut versuchen' }));

    expect(await screen.findByText('Stadt Augsburg')).toBeInTheDocument();
    expect(screen.queryByText(/konnten nicht geladen werden/)).not.toBeInTheDocument();
    expect(mockListAll).toHaveBeenCalledTimes(2);
  });

  it('says that no organization is set up when the list is empty', async () => {
    mockListAll.mockResolvedValueOnce([]);
    renderPicker();

    expect(await screen.findByText('Hier ist noch keine Organisation eingerichtet.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Erneut versuchen' })).not.toBeInTheDocument();
  });

  // The buttons of the list sit in list items, and axe's "list" rule finds nothing.
  it('renders the organizations as list items of a list', async () => {
    mockListAll.mockResolvedValueOnce([
      { slug: 'augsburg', name: 'Augsburg' },
      { slug: 'munich', name: 'Munich' },
    ]);
    renderPicker();

    const list = (await screen.findByRole('list')) as HTMLElement;
    expect(Array.from(list.children).map((c) => c.tagName)).toEqual(['LI', 'LI']);
    expect(screen.getAllByRole('listitem')).toHaveLength(2);

    const results = await axe(list, { rules: { list: { enabled: true } } });
    expect(results.violations.filter((v) => v.id === 'list')).toEqual([]);
  });
});
