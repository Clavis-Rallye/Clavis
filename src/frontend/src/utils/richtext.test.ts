import { describe, expect, it } from 'vitest';
import {
  excerpt,
  htmlToPlainText,
  isHtml,
  isOwnMediaSrc,
  isRichTextEmpty,
  plainTextToHtml,
  toEditorHtml,
} from './richtext';
import tableCases from './richTextTableCases.json';

describe('richtext utils', () => {
  it('detects markup', () => {
    expect(isHtml('<p>Hallo</p>')).toBe(true);
    expect(isHtml('Hallo < Welt')).toBe(false);
    expect(isHtml('')).toBe(false);
    expect(isHtml(null)).toBe(false);
  });

  it('converts legacy plain text to paragraphs with line breaks', () => {
    expect(plainTextToHtml('Erste Zeile\nZweite Zeile\n\nNeuer Absatz & <b>')).toBe(
      '<p>Erste Zeile<br>Zweite Zeile</p><p>Neuer Absatz &amp; &lt;b&gt;</p>',
    );
    expect(plainTextToHtml('   ')).toBe('');
    expect(toEditorHtml('<p>bleibt</p>')).toBe('<p>bleibt</p>');
    expect(toEditorHtml('wird')).toBe('<p>wird</p>');
  });

  it('turns documents into plain text with block boundaries as spaces', () => {
    expect(htmlToPlainText('<h2>Rathaus</h2><p>Der <strong>Perlach</strong><br>daneben.</p><ul><li>eins</li><li>zwei</li></ul>')).toBe(
      'Rathaus Der Perlach daneben. eins zwei',
    );
    expect(htmlToPlainText('Plain\ntext')).toBe('Plain\ntext');
  });

  it('builds a markup-free excerpt that ends on a word', () => {
    const long = `<p>${'Wort '.repeat(60).trim()}</p>`;
    const out = excerpt(long, 50);
    expect(out.endsWith(' …')).toBe(true);
    expect(out.length).toBeLessThanOrEqual(52);
    expect(out).not.toContain('<');
    expect(excerpt('<p>kurz</p>')).toBe('kurz');
  });

  it('treats an image-only document as filled and a whitespace document as empty', () => {
    expect(isRichTextEmpty('<p></p>')).toBe(true);
    expect(isRichTextEmpty('<p> <br> </p>')).toBe(true);
    expect(isRichTextEmpty('<p><img src="/api/orgs/a/media/a/media/x.png" alt="x"></p>')).toBe(false);
    expect(isRichTextEmpty('<p>Text</p>')).toBe(false);
    expect(isRichTextEmpty('')).toBe(true);
  });

  it('accepts only the organisation media route as image source', () => {
    expect(isOwnMediaSrc('/api/orgs/stadt/media/1234/media/abc_bild.png')).toBe(true);
    expect(isOwnMediaSrc('https://tracker.example/pixel.gif')).toBe(false);
    expect(isOwnMediaSrc('/api/orgs/stadt/media/../../secret.png')).toBe(false);
    expect(isOwnMediaSrc('/api/files/1234/runs/x.png')).toBe(false);
    expect(isOwnMediaSrc('')).toBe(false);
  });

  describe('toEditorHtml on old legal texts', () => {
    // The same pair as in test_richtext.py (sanitize_html).
    const LEGACY_LEGAL_DOCUMENT =
      '<h1>Impressum</h1><pre><code>Musterstraße 1\n86150 Augsburg\n\nTelefon 0821 0000</code></pre><p>Ein Aufruf wie <code>ls -l</code> im Text.</p><blockquote><p>Ein Zitat.</p></blockquote>';
    const LEGACY_LEGAL_MAPPED =
      '<h2>Impressum</h2><p>Musterstraße 1<br>86150 Augsburg</p><p>Telefon 0821 0000</p><p>Ein Aufruf wie ls -l im Text.</p><blockquote><p>Ein Zitat.</p></blockquote>';

    it('maps h1, pre and code to what the editor has', () => {
      expect(toEditorHtml(LEGACY_LEGAL_DOCUMENT)).toBe(LEGACY_LEGAL_MAPPED);
    });

    it('keeps the inline markup of an h1', () => {
      expect(toEditorHtml('<h1>Neu <em>hier</em></h1>')).toBe('<h2>Neu <em>hier</em></h2>');
    });

    it('keeps only the text of a pre with markup inside', () => {
      expect(toEditorHtml('<pre>Eins <strong>zwei</strong>\ndrei</pre>')).toBe('<p>Eins zwei<br>drei</p>');
    });

    it('keeps < and & inside a pre as escaped text', () => {
      expect(toEditorHtml('<pre>a &lt; b &amp;&amp; c &gt; d</pre>')).toBe(
        '<p>a &lt; b &amp;&amp; c &gt; d</p>',
      );
    });

    it('starts a new paragraph at a blank line with spaces and reads CRLF', () => {
      expect(toEditorHtml('<pre>Eins\n  \nZwei\r\nDrei</pre>')).toBe('<p>Eins</p><p>Zwei<br>Drei</p>');
    });

    it('leaves no block for an empty pre', () => {
      expect(toEditorHtml('<p>Davor</p><pre>\n</pre><p>Danach</p>')).toBe('<p>Davor</p><p>Danach</p>');
    });

    it('turns code into plain text', () => {
      expect(toEditorHtml('<p>Nutze <code>ls -l</code> hier.</p>')).toBe('<p>Nutze ls -l hier.</p>');
    });

    it('passes a document without the three tags unchanged', () => {
      const document = '<h2>Titel</h2><p>Text<br>Zeile</p><blockquote><p>Zitat</p></blockquote>';
      expect(toEditorHtml(document)).toBe(document);
    });
  });
});

describe('toEditorHtml with tables', () => {
  const wide = tableCases.cases.find((c) => c.name === 'six_columns_walk_sample')!;

  it('turns a table of six columns into the paragraphs of its rows', () => {
    expect(toEditorHtml(wide.input)).toBe(wide.output);
  });

  it('turns a table inside a cell into a paragraph and keeps the outer table', () => {
    const nested = tableCases.cases.find((c) => c.name === 'nested_table_in_a_small_table')!;
    expect(toEditorHtml(nested.input)).toBe(nested.output);
  });

  it('gives a text with a table of three columns back byte for byte', () => {
    const html = '<p>vor</p><table><tbody><tr><td><p>a</p></td><td><p>b</p></td><td><p>c</p></td></tr></tbody></table>\n<p>nach</p>';
    expect(toEditorHtml(html)).toBe(html);
  });

  it('gives a text without a table back byte for byte', () => {
    const html = '<p>kein  Table</p>\n<ul><li>x</li></ul>';
    expect(toEditorHtml(html)).toBe(html);
  });
});

describe('richtext helpers run in linear time', () => {
  const SHAPES: Record<string, (k: number) => string> = {
    'shape 1': (k) => '<p>x</p>' + '<pre '.repeat(k),
    'shape 2': (k) => '<code '.repeat(k),
    'shape 3': (k) => '<pre>' + '<br '.repeat(k),
    'shape 4': (k) => '<a'.repeat(k),
    'shape 5': (k) => '<h1 '.repeat(k),
  };

  // A wall-time limit measures the runner, a ratio measures the growth.
  const bestOf = (runs: number, fn: () => unknown): number => {
    let best = Infinity;
    for (let i = 0; i < runs; i += 1) {
      const start = performance.now();
      fn();
      best = Math.min(best, performance.now() - start);
    }
    return best;
  };

  // The old sizes (400 000 and 2 000 000 characters) sit in a range where this V8 replaces
  // single characters in dense strings slower than linearly, so a 4n input there measures the
  // engine, not the filter. Sizes from about eight million characters on grow linearly again,
  // and the larger inputs keep the smaller ones clearly above two milliseconds.
  const SIZES: Record<string, number> = {
    'shape 1': 102_400_000,
    'shape 2': 12_800_000,
    'shape 3': 102_400_000,
    'shape 4': 12_800_000,
    'shape 5': 12_800_000,
  };

  for (const [name, build] of Object.entries(SHAPES)) {
    const size = SIZES[name];
    it(`grows isHtml and toEditorHtml on ${name} by less than a factor of ten when the input grows by four`, () => {
      const textOf = (n: number) => build(Math.ceil(n / build(1).length)).slice(0, n);
    const textSmall = textOf(size / 4);
    const textLarge = textOf(size);
    const small = bestOf(3, () => isHtml(textSmall));
    const large = bestOf(3, () => isHtml(textLarge));
    expect(large / Math.max(small, 1)).toBeLessThan(10);
    expect(large).toBeLessThan(10_000);
    const smallEditor = bestOf(3, () => toEditorHtml(textSmall));
    const largeEditor = bestOf(3, () => toEditorHtml(textLarge));
      expect(largeEditor / Math.max(smallEditor, 1)).toBeLessThan(10);
      expect(largeEditor).toBeLessThan(10_000);
    });
  }

  it('keeps the meaning of isHtml', () => {
    expect(isHtml('a < b > c')).toBe(false);
    expect(isHtml('<a')).toBe(false);
    expect(isHtml('x <B> y')).toBe(true);
    expect(isHtml('<1> <a')).toBe(false);
    expect(isHtml('< a>')).toBe(false);
  });
});

describe('isOwnMediaSrc strict form', () => {
  const ROUTE = '/api/orgs/demo/media/';

  it('accepts a clean address of the media route', () => {
    expect(isOwnMediaSrc(`${ROUTE}content/x.jpg`)).toBe(true);
  });

  it.each([
    ['address 1', `${ROUTE}.\t./x.jpg`],
    ['address 2', `${ROUTE}a\tb.jpg`],
    ['address 3', `${ROUTE}a\nb.jpg`],
    ['address 4', `${ROUTE}a\rb.jpg`],
    ['address 5', `${ROUTE}a\u00a0b.jpg`],
    ['address 6', `${ROUTE}a b.jpg`],
    ['address 7', `${ROUTE}a\u007fb.jpg`],
    ['address 8', `${ROUTE}./x.jpg`],
    ['address 9', `${ROUTE}../x.jpg`],
    ['address 10', `${ROUTE}a/../x.jpg`],
    ['address 11', `${ROUTE}%2e%2e/x.jpg`],
    ['address 12', `${ROUTE}%2E/x.jpg`],
    ['address 13', `${ROUTE}a%2fb.jpg`],
    ['address 14', `${ROUTE}a%5Cb.jpg`],
    ['address 15', `${ROUTE}a\\b.jpg`],
  ])('refuses an unclean address (%s)', (_name, src) => {
    expect(isOwnMediaSrc(src)).toBe(false);
  });
});
