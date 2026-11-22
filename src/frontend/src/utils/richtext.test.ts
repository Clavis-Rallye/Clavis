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
