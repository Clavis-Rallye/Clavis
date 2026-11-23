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
