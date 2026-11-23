import contract from './richTextContract.json';

/**
 * The rule for tables outside the contract. A table keeps its shape when it has at most
 * `maxColumns` columns and does not stand inside a cell. Any other table, one that is too wide
 * or sits in a `td` or `th`, becomes paragraphs: first one paragraph for each loose text of the
 * table (a removed `caption`), then one paragraph for each row. The cells of a row are joined by
 * the cell separator, the blocks of a cell become lines joined by `br`, empty cells are left out,
 * and a row of header cells is bold. Images and figures of a row follow the paragraph of the row.
 * Tables are done from the innermost out. The backend filter follows the same rule on the same
 * fixture (`richTextTableCases.json`), so both sides give the same output.
 *
 * It works on clean HTML, which holds only the elements of the contract.
 */

export const TABLE_MAX_COLUMNS: number = contract.tables.maxColumns;
export const TABLE_CELL_SEPARATOR: string = contract.tables.cellSeparator;

const LEADING_SPACE = /^[ \t\n\r\f\u00a0]+/;
const TRAILING_SPACE = /[ \t\n\r\f\u00a0]+$/;
const isBlank = (text: string): boolean => text.replace(LEADING_SPACE, '') === '';

/** Elements that stay in the current line of a cell, whole and with everything inside. */
const INLINE_TAGS = new Set(['strong', 'em', 'u', 's', 'a', 'br', 'code']);

const nameOf = (node: Node): string => (node.nodeType === 1 ? node.nodeName.toLowerCase() : '');

const isCell = (node: Node): node is Element => nameOf(node) === 'td' || nameOf(node) === 'th';

const cellsOf = (row: Element): Element[] => Array.from(row.children).filter(isCell);

/** The `tr` children of the table and of its `tbody`, in document order. */
function ownRows(table: Element): Element[] {
  const rows: Element[] = [];
  for (const child of Array.from(table.children)) {
    const name = nameOf(child);
    if (name === 'tr') rows.push(child);
    else if (name === 'tbody') {
      for (const row of Array.from(child.children)) if (nameOf(row) === 'tr') rows.push(row);
    }
  }
  return rows;
}

/** Text outside every cell, in document order, that holds more than white space. */
function looseTexts(parent: Node, found: string[] = []): string[] {
  for (const child of Array.from(parent.childNodes)) {
    if (child.nodeType === 3) {
      const text = (child as Text).data;
      if (!isBlank(text)) found.push(text.replace(LEADING_SPACE, '').replace(TRAILING_SPACE, ''));
    } else if (nameOf(child) === 'tbody' || nameOf(child) === 'tr') {
      looseTexts(child, found);
    }
  }
  return found;
}

/** Removes white space and `br` from one edge of a run of nodes, going into elements at the edge. */
function trimEdge(nodes: Node[], atStart: boolean): void {
  while (nodes.length > 0) {
    const node = atStart ? nodes[0] : nodes[nodes.length - 1];
    if (node.nodeType === 3) {
      const text = node as Text;
      text.data = text.data.replace(atStart ? LEADING_SPACE : TRAILING_SPACE, '');
      if (text.data !== '') return;
    } else if (nameOf(node) !== 'br') {
      trimEdge(Array.from(node.childNodes), atStart);
      if (node.childNodes.length > 0) return;
    }
    node.parentNode?.removeChild(node);
    if (atStart) nodes.shift();
    else nodes.pop();
  }
}

const hasText = (nodes: Node[]): boolean => nodes.some((node) => !isBlank(node.textContent ?? ''));

/**
 * The lines of one cell. Images and figures leave the cell into `images`.
 * A block ends the line before and after its own lines, `hr` only ends the line.
 */
function linesOfCell(cell: Element, images: Node[]): Node[][] {
  const lines: Node[][] = [];
  let current: Node[] = [];
  const endLine = () => {
    if (current.length > 0) lines.push(current);
    current = [];
  };
  const walk = (parent: Node) => {
    for (const child of Array.from(parent.childNodes)) {
      if (child.nodeType === 3) {
        current.push(child);
      } else if (child.nodeType === 1) {
        const name = nameOf(child);
        if (INLINE_TAGS.has(name)) current.push(child);
        else if (name === 'hr') endLine();
        else if (name === 'img' || name === 'figure') images.push(child);
        else {
          endLine();
          walk(child);
          endLine();
        }
      }
    }
  };
  walk(cell);
  endLine();
  return lines.filter(hasText).map((line) => {
    trimEdge(line, true);
    trimEdge(line, false);
    return line;
  });
}

/** Replaces one table by paragraphs when it is outside the contract. True when it did. */
function flattenTable(table: Element): boolean {
  const doc = table.ownerDocument;
  const rows = ownRows(table);
  const columns = rows.reduce((most, row) => Math.max(most, cellsOf(row).length), 0);
  if (columns <= TABLE_MAX_COLUMNS && !table.parentElement?.closest('td, th')) return false;

  const out: Node[] = [];
  for (const text of looseTexts(table)) {
    const paragraph = doc.createElement('p');
    paragraph.appendChild(doc.createTextNode(text));
    out.push(paragraph);
  }
  for (const row of rows) {
    const cells = cellsOf(row);
    const images: Node[] = [];
    const contents: Node[][] = [];
    for (const cell of cells) {
      const lines = linesOfCell(cell, images);
      if (lines.length === 0) continue;
      const content: Node[] = [];
      lines.forEach((line, index) => {
        if (index > 0) content.push(doc.createElement('br'));
        content.push(...line);
      });
      contents.push(content);
    }
    if (contents.length > 0) {
      const paragraph = doc.createElement('p');
      const holder = cells.every((cell) => nameOf(cell) === 'th')
        ? paragraph.appendChild(doc.createElement('strong'))
        : paragraph;
      contents.forEach((content, index) => {
        if (index > 0) holder.appendChild(doc.createTextNode(TABLE_CELL_SEPARATOR));
        for (const node of content) holder.appendChild(node);
      });
      out.push(paragraph);
    }
    out.push(...images);
  }
  table.replaceWith(...out);
  return true;
}

/**
 * Applies the rule to every table below `root`, in place, the innermost first.
 * Returns whether it changed anything. New nodes come from the document of the node,
 * so it works in a parsed document and in a `template`.
 */
export function flattenTablesOutsideContract(root: ParentNode): boolean {
  let changed = false;
  // Document order puts a table before the tables inside it, so the reverse order is innermost first.
  for (const table of Array.from(root.querySelectorAll('table')).reverse()) {
    if (flattenTable(table)) changed = true;
  }
  return changed;
}
