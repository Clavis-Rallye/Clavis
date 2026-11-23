import { describe, expect, it } from 'vitest';
import { RICH_TEXT_FILTER, sanitizeRichText, wrapTablesForScroll } from './sanitizeRichText';
import contract from './richTextContract.json';
import tableCases from './richTextTableCases.json';
import { EDITOR_091, RICH_TEXT_091_CORPUS, SANITIZED_091 } from './richText091.fixtures';

describe('sanitizeRichText on the 0.9.1 corpus', () => {
  it('records the same keys in all three fixture objects', () => {
    const keys = Object.keys(RICH_TEXT_091_CORPUS).sort();
    expect(Object.keys(SANITIZED_091).sort()).toEqual(keys);
    expect(Object.keys(EDITOR_091).sort()).toEqual(keys);
  });

  it.each(Object.keys(RICH_TEXT_091_CORPUS))('cleans %s as release 0.9.1 did', (key) => {
    expect(sanitizeRichText(RICH_TEXT_091_CORPUS[key])).toBe(SANITIZED_091[key]);
  });
});

const MEDIA = '/api/orgs/stadt/media/content/rathaus.jpg';

/** The output parsed into a fresh document, the way a browser would read it. */
function parse(html: string): HTMLElement {
  return new DOMParser().parseFromString(html, 'text/html').body;
}

describe('the filter lists and the contract file', () => {
  const RENDER_ONLY_TAGS = ['h1', 'code', 'pre'];

  it('allows the contract elements plus the three render-only tags', () => {
    const contractTags = Object.keys(contract.elements);
    expect([...RICH_TEXT_FILTER.tags].sort()).toEqual([...contractTags, ...RENDER_ONLY_TAGS].sort());
    expect([...RICH_TEXT_FILTER.renderOnlyTags].sort()).toEqual([...RENDER_ONLY_TAGS].sort());
  });

  it('allows the contract attributes per element, plus loading on img', () => {
    const expected: Record<string, string[]> = {};
    for (const [tag, names] of Object.entries(contract.elements)) expected[tag] = [...names];
    for (const tag of RENDER_ONLY_TAGS) expected[tag] = [];
    expected.img = [...expected.img, 'loading'];
    const actual: Record<string, string[]> = {};
    for (const [tag, names] of Object.entries(RICH_TEXT_FILTER.attributes)) actual[tag] = [...names].sort();
    for (const names of Object.values(expected)) names.sort();
    expect(actual).toEqual(expected);
  });

  it('allows the contract values of the two data attributes and nothing else', () => {
    const actual: Record<string, string[]> = {};
    for (const [name, values] of Object.entries(RICH_TEXT_FILTER.attributeValues)) actual[name] = [...values].sort();
    const expected: Record<string, string[]> = {};
    for (const [name, values] of Object.entries(contract.attributeValues)) expected[name] = [...values].sort();
    expect(actual).toEqual(expected);
  });

  it('allows the contract URL schemes', () => {
    expect([...RICH_TEXT_FILTER.urlSchemes].sort()).toEqual([...contract.urlSchemes].sort());
  });

  it('is frozen', () => {
    expect(Object.isFrozen(RICH_TEXT_FILTER)).toBe(true);
    expect(Object.isFrozen(RICH_TEXT_FILTER.attributes)).toBe(true);
    expect(Object.isFrozen(RICH_TEXT_FILTER.attributes.img)).toBe(true);
  });
});

describe('sanitizeRichText on hostile HTML', () => {
  // The last column is a selector that must find nothing in the output.
  const HOSTILE: Array<[string, string, string]> = [
    ['script', '<p>ok</p><script>alert(1)</script>', 'script'],
    ['style element', '<p>ok</p><style>p{color:red}</style>', 'style'],
    ['style attribute', '<p style="color:red">ok</p>', '[style]'],
    ['onerror', `<p>ok</p><img src="${MEDIA}" onerror="alert(1)">`, '[onerror]'],
    ['onclick', '<p onclick="alert(1)">ok</p>', '[onclick]'],
    ['onload', '<p onload="alert(1)">ok</p>', '[onload]'],
    ['iframe', '<p>ok</p><iframe src="https://example.org"></iframe>', 'iframe'],
    ['object', '<p>ok</p><object data="https://example.org"></object>', 'object'],
    ['embed', '<p>ok</p><embed src="https://example.org">', 'embed'],
    ['svg', '<p>ok</p><svg><circle r="1"></circle></svg>', 'svg'],
    ['math', '<p>ok</p><math><mi>x</mi></math>', 'math'],
    ['form', '<p>ok</p><form action="https://example.org"><p>in</p></form>', 'form'],
    ['input', '<p>ok</p><input name="a" value="b">', 'input'],
    ['template', '<p>ok</p><template><p>in</p></template>', 'template'],
    ['class', '<p class="x">ok</p>', '[class]'],
    ['id', '<p id="x">ok</p>', '[id]'],
    ['name', '<p>ok</p><a name="x" href="https://example.org">x</a>', '[name]'],
    ['unknown data attribute', '<p data-foo="1">ok</p>', '[data-foo]'],
    ['javascript: URL', '<p>ok</p><a href="javascript:alert(1)">x</a>', 'a[href]'],
    ['data: URL', '<p>ok</p><a href="data:text/html,<script>alert(1)</script>">x</a>', 'a[href]'],
    ['vbscript: URL', '<p>ok</p><a href="vbscript:msgbox(1)">x</a>', 'a[href]'],
    ['javascript: URL with a tab', '<p>ok</p><a href="jav&#x09;ascript:alert(1)">x</a>', 'a[href]'],
    ['javascript: URL with leading blanks', '<p>ok</p><a href="  javascript:alert(1)">x</a>', 'a[href]'],
    ['data: URL on an image', '<p>ok</p><img src="data:image/png;base64,AAAA">', 'img'],
  ];

  it.each(HOSTILE)('removes %s', (_name, html, absent) => {
    const out = sanitizeRichText(html);
    expect(parse(out).querySelector(absent)).toBeNull();
    expect(out).toContain('<p>ok</p>');
    expect(out).not.toMatch(/alert\(1\)|msgbox/);
  });

  const MUTATION_XSS = [
    '<svg><p><style><img src=x onerror=alert(1)></style></p></svg>',
    '<math><mtext><table><mglyph><style><img src=x onerror=alert(1)></style></mglyph></table></mtext></math>',
    '<noscript><p title="</noscript><img src=x onerror=alert(1)>"></p></noscript>',
    '<form><math><mtext></form><form><mglyph><style></math><img src onerror=alert(1)>',
    '<a href="jav&#x09;ascript:alert(1)">x</a>',
    '<a href="  javascript:alert(1)">x</a>',
  ];

  it.each(MUTATION_XSS)('leaves nothing active in the reparsed output of %s', (html) => {
    const body = parse(sanitizeRichText(html));
    expect(body.querySelector('script, svg, math, style')).toBeNull();
    for (const element of Array.from(body.querySelectorAll('*'))) {
      const names = element.getAttributeNames();
      expect(names.filter((name) => name.startsWith('on'))).toEqual([]);
      expect(element.getAttribute('href') ?? '').not.toMatch(/^\s*(javascript|data|vbscript):/i);
    }
  });
});

describe('sanitizeRichText on the two data attributes', () => {
  const figureOf = (attrs: string) =>
    `<figure ${attrs}><img src="${MEDIA}" alt="a"><figcaption>c</figcaption></figure>`;
  const imgOf = (attrs: string) => `<p><img src="${MEDIA}" alt="a" ${attrs}></p>`;

  const BAD_VALUES = [
    'data-width="50;background:url(x)"',
    'data-width="33"',
    'data-align="javascript:alert(1)"',
    'data-align="justify"',
    'data-align="CENTER"',
  ];

  it.each(BAD_VALUES)('drops %s from an image and keeps the image', (attr) => {
    const img = parse(sanitizeRichText(imgOf(attr))).querySelector('img');
    expect(img).not.toBeNull();
    expect(img?.getAttributeNames().filter((n) => n.startsWith('data-'))).toEqual([]);
  });

  it.each(BAD_VALUES)('drops %s from a figure and keeps the figure', (attr) => {
    const figure = parse(sanitizeRichText(figureOf(attr))).querySelector('figure');
    expect(figure).not.toBeNull();
    expect(figure?.getAttributeNames().filter((n) => n.startsWith('data-'))).toEqual([]);
    expect(figure?.querySelector('img')).not.toBeNull();
    expect(figure?.querySelector('figcaption')?.textContent).toBe('c');
  });

  it.each(['data-width="50"', 'data-align="center"', 'data-width="100" data-align="left"'])(
    'keeps %s on an image and on a figure',
    (attr) => {
      const names = attr.split(' ').map((pair) => pair.split('=')[0]);
      const img = parse(sanitizeRichText(imgOf(attr))).querySelector('img');
      expect(img?.getAttributeNames()).toEqual(expect.arrayContaining(names));
      const figure = parse(sanitizeRichText(figureOf(attr))).querySelector('figure');
      expect(figure?.getAttributeNames()).toEqual(expect.arrayContaining(names));
    },
  );

  it('removes an attribute of the contract from an element that does not list it', () => {
    const body = parse(
      sanitizeRichText(
        `<p data-width="50" data-align="center" href="https://example.org" target="_blank">p</p>` +
          `<p><img src="${MEDIA}" href="https://example.org" target="_blank" rel="x"></p>` +
          `<table><tbody><tr><td target="_blank" href="https://example.org" src="${MEDIA}">c</td></tr></tbody></table>` +
          `<blockquote data-width="50" rel="x">q</blockquote>` +
          `<a href="https://example.org" src="${MEDIA}" alt="x" data-width="50">a</a>`,
      ),
    );
    for (const element of Array.from(body.querySelectorAll('p, td, blockquote'))) {
      if (element.querySelector('img')) continue;
      expect(element.getAttributeNames()).toEqual([]);
    }
    expect(body.querySelector('img')?.getAttributeNames().sort()).toEqual(['alt', 'loading', 'src']);
    expect(body.querySelector('a')?.getAttributeNames()).toEqual(['href']);
  });

  it('keeps href, target and rel on an anchor', () => {
    const a = parse(
      sanitizeRichText('<a href="https://example.org" target="_blank" rel="noopener noreferrer">x</a>'),
    ).querySelector('a');
    expect(a?.getAttributeNames().sort()).toEqual(['href', 'rel', 'target']);
  });
});

describe('sanitizeRichText on images and figures', () => {
  const FOREIGN = 'https://example.org/pixel.gif';

  it('removes an image outside the media route, also in a figure and in a table cell', () => {
    const body = parse(
      sanitizeRichText(
        `<p>a<img src="${FOREIGN}">b</p>` +
          `<figure><img src="${FOREIGN}"><figcaption>cap</figcaption></figure>` +
          `<table><tbody><tr><td>x<img src="/other/path.png"></td></tr></tbody></table>`,
      ),
    );
    expect(body.querySelector('img')).toBeNull();
    expect(body.querySelector('td')?.textContent).toBe('x');
    expect(body.querySelector('p')?.textContent).toBe('ab');
  });

  it('removes a figure left without an image, with its caption', () => {
    const out = sanitizeRichText(
      `<p>before</p><figure><img src="${FOREIGN}"><figcaption>cap</figcaption></figure>` +
        `<figure><figcaption>only a caption</figcaption></figure><p>after</p>`,
    );
    expect(out).toBe('<p>before</p><p>after</p>');
  });

  it('removes a figure whose only image sits in an inner figure without one of its own', () => {
    const body = parse(
      sanitizeRichText(`<figure><figure><img src="${FOREIGN}"></figure><figcaption>cap</figcaption></figure>`),
    );
    expect(body.querySelector('figure')).toBeNull();
    expect(body.textContent).toBe('');
  });

  it('keeps a figure of the media route with its caption and its two attributes', () => {
    const body = parse(
      sanitizeRichText(
        `<figure data-width="50" data-align="center"><img src="${MEDIA}" alt="Rathaus" data-width="50" data-align="center"><figcaption>Das Rathaus</figcaption></figure>`,
      ),
    );
    const figure = body.querySelector('figure');
    expect(figure?.getAttribute('data-width')).toBe('50');
    expect(figure?.getAttribute('data-align')).toBe('center');
    expect(figure?.querySelector('figcaption')?.textContent).toBe('Das Rathaus');
    const img = figure?.querySelector('img');
    expect(img?.getAttribute('src')).toBe(MEDIA);
    expect(img?.getAttribute('alt')).toBe('Rathaus');
    expect(img?.getAttribute('loading')).toBe('lazy');
    expect(img?.getAttribute('data-width')).toBe('50');
    expect(img?.getAttribute('data-align')).toBe('center');
  });

  it('keeps the loading and empty alt defaults of an image', () => {
    const img = parse(sanitizeRichText(`<p><img src="${MEDIA}" loading="eager"></p>`)).querySelector('img');
    expect(img?.getAttribute('loading')).toBe('lazy');
    expect(img?.getAttribute('alt')).toBe('');
  });

  it('keeps quotes, dividers and tables of the contract', () => {
    const html = '<blockquote><p>q</p></blockquote><hr><table><tbody><tr><th>h</th><td>c</td></tr></tbody></table>';
    expect(sanitizeRichText(html)).toBe(html);
  });

  it('keeps the three render-only tags', () => {
    const html = '<h1>t</h1><pre><code>a\nb</code></pre>';
    expect(sanitizeRichText(html)).toBe(html);
  });
});

describe('wrapTablesForScroll', () => {
  const LABEL = 'Tabelle, seitlich verschiebbar';
  const TABLE = '<table><tbody><tr><td>a</td></tr></tbody></table>';

  it('wraps each table in a keyboard-reachable scroll region with the label', () => {
    const body = parse(wrapTablesForScroll(`<p>x</p>${TABLE}<p>y</p>${TABLE}`, LABEL));
    const wrappers = body.querySelectorAll('div');
    expect(wrappers).toHaveLength(2);
    for (const wrapper of wrappers) {
      expect(wrapper.getAttribute('role')).toBe('region');
      expect(wrapper.getAttribute('tabindex')).toBe('0');
      expect(wrapper.getAttribute('aria-label')).toBe(LABEL);
      expect(wrapper.children).toHaveLength(1);
      expect(wrapper.firstElementChild?.tagName).toBe('TABLE');
    }
  });

  it('escapes the label and adds nothing else', () => {
    const out = wrapTablesForScroll(TABLE, '"><img src=x onerror=alert(1)>');
    const body = parse(out);
    expect(body.querySelector('img')).toBeNull();
    expect(body.querySelector('div')?.getAttributeNames().sort()).toEqual(['aria-label', 'role', 'tabindex']);
    expect(body.querySelector('div')?.getAttribute('aria-label')).toBe('"><img src=x onerror=alert(1)>');
  });

  it('leaves a text without a table byte for byte as it is', () => {
    const html = '<p>kein <strong>Table</strong></p>';
    expect(wrapTablesForScroll(html, LABEL)).toBe(html);
    expect(wrapTablesForScroll('plain', LABEL)).toBe('plain');
  });

  it('keeps the sanitized table content unchanged inside the wrapper', () => {
    const clean = sanitizeRichText('<table><tr><th>K</th></tr><tr><td>v</td></tr></table>');
    const body = parse(wrapTablesForScroll(clean, LABEL));
    expect(body.querySelector('div > table')?.outerHTML).toBe(parse(clean).querySelector('table')?.outerHTML);
  });
});

describe('tables outside the contract', () => {
  it.each(tableCases.cases.map((c) => [c.name, c.input, c.output]))('%s', (_name, input, output) => {
    expect(sanitizeRichText(input)).toBe(output);
  });

  it('keeps the words of a header row in a thead', () => {
    const out = sanitizeRichText('<table><thead><tr><th>Kopf A</th><th>Kopf B</th></tr></thead><tbody><tr><td>x</td><td>y</td></tr></tbody></table>');
    expect(out).toContain('Kopf A');
    expect(out).toContain('Kopf B');
  });
});

describe('sanitizeRichText on a strict image address', () => {
  it('does not render an image with an unclean address and drops the figure around it', () => {
    const body = parse(
      sanitizeRichText(
        '<p>ok</p><figure><img src="/api/orgs/stadt/media/.\t./x.jpg" alt="a"><figcaption>c</figcaption></figure>',
      ),
    );
    expect(body.querySelector('img')).toBeNull();
    expect(body.querySelector('figure')).toBeNull();
  });
});
