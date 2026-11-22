import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { axe } from 'vitest-axe';
import SafeHtml from './SafeHtml';
import { sanitizeRichText } from '../utils/sanitizeRichText';

const OWN_IMG = '<img src="/api/orgs/stadt/media/0000/media/abc_rathaus.png" alt="Das Rathaus">';

describe('SafeHtml', () => {
  it('renders legacy plain text with its line breaks and no markup parsing', () => {
    const { container } = render(<SafeHtml html={'Zeile 1\nZeile 2 & 3 < 4'} />);
    expect(container.textContent).toBe('Zeile 1\nZeile 2 & 3 < 4');
    expect(container.querySelector('p')).toBeNull();
  });

  it('makes a web address in a legacy plain text a link, and keeps the text around it', () => {
    // A text stored before the editor had its URL as plain text.
    // The URL must still become a clickable link.
    const { container } = render(
      <SafeHtml html={'Mehr dazu:\nhttps://augsburg.de/rathaus oder www.fuggerei.de.'} />,
    );
    const anchors = [...container.querySelectorAll('a')];
    expect(anchors.map((a) => a.getAttribute('href'))).toEqual([
      'https://augsburg.de/rathaus',
      'https://www.fuggerei.de',
    ]);
    expect(anchors.every((a) => a.getAttribute('target') === '_blank')).toBe(true);
    expect(anchors.every((a) => a.getAttribute('rel') === 'noopener noreferrer')).toBe(true);
    expect(container.textContent).toBe('Mehr dazu:\nhttps://augsburg.de/rathaus oder www.fuggerei.de.');
  });

  it('links nothing but http and https in a legacy plain text', () => {
    const { container } = render(
      <SafeHtml html={'javascript:alert(1) data:text/html,x https://ok.example/"onmouseover="x'} />,
    );
    const anchors = [...container.querySelectorAll('a')];
    expect(anchors).toHaveLength(1);
    expect(anchors[0].getAttribute('href')).toBe('https://ok.example/');
    expect(anchors[0].getAttribute('onmouseover')).toBeNull();
  });

  it('keeps allowed markup and the organisation image, drops foreign images', () => {
    const html = `<h2>Rathaus</h2><p><strong>fett</strong> ${OWN_IMG}<img src="https://tracker.example/p.gif" alt=""></p>`;
    const { container } = render(<SafeHtml html={html} />);
    expect(container.querySelector('h2')?.textContent).toBe('Rathaus');
    expect(container.querySelector('strong')?.textContent).toBe('fett');
    const images = container.querySelectorAll('img');
    expect(images).toHaveLength(1);
    expect(images[0].getAttribute('src')).toBe('/api/orgs/stadt/media/0000/media/abc_rathaus.png');
    expect(images[0].getAttribute('alt')).toBe('Das Rathaus');
    expect(images[0].getAttribute('loading')).toBe('lazy');
  });

  it('strips scripts, handlers, frames and styles', () => {
    const cleaned = sanitizeRichText(
      '<p onclick="x()" style="color:red">Text</p><script>alert(1)</script><iframe src="https://evil.example"></iframe><a href="javascript:alert(1)">k</a>',
    );
    expect(cleaned).not.toContain('<script');
    expect(cleaned).not.toContain('<iframe');
    expect(cleaned).not.toContain('onclick');
    expect(cleaned).not.toContain('style=');
    expect(cleaned).not.toContain('javascript:');
    expect(cleaned).toContain('Text');
  });

  it('passes the writing direction through', () => {
    const { container } = render(<SafeHtml html="<p>مرحبا</p>" dir="rtl" />);
    expect(container.firstElementChild?.getAttribute('dir')).toBe('rtl');
  });

  it('has no accessibility violations with an image', async () => {
    const { container } = render(<SafeHtml html={`<p>Vor dem Bild</p>${OWN_IMG}<p>Danach</p>`} />);
    expect(await axe(container)).toHaveNoViolations();
  });

  it('wraps a table in a labelled scroll region that is not part of the stored text', () => {
    const html = '<table><tbody><tr><th>Kopf</th></tr><tr><td>Zelle</td></tr></tbody></table>';
    const { container } = render(<SafeHtml html={html} />);
    const region = container.querySelector('[role="region"]');
    expect(region?.getAttribute('aria-label')).toBe('Tabelle, seitlich verschiebbar');
    expect(region?.getAttribute('tabindex')).toBe('0');
    expect(region?.querySelector('table')).not.toBeNull();
    expect(html).not.toContain('region');
  });

  it('keeps the rules of the 0.9.1 elements and adds the rules for the new ones', () => {
    const html =
      `<figure data-width="50" data-align="center">${OWN_IMG}<figcaption>Unterschrift</figcaption></figure>` +
      '<blockquote>Zitat</blockquote><hr><table><tbody><tr><th>K</th><td>Z</td></tr></tbody></table>';
    const { container } = render(<SafeHtml html={html} />);
    const root = container.firstElementChild as HTMLElement;
    const css = [...document.querySelectorAll('style')].map((s) => s.textContent ?? '').join('\n');
    const cls = '.' + root.className.split(/\s+/).filter((c) => c.startsWith('css-'))[0];
    for (const needle of [
      `${cls} img{display:block;max-width:100%;height:auto;`,
      'figure[data-width="25"]',
      'figure[data-width="50"]',
      'figure[data-width="75"]',
      'figure[data-width="100"]',
      'img[data-width="50"]',
      'data-align="center"',
      'data-align="right"',
      'figcaption',
      'blockquote',
      'border-inline-start',
      ' hr',
      ' table',
      'overflow-wrap',
    ]) {
      expect(css.replace(/\s+/g, '')).toContain(needle.replace(/\s+/g, ''));
    }
    expect(getComputedStyle(container.querySelector('figure') as Element).width).toBe('50%');
  });
});
