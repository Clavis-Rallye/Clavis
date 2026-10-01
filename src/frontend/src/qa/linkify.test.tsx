import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { linkify, splitLinks, trimUrl } from './linkify';

describe('splitLinks', () => {
  it('leaves a text without an address alone', () => {
    expect(splitLinks('Tippe auf Weiter.')).toEqual([{ kind: 'text', value: 'Tippe auf Weiter.' }]);
  });

  it('keeps sentence punctuation outside the link', () => {
    expect(splitLinks('Oeffne https://stand.example/join, dann weiter.')).toEqual([
      { kind: 'text', value: 'Oeffne ' },
      { kind: 'link', value: 'https://stand.example/join' },
      { kind: 'text', value: ', dann weiter.' },
    ]);
  });

  it('finds several addresses and keeps the line breaks between them', () => {
    const parts = splitLinks('1. http://a.example:8025\n2. https://b.example/x?y=1#z.');
    expect(parts.filter((p) => p.kind === 'link').map((p) => p.value)).toEqual([
      'http://a.example:8025',
      'https://b.example/x?y=1#z',
    ]);
    expect(parts.map((p) => p.value).join('')).toBe('1. http://a.example:8025\n2. https://b.example/x?y=1#z.');
  });

  it('links only http and https', () => {
    expect(splitLinks('javascript:alert(1) mailto:a@b.c ftp://x.example').every((p) => p.kind === 'text')).toBe(true);
  });
});

describe('trimUrl', () => {
  it('drops an unbalanced closing bracket and keeps a balanced one', () => {
    expect(trimUrl('https://x.example/a)')).toBe('https://x.example/a');
    expect(trimUrl('https://x.example/wiki/Foo_(bar)')).toBe('https://x.example/wiki/Foo_(bar)');
    expect(trimUrl('https://x.example/a).')).toBe('https://x.example/a');
  });
});

describe('linkify', () => {
  function renderText(text: string) {
    return render(<MemoryRouter>{linkify(text)}</MemoryRouter>);
  }

  it('opens another host in a new tab without an opener', () => {
    renderText('Mails: http://mail.example:8025.');
    const link = screen.getByRole('link', { name: 'http://mail.example:8025' });
    expect(link).toHaveAttribute('href', 'http://mail.example:8025');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('keeps a same-origin link in the same tab, as an App route', () => {
    renderText(`Gehe zu ${window.location.origin}/join?code=ABC.`);
    const link = screen.getByRole('link', { name: `${window.location.origin}/join?code=ABC` });
    expect(link).toHaveAttribute('href', '/join?code=ABC');
    expect(link).not.toHaveAttribute('target');
  });

  it('renders markup in the text as text, never as HTML', () => {
    const { container } = renderText('<b>fett</b> https://x.example');
    expect(container.querySelector('b')).toBeNull();
    expect(container.textContent).toBe('<b>fett</b> https://x.example');
  });
});
