import { describe, it, expect, beforeEach } from 'vitest';
import { useEffect } from 'react';
import { act, render } from '@testing-library/react';
import { MemoryRouter, useNavigate } from 'react-router';
import DocumentTitle from './DocumentTitle';
import i18n from '../i18n';
import { __resetInstanceConfigForTests, DEFAULT_INSTANCE_CONFIG } from '../config/instanceConfig';

let navigate: ReturnType<typeof useNavigate>;
function Capture() {
  const nav = useNavigate();
  useEffect(() => {
    navigate = nav;
  }, [nav]);
  return null;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <DocumentTitle />
      <Capture />
    </MemoryRouter>,
  );
}

describe('DocumentTitle', () => {
  beforeEach(async () => {
    __resetInstanceConfigForTests(DEFAULT_INSTANCE_CONFIG);
    await i18n.changeLanguage('de');
  });

  it('sets the page name, a middle dot and the brand name', () => {
    renderAt('/stations');
    expect(document.title).toBe('Stationen · Clavis');
  });

  it('uses the brand name from the instance configuration', () => {
    __resetInstanceConfigForTests({ ...DEFAULT_INSTANCE_CONFIG, brand_name: 'Stadtrallye Musterstadt' });
    renderAt('/rallyes');
    expect(document.title).toBe('Rallyes · Stadtrallye Musterstadt');
  });

  it('shows the brand name alone on a route without an entry', () => {
    renderAt('/nowhere');
    expect(document.title).toBe('Clavis');
  });

  it('changes the title on every navigation without a reload', () => {
    renderAt('/runs');
    expect(document.title).toBe('Läufe · Clavis');
    act(() => {
      void navigate('/runs/new');
    });
    expect(document.title).toBe('Neuer Lauf · Clavis');
    act(() => {
      void navigate('/runs/7');
    });
    expect(document.title).toBe('Lauf · Clavis');
    act(() => {
      void navigate('/nowhere');
    });
    expect(document.title).toBe('Clavis');
  });

  it('follows the interface language and changes with it', async () => {
    renderAt('/stations');
    expect(document.title).toBe('Stationen · Clavis');
    await act(async () => {
      await i18n.changeLanguage('en');
    });
    expect(document.title).toBe('Stations · Clavis');
    await act(async () => {
      await i18n.changeLanguage('de');
    });
    expect(document.title).toBe('Stationen · Clavis');
  });

  it('keeps the page name when the brand arrives after the first render', () => {
    renderAt('/profile');
    expect(document.title).toBe('Profil · Clavis');
    act(() => {
      __resetInstanceConfigForTests({ ...DEFAULT_INSTANCE_CONFIG, brand_name: 'Rallye X' });
    });
    expect(document.title).toBe('Profil · Rallye X');
  });
});
