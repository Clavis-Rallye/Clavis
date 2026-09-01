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
});
