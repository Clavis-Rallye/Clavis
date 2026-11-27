import { afterEach, describe, it, expect, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, useParams } from 'react-router';
import { axe } from 'vitest-axe';
import NoRunsAvailable from './NoRunsAvailable';
import {
  DEFAULT_INSTANCE_CONFIG,
  __resetInstanceConfigForTests,
} from '../config/instanceConfig';
import {
  __resetOperatorNoticeForTests,
  type OperatorNotice as OperatorNoticeData,
} from '../config/operatorNotice';

// Navigation target stub — renders the :code param so the test can assert
// where the join button navigated.
const JoinStub = () => {
  const { code } = useParams();
  return <div>join-stub:{code}</div>;
};

const renderPage = (entry = '/no-runs') =>
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/no-runs" element={<NoRunsAvailable />} />
        <Route path="/join/:code" element={<JoinStub />} />
      </Routes>
    </MemoryRouter>,
  );

// The operator notice stands directly above the legal links, outside main
// (spec 095, C9).
const operatorNotice: OperatorNoticeData = {
  version: 1,
  logos: [{ src: 'programme.svg', alt: { de: 'Logo des Programms' }, height: 48 }],
  text: { de: 'Dieses Angebot wird gefoerdert.' },
  links: [{ label: 'example.org/programme', url: 'https://example.org/programme' }],
};

function expectNoticeAboveLegalLinks() {
  const region = screen.getByRole('region', { name: 'Hinweis des Betreibers' });
  const nav = screen.getByRole('navigation', { name: 'Rechtliches' });
  // Same parent, the notice right before the row of links.
  expect(region.nextElementSibling).toBe(nav);
  expect(nav.compareDocumentPosition(region) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  expect(screen.getByRole('main')).not.toContainElement(region);
  return region;
}

describe('<NoRunsAvailable>', () => {
  beforeEach(() => {
    __resetInstanceConfigForTests();
  });

  // Brand, an explanation of how to join a run, and a link back. The page never
  // claims that no run exists: participants cannot list runs, so that claim would
  // be ungrounded. The brand comes from the instance config.
  it('shows the configured brand, the explanation and a link back', async () => {
    __resetInstanceConfigForTests({
      ...DEFAULT_INSTANCE_CONFIG,
      brand_name: 'Stadtrallye Musterstadt',
    });
    const { container } = renderPage();

    expect(screen.getByText('Stadtrallye Musterstadt')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Noch in keinem Lauf' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Beitrittscode oder -link.*Spielleitung/),
    ).toBeInTheDocument();
    const back = screen.getByRole('link', { name: 'Zur Startseite' });
    expect(back).toHaveAttribute('href', '/');
    // The heading sits in main, so the route focus can land on it.
    expect(screen.getByRole('main')).toContainElement(
      screen.getByRole('heading', { level: 1 }),
    );
    // The page links the legal pages.
    expect(screen.getByRole('navigation', { name: 'Rechtliches' })).toBeInTheDocument();

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  // All content stands inside a landmark. The logo row is the header, the card is the one main.
  it('puts the logo link into the banner landmark and has one main', () => {
    renderPage();

    const banner = screen.getByRole('banner');
    const logoLink = within(banner).getByRole('link', { name: 'Clavis' });
    expect(logoLink).toHaveAttribute('href', '/');
    expect(screen.getAllByRole('main')).toHaveLength(1);
    expect(screen.getByRole('main')).not.toContainElement(banner);
  });

  it('keeps the join button disabled while the code is empty', async () => {
    renderPage();
    const user = userEvent.setup();

    const join = screen.getByRole('button', { name: 'Beitreten' });
    expect(join).toBeDisabled();

    // Whitespace is not a code either — trimmed empty stays disabled.
    await user.type(screen.getByLabelText('Beitrittscode'), '   ');
    expect(join).toBeDisabled();
  });

  it('navigates to /join/<CODE> with the trimmed, upper-case code', async () => {
    renderPage();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Beitrittscode'), ' ab12 ');
    await user.click(screen.getByRole('button', { name: 'Beitreten' }));

    expect(await screen.findByText('join-stub:AB12')).toBeInTheDocument();
  });

  // A participant that the coordinator removed lands here from the run and reads why.
  it('says that the person is no longer in a team when reason=removed', () => {
    renderPage('/no-runs?reason=removed');
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Du bist in deinem letzten Lauf in keinem Team mehr.',
    );
  });

  it('shows no notice without a reason', () => {
    renderPage();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows the default brand when no config arrived', () => {
    renderPage();
    expect(screen.getByText('Clavis')).toBeInTheDocument();
  });
});

describe('<NoRunsAvailable> operator notice', () => {
  afterEach(() => {
    __resetOperatorNoticeForTests(null);
  });

  it('shows the notice above the legal links, outside main', () => {
    __resetOperatorNoticeForTests(operatorNotice);
    renderPage();
    expectNoticeAboveLegalLinks();
  });

  it('shows no notice region without a notice', () => {
    renderPage();
    expect(screen.queryByRole('region', { name: 'Hinweis des Betreibers' })).toBeNull();
  });
});
