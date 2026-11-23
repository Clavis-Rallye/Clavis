import contract from './richTextContract.json';

/**
 * The rule for tables outside the contract. A table keeps its shape when it has at most
 * `maxColumns` columns and does not stand inside a cell. Any other table, one that is too wide
 * or sits in a `td` or `th`, becomes paragraphs: first one paragraph for each run of loose content
 * of the table (a removed `caption`, also with markup), then one paragraph for each row. The cells of a row are joined by
 * the cell separator, the blocks of a cell become lines joined by `br`, empty cells are left out,
 * and a row of header cells is bold. Images and figures of a row follow the paragraph of the row.
 * Tables are done from the innermost out. The backend filter follows the same rule on the same
 * fixture (`richTextTableCases.json`), so both sides give the same output.
 *
 * It works on clean HTML, which holds only the elements of the contract.
 *
 * Choices the backend filter follows exactly:
 * - White space is space, tab, newline, carriage return, form feed and U+00A0. A line loses it at
 *   both ends, and loose text is trimmed at both ends the same way.
 * - Loose content is every child node of the table, of its `tbody` or of a `tr` that is not a
 *   `tbody`, `tr`, `td` or `th`. Adjacent loose nodes of one parent are one run. A run gives lines
 *   like a cell does, its lines are joined by `br` into one paragraph, and a run without a line
 *   gives none. Images of loose content follow the loose paragraphs, before the rows.
 * - `img` and `figure` anywhere in a cell or in loose content, also deep inside an inline element,
 *   leave it and join the images of the row. An inline element that lost an image and holds no
 *   text other than white space goes away with it. One with text stays, without the image.
 * - `h1` and `pre` count as blocks, `code` as inline, any other unknown element as a block.
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

const isStructure = (node: Node): boolean => ['tbody', 'tr', 'td', 'th'].includes(nameOf(node));

/**
 * Loose content: the child nodes of the table, of its `tbody` and of a `tr` that are not a
 * `tbody`, `tr`, `td` or `th`, for example a removed `caption`. Adjacent loose nodes of one parent
 * form one run. Runs come in document order.
 */
function looseRuns(parent: Node, runs: Node[][] = []): Node[][] {
  let run: Node[] = [];
  const flush = () => {
    if (run.length > 0) runs.push(run);
    run = [];
  };
  for (const child of Array.from(parent.childNodes)) {
    if (!isStructure(child)) {
      run.push(child);
      continue;
    }
    flush();
    if (nameOf(child) === 'tbody' || nameOf(child) === 'tr') looseRuns(child, runs);
  }
  flush();
  return runs;
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
 * Moves every `img` and `figure` below `parent`, also deep inside inline elements, into `images`
 * in document order. An inline element that lost an image and holds no text other than white
 * space goes away with it, so no empty link is left. Returns whether it moved anything.
 */
function takeImages(parent: Node, images: Node[]): boolean {
  let took = false;
  for (const child of Array.from(parent.childNodes)) {
    const name = nameOf(child);
    if (name === 'img' || name === 'figure') {
      images.push(child);
      took = true;
    } else if (child.nodeType === 1 && takeImages(child, images)) {
      took = true;
      if (isBlank(child.textContent ?? '')) parent.removeChild(child);
    }
  }
  return took;
}

/**
 * The lines of a run of nodes: the content of a cell, or loose content of a table.
 * Images and figures leave into `images`. A block ends the line before and after its own lines,
 * `hr` only ends the line. Unknown elements, `h1` and `pre` count as blocks, `code` as inline.
 */
function linesOf(nodes: Node[], images: Node[]): Node[][] {
  const lines: Node[][] = [];
  let current: Node[] = [];
  const endLine = () => {
    if (current.length > 0) lines.push(current);
    current = [];
  };
  const walk = (list: Node[]) => {
    for (const child of list) {
      if (child.nodeType === 3) {
        current.push(child);
      } else if (child.nodeType === 1) {
        const name = nameOf(child);
        if (name === 'img' || name === 'figure') images.push(child);
        else if (INLINE_TAGS.has(name)) {
          if (takeImages(child, images) && isBlank(child.textContent ?? '')) continue;
          current.push(child);
        } else if (name === 'hr') endLine();
        else {
          endLine();
          walk(Array.from(child.childNodes));
          endLine();
        }
      }
    }
  };
  walk(nodes);
  endLine();
  return lines.filter(hasText).map((line) => {
    trimEdge(line, true);
    trimEdge(line, false);
    return line;
  });
}

/** The lines joined by `br`. */
function joinLines(doc: Document, lines: Node[][]): Node[] {
  const content: Node[] = [];
  lines.forEach((line, index) => {
    if (index > 0) content.push(doc.createElement('br'));
    content.push(...line);
  });
  return content;
}

/** Replaces one table by paragraphs when it is outside the contract. True when it did. */
function flattenTable(table: Element): boolean {
  const doc = table.ownerDocument;
  const rows = ownRows(table);
  const columns = rows.reduce((most, row) => Math.max(most, cellsOf(row).length), 0);
  if (columns <= TABLE_MAX_COLUMNS && !table.parentElement?.closest('td, th')) return false;

  const out: Node[] = [];
  const looseImages: Node[] = [];
  for (const run of looseRuns(table)) {
    const lines = linesOf(run, looseImages);
    if (lines.length === 0) continue;
    const paragraph = doc.createElement('p');
    for (const node of joinLines(doc, lines)) paragraph.appendChild(node);
    out.push(paragraph);
  }
  out.push(...looseImages);
  for (const row of rows) {
    const cells = cellsOf(row);
    const images: Node[] = [];
    const contents: Node[][] = [];
    for (const cell of cells) {
      const lines = linesOf(Array.from(cell.childNodes), images);
      if (lines.length > 0) contents.push(joinLines(doc, lines));
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
