import { describe, expect, it } from 'vitest';
import { flattenTablesOutsideContract, TABLE_CELL_SEPARATOR, TABLE_MAX_COLUMNS } from './richTextTables';

const MEDIA = '/api/orgs/stadt/media/content/rathaus.jpg';

function body(html: string): HTMLElement {
  return new DOMParser().parseFromString(html, 'text/html').body;
}

const WIDE_ROW = (first: string) =>
  `<table><tbody><tr><td>${first}</td><td>b</td><td>c</td><td>d</td></tr></tbody></table>`;

describe('flattenTablesOutsideContract', () => {
  it('reads its two constants from the contract file', () => {
    expect(TABLE_MAX_COLUMNS).toBe(3);
    expect(TABLE_CELL_SEPARATOR).toBe(' · ');
  });

  it('puts an image of a cell of a wide table after the paragraph of its row', () => {
    const root = body(WIDE_ROW(`<p>a</p><img src="${MEDIA}" alt="Rathaus">`));
    expect(flattenTablesOutsideContract(root)).toBe(true);
    expect(root.innerHTML).toBe(`<p>a · b · c · d</p><img src="${MEDIA}" alt="Rathaus">`);
  });

  it('moves a figure whole, with its image and caption', () => {
    const figure = `<figure data-align="left"><img src="${MEDIA}" alt=""><figcaption>Unterschrift</figcaption></figure>`;
    const root = body(WIDE_ROW(`<p>a</p>${figure}`));
    flattenTablesOutsideContract(root);
    expect(root.innerHTML).toBe(`<p>a · b · c · d</p>${figure}`);
  });

  it('keeps the images of a row without text', () => {
    const root = body(WIDE_ROW(`<img src="${MEDIA}" alt="">`).replace(/<td>[bcd]<\/td>/g, '<td></td>'));
    flattenTablesOutsideContract(root);
    expect(root.innerHTML).toBe(`<img src="${MEDIA}" alt="">`);
  });

  it('moves an image out of a link and leaves no empty link', () => {
    const root = body(WIDE_ROW(`<a href="https://example.org/" rel="noopener noreferrer"><img src="${MEDIA}" alt="Rathaus"></a>`));
    expect(flattenTablesOutsideContract(root)).toBe(true);
    expect(root.innerHTML).toBe(`<p>b · c · d</p><img src="${MEDIA}" alt="Rathaus">`);
    expect(root.querySelector('a')).toBeNull();
  });

  it('keeps the text of a link and moves its image after the row', () => {
    const link = `<a href="https://example.org/" rel="noopener noreferrer">Seite<img src="${MEDIA}" alt=""></a>`;
    const root = body(WIDE_ROW(link));
    flattenTablesOutsideContract(root);
    expect(root.innerHTML).toBe(
      `<p><a href="https://example.org/" rel="noopener noreferrer">Seite</a> · b · c · d</p><img src="${MEDIA}" alt="">`,
    );
  });

  it('keeps a removed caption with markup as a paragraph before the rows', () => {
    const root = body('<table><caption><strong>Preise</strong> 2026</caption><tr><td>1</td><td>2</td><td>3</td><td>4</td></tr></table>');
    flattenTablesOutsideContract(root);
    expect(root.innerHTML).toBe('<p><strong>Preise</strong> 2026</p><p>1 · 2 · 3 · 4</p>');
  });

  it('puts the images of loose content after the loose paragraphs and before the rows', () => {
    const root = body(`<table><caption>Titel<img src="${MEDIA}" alt=""></caption><tr><td>1</td><td>2</td><td>3</td><td>4</td></tr></table>`);
    flattenTablesOutsideContract(root);
    expect(root.innerHTML).toBe(`<p>Titel</p><img src="${MEDIA}" alt=""><p>1 · 2 · 3 · 4</p>`);
  });

  it('leaves a table of three columns as the same node and returns false', () => {
    const root = body('<table><tbody><tr><td>a</td><td>b</td><td>c</td></tr></tbody></table>');
    const table = root.querySelector('table');
    const before = root.innerHTML;
    expect(flattenTablesOutsideContract(root)).toBe(false);
    expect(root.querySelector('table')).toBe(table);
    expect(root.innerHTML).toBe(before);
  });

  it('returns false for a document without a table', () => {
    const root = body('<p>Text</p>');
    expect(flattenTablesOutsideContract(root)).toBe(false);
    expect(root.innerHTML).toBe('<p>Text</p>');
  });

  it('works in a template, with its own document', () => {
    const template = document.createElement('template');
    template.innerHTML = WIDE_ROW('a');
    expect(flattenTablesOutsideContract(template.content)).toBe(true);
    expect(template.innerHTML).toBe('<p>a · b · c · d</p>');
  });
});
