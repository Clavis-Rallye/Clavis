import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

import i18n from '../i18n';
import StationTipsDialog from './StationTipsDialog';
import type { StationTips } from '../api/types';

const tips: StationTips = {
  header: { de: 'Tipps', en: 'Tips' },
  links: [
    { label: { de: 'Stadtplan', en: 'City map' }, url: 'https://example.com/map' },
    // DE label deliberately blank — exercises the de -> en fallback in resolveLocalized.
    { label: { de: '', en: 'Museum site' }, url: 'https://example.com/museum' },
  ],
};

describe('StationTipsDialog', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de');
  });

  it('renders the resolved-locale header and links with correct href/target/rel', () => {
    render(<StationTipsDialog tips={tips} open onClose={vi.fn()} />);

    expect(screen.getByText('Tipps')).toBeInTheDocument();

    const links = screen.getAllByRole('link');
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveTextContent('Stadtplan');
    expect(links[0]).toHaveAttribute('href', 'https://example.com/map');
    expect(links[0]).toHaveAttribute('target', '_blank');
    expect(links[0]).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('falls back across locales via resolveLocalized when a link has no DE label', () => {
    render(<StationTipsDialog tips={tips} open onClose={vi.fn()} />);

    const links = screen.getAllByRole('link');
    // German UI, but the second link's `de` label is empty -> falls back to `en`.
    expect(links[1]).toHaveTextContent('Museum site');
    expect(links[1]).toHaveAttribute('href', 'https://example.com/museum');
  });

  it('shows an English header when the UI language is English', async () => {
    await i18n.changeLanguage('en');
    render(<StationTipsDialog tips={tips} open onClose={vi.fn()} />);

    expect(screen.getByText('Tips')).toBeInTheDocument();
  });
});
