import { describe, it, expect, afterEach } from 'vitest';
import { act, render, screen, within } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material';
import OperatorNotice from './OperatorNotice';
import { __resetOperatorNoticeForTests, type OperatorNotice as Notice } from '../config/operatorNotice';
import i18n from '../i18n';

// setup.ts pins the test language to German before each case.

function makeNotice(overrides: Partial<Notice> = {}): Notice {
  return {
    version: 1,
    logos: [
      {
        src: 'programme.svg',
        src_dark: 'programme-negative.svg',
        alt: { de: 'Logo des Programms', en: 'Logo of the programme' },
        height: 48,
      },
      { src: 'partner.png', alt: { de: 'Logo des Partners', en: 'Logo of the partner' }, height: 120 },
    ],
    text: { de: 'Dieses Angebot wird gefoerdert.', en: 'This service is funded.' },
    links: [{ label: 'example.org/programme', url: 'https://example.org/programme' }],
    ...overrides,
  };
}

afterEach(() => {
  __resetOperatorNoticeForTests(null);
});

describe('OperatorNotice', () => {
  it('renders nothing without a notice', () => {
    __resetOperatorNoticeForTests(null);
    const { container } = render(<OperatorNotice />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders the logos in file order from /operator/ with alt and exact height', () => {
    __resetOperatorNoticeForTests(makeNotice());
    render(<OperatorNotice />);
    const images = screen.getAllByRole('img');
    expect(images).toHaveLength(2);
    expect(images[0]).toHaveAttribute('src', '/operator/programme.svg');
    expect(images[0]).toHaveAttribute('alt', 'Logo des Programms');
    expect(images[0].style.height).toBe('48px');
    expect(images[0].style.width).toBe('auto');
    expect(images[0].style.maxHeight).toBe('');
    expect(images[1]).toHaveAttribute('src', '/operator/partner.png');
    expect(images[1]).toHaveAttribute('alt', 'Logo des Partners');
    expect(images[1].style.height).toBe('120px');
    expect(images[0]).not.toHaveAttribute('loading');
    expect(images[0]).toHaveAttribute('decoding', 'async');
  });

  it('shows markup in the text as characters and makes no element of it', () => {
    const markup = '<b>bold</b><script>alert(1)</script>';
    __resetOperatorNoticeForTests(makeNotice({ text: { de: markup } }));
    const { container } = render(<OperatorNotice />);
    expect(screen.getByText(markup)).toBeInTheDocument();
    expect(container.querySelector('b')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
  });

  it('forces the text of text_lang while the interface is English', async () => {
    await act(async () => {
      await i18n.changeLanguage('en');
    });
    __resetOperatorNoticeForTests(makeNotice({ text_lang: 'de' }));
    render(<OperatorNotice />);
    const text = screen.getByText('Dieses Angebot wird gefoerdert.');
    expect(text).toHaveAttribute('lang', 'de');
    expect(screen.queryByText('This service is funded.')).toBeNull();
  });

  it('follows the interface language without text_lang', async () => {
    __resetOperatorNoticeForTests(makeNotice());
    const { unmount } = render(<OperatorNotice />);
    expect(screen.getByText('Dieses Angebot wird gefoerdert.')).toHaveAttribute('lang', 'de');
    unmount();

    await act(async () => {
      await i18n.changeLanguage('en');
    });
    render(<OperatorNotice />);
    expect(screen.getByText('This service is funded.')).toHaveAttribute('lang', 'en');
    expect(screen.getAllByRole('img')[0]).toHaveAttribute('alt', 'Logo of the programme');
  });

  it('falls back to English for an interface language the notice lacks', async () => {
    await act(async () => {
      await i18n.changeLanguage('fr');
    });
    __resetOperatorNoticeForTests(makeNotice());
    render(<OperatorNotice />);
    expect(screen.getByText('This service is funded.')).toHaveAttribute('lang', 'en');
  });

  it('keeps the line breaks of the text', () => {
    __resetOperatorNoticeForTests(makeNotice({ text: { de: 'Zeile eins\nZeile zwei' } }));
    render(<OperatorNotice />);
    const text = screen.getByText(/Zeile eins/);
    expect(getComputedStyle(text).whiteSpace).toBe('pre-line');
  });

  it('renders each link as a new-tab anchor with noopener noreferrer', () => {
    __resetOperatorNoticeForTests(
      makeNotice({
        links: [
          { label: 'example.org/programme', url: 'https://example.org/programme' },
          { label: 'example.org/partner', url: 'http://example.org/partner' },
        ],
      }),
    );
    render(<OperatorNotice />);
    const links = screen.getAllByRole('link');
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveTextContent('example.org/programme');
    expect(links[0]).toHaveAttribute('href', 'https://example.org/programme');
    expect(links[0]).toHaveAttribute('target', '_blank');
    expect(links[0]).toHaveAttribute('rel', 'noopener noreferrer');
    expect(links[1]).toHaveAttribute('href', 'http://example.org/partner');
  });

  it('puts the text after the logos and the links after the text', () => {
    __resetOperatorNoticeForTests(makeNotice());
    render(<OperatorNotice />);
    const region = screen.getByRole('region');
    const img = within(region).getAllByRole('img')[1];
    const text = within(region).getByText('Dieses Angebot wird gefoerdert.');
    const link = within(region).getByRole('link');
    expect(img.compareDocumentPosition(text) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(text.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('uses src_dark in the dark theme and keeps src for a logo without one', () => {
    __resetOperatorNoticeForTests(makeNotice());
    render(
      <ThemeProvider theme={createTheme({ palette: { mode: 'dark' } })}>
        <OperatorNotice />
      </ThemeProvider>,
    );
    const images = screen.getAllByRole('img');
    expect(images[0]).toHaveAttribute('src', '/operator/programme-negative.svg');
    expect(images[1]).toHaveAttribute('src', '/operator/partner.png');
  });

  it('uses src in the light theme', () => {
    __resetOperatorNoticeForTests(makeNotice());
    render(
      <ThemeProvider theme={createTheme({ palette: { mode: 'light' } })}>
        <OperatorNotice />
      </ThemeProvider>,
    );
    expect(screen.getAllByRole('img')[0]).toHaveAttribute('src', '/operator/programme.svg');
  });

  it('is one region named in German and in English', async () => {
    __resetOperatorNoticeForTests(makeNotice());
    const { unmount } = render(<OperatorNotice />);
    expect(screen.getByRole('region', { name: 'Hinweis des Betreibers' })).toBeInTheDocument();
    unmount();

    await act(async () => {
      await i18n.changeLanguage('en');
    });
    render(<OperatorNotice />);
    expect(screen.getByRole('region', { name: 'Notice from the operator' })).toBeInTheDocument();
  });

  it('shows a notice that arrives after the first render', () => {
    __resetOperatorNoticeForTests(null);
    const { container } = render(<OperatorNotice />);
    expect(container).toBeEmptyDOMElement();
    act(() => {
      __resetOperatorNoticeForTests(makeNotice());
    });
    expect(screen.getByRole('region', { name: 'Hinweis des Betreibers' })).toBeInTheDocument();
    expect(screen.getAllByRole('img')).toHaveLength(2);
  });
});
