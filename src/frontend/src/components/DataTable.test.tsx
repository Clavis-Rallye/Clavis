import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'vitest-axe';
import DataTable from './DataTable';
import type { DataTableColumn, DataTableFilter, DataTableQuery } from './DataTable';
import '../i18n';

interface Row {
  id: string;
  name: string;
  type: string;
}

const rows: Row[] = Array.from({ length: 10 }, (_, i) => ({
  id: String(i),
  name: `Station ${i + 1}`,
  type: i % 2 === 0 ? 'quiz' : 'text',
}));

// No `sortValue` / `searchValue` on purpose: in server mode only `sortKey`
// makes a column sortable and the search box must not depend on a
// column having `searchValue`.
const columns: DataTableColumn<Row>[] = [
  { id: 'name', header: 'Name', render: (r) => r.name, sortKey: 'title' },
  { id: 'type', header: 'Typ', render: (r) => r.type, sortKey: 'type' },
  { id: 'note', header: 'Notiz', render: () => '–' },
];

const typeFilter: DataTableFilter<Row>[] = [
  { id: 'type', label: 'Typ', options: [{ value: 'quiz', label: 'Quiz' }] },
];

const lastQuery = (fn: ReturnType<typeof vi.fn>): DataTableQuery => {
  const calls = fn.mock.calls as DataTableQuery[][];
  return calls[calls.length - 1]![0]!;
};

describe('DataTable server mode', () => {
  let user: ReturnType<typeof userEvent.setup>;
  beforeEach(() => {
    // shouldAdvanceTime keeps userEvent's internal zero-delay timers resolving
    // on a real-time ticker while the debounce stays under fake-timer control.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    user = userEvent.setup({ delay: null });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('emits the initial query once, with the default sort key and page size', () => {
    const onQueryChange = vi.fn();
    render(
      <DataTable
        rows={rows}
        columns={columns}
        getRowId={(r) => r.id}
        ariaLabel="Server-Tabelle"
        filters={typeFilter}
        server={{ total: 100, onQueryChange }}
        defaultSort={{ columnId: 'name', dir: 'desc' }}
        defaultRowsPerPage={10}
      />,
    );
    expect(onQueryChange).toHaveBeenCalledTimes(1);
    expect(onQueryChange).toHaveBeenCalledWith({
      search: '',
      filters: {},
      sort: { key: 'title', dir: 'desc' },
      page: 0,
      rowsPerPage: 10,
    });
  });

  it('emits a single query after the debounce when typing, with page reset to 0', async () => {
    const onQueryChange = vi.fn();
    render(
      <DataTable
        rows={rows}
        columns={columns}
        getRowId={(r) => r.id}
        ariaLabel="Server-Tabelle"
        server={{ total: 100, onQueryChange }}
        defaultRowsPerPage={10}
      />,
    );
    await user.click(screen.getByRole('button', { name: 'Nächste Seite' }));
    expect(lastQuery(onQueryChange).page).toBe(1);

    await user.type(screen.getByRole('textbox', { name: 'Suchen' }), 'quiz');
    // Nothing fires while the debounce window is still open — not even the
    // page reset.
    expect(onQueryChange).toHaveBeenCalledTimes(2);
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(onQueryChange).toHaveBeenCalledTimes(3);
    expect(onQueryChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ search: 'quiz', page: 0, filters: {} }),
    );
  });

  it('emits at once on a sort header click and on a filter change, resetting the page only for the filter', async () => {
    const onQueryChange = vi.fn();
    render(
      <DataTable
        rows={rows}
        columns={columns}
        getRowId={(r) => r.id}
        ariaLabel="Server-Tabelle"
        filters={typeFilter}
        server={{ total: 100, onQueryChange }}
        defaultRowsPerPage={10}
      />,
    );
    await user.click(screen.getByRole('button', { name: 'Nächste Seite' }));
    expect(lastQuery(onQueryChange).page).toBe(1);

    await user.click(screen.getByRole('button', { name: 'Name' }));
    expect(onQueryChange).toHaveBeenCalledTimes(3);
    expect(lastQuery(onQueryChange)).toMatchObject({ sort: { key: 'title', dir: 'asc' }, page: 1 });

    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Typ' }));
    fireEvent.click(screen.getByRole('option', { name: 'Quiz' }));
    expect(onQueryChange).toHaveBeenCalledTimes(4);
    expect(lastQuery(onQueryChange)).toMatchObject({ filters: { type: 'quiz' }, page: 0 });
  });

  it('keeps the search field in the document and focused while loading (rule 5)', async () => {
    const onQueryChange = vi.fn();
    const props = (loading: boolean) => ({
      rows: loading ? [] : rows,
      columns,
      getRowId: (r: Row) => r.id,
      ariaLabel: 'Server-Tabelle',
      filters: typeFilter,
      loading,
      server: { total: 100, onQueryChange },
      defaultRowsPerPage: 10,
    });
    const { rerender } = render(<DataTable {...props(false)} />);
    const search = screen.getByRole('textbox', { name: 'Suchen' });
    await user.type(search, 'quiz');
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(onQueryChange).toHaveBeenCalledTimes(2);

    rerender(<DataTable {...props(true)} />);
    const still = screen.getByRole('textbox', { name: 'Suchen' });
    expect(still).toHaveValue('quiz');
    expect(still).toHaveFocus();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    // A loading re-render with a fresh `server` object must not re-emit.
    expect(onQueryChange).toHaveBeenCalledTimes(2);
  });

  it('shows noMatchMessage only while a search or filter is active, emptyMessage otherwise (rule 7)', async () => {
    const onQueryChange = vi.fn();
    render(
      <DataTable
        rows={[]}
        columns={columns}
        getRowId={(r) => r.id}
        ariaLabel="Server-Tabelle"
        filters={typeFilter}
        server={{ total: 0, onQueryChange }}
        emptyMessage="Keine Stationen"
        noMatchMessage="Nichts gefunden"
      />,
    );
    expect(screen.getByText('Keine Stationen')).toBeInTheDocument();
    expect(screen.queryByText('Nichts gefunden')).not.toBeInTheDocument();

    await user.type(screen.getByRole('textbox', { name: 'Suchen' }), 'x');
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(screen.getByText('Nichts gefunden')).toBeInTheDocument();
    expect(screen.queryByText('Keine Stationen')).not.toBeInTheDocument();

    await user.clear(screen.getByRole('textbox', { name: 'Suchen' }));
    act(() => {
      vi.advanceTimersByTime(300);
    });
    // No search and no filter → back to the plain empty message.
    expect(screen.getByText('Keine Stationen')).toBeInTheDocument();

    // An active filter alone also switches to the no-match message.
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Typ' }));
    fireEvent.click(screen.getByRole('option', { name: 'Quiz' }));
    expect(screen.getByText('Nichts gefunden')).toBeInTheDocument();
  });

  it('announces the result count in a polite live region once loading finishes (rule 8)', () => {
    const onQueryChange = vi.fn();
    const props = (loading: boolean) => ({
      rows: loading ? [] : rows,
      columns,
      getRowId: (r: Row) => r.id,
      ariaLabel: 'Server-Tabelle',
      loading,
      server: { total: 57, onQueryChange },
    });
    const { rerender } = render(<DataTable {...props(true)} />);
    // While loading the body is the spinner (itself a status region) — the
    // result count must not be announced yet.
    expect(screen.queryByText(/Ergebnis/)).not.toBeInTheDocument();

    rerender(<DataTable {...props(false)} />);
    const count = screen.getByText('57 Ergebnisse');
    const status = count.closest('[role="status"]');
    expect(status).not.toBeNull();
    expect(status).toHaveAttribute('aria-live', 'polite');
  });

  it('moves back to the last page and emits when the total shrinks (rule 6)', async () => {
    const onQueryChange = vi.fn();
    const props = (total: number) => ({
      rows,
      columns,
      getRowId: (r: Row) => r.id,
      ariaLabel: 'Server-Tabelle',
      defaultRowsPerPage: 10,
      server: { total, onQueryChange },
    });
    const { rerender } = render(<DataTable {...props(100)} />);
    await user.click(screen.getByRole('button', { name: 'Nächste Seite' }));
    await user.click(screen.getByRole('button', { name: 'Nächste Seite' }));
    expect(lastQuery(onQueryChange).page).toBe(2);

    rerender(<DataTable {...props(15)} />);
    expect(onQueryChange).toHaveBeenCalledTimes(4);
    expect(lastQuery(onQueryChange).page).toBe(1);
    expect(screen.getByText('11–15 von 15')).toBeInTheDocument();
  });

  it('never calls match or sortValue on server rows, even when a column keeps both', async () => {
    const onQueryChange = vi.fn();
    const match = vi.fn(() => true);
    const sortValue = vi.fn((r: Row) => r.name);
    const columnsWithClientCallbacks: DataTableColumn<Row>[] = [
      { id: 'name', header: 'Name', render: (r) => r.name, sortKey: 'title', sortValue },
      { id: 'type', header: 'Typ', render: (r) => r.type, sortKey: 'type' },
    ];
    const filtersWithMatch: DataTableFilter<Row>[] = [
      { id: 'type', label: 'Typ', options: [{ value: 'quiz', label: 'Quiz' }], match },
    ];
    render(
      <DataTable
        rows={rows}
        columns={columnsWithClientCallbacks}
        getRowId={(r) => r.id}
        ariaLabel="Server-Tabelle"
        filters={filtersWithMatch}
        server={{ total: 100, onQueryChange }}
        defaultRowsPerPage={10}
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Name' }));
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Typ' }));
    fireEvent.click(screen.getByRole('option', { name: 'Quiz' }));
    await user.type(screen.getByRole('textbox', { name: 'Suchen' }), 'quiz');
    act(() => {
      vi.advanceTimersByTime(300);
    });

    expect(match).not.toHaveBeenCalled();
    expect(sortValue).not.toHaveBeenCalled();
  });

  // The reload unmounts the table, so without the restore focus falls to the
  // document body. Same props factory for both focus tests below.
  const focusProps = (loading: boolean, onQueryChange: (query: DataTableQuery) => void) => ({
    rows: loading ? [] : rows,
    columns,
    getRowId: (r: Row) => r.id,
    ariaLabel: 'Server-Tabelle',
    loading,
    server: { total: 100, onQueryChange },
    defaultRowsPerPage: 10,
  });

  it('returns focus to the header sort control after a keyboard sort reloaded', async () => {
    const onQueryChange = vi.fn();
    const { rerender } = render(<DataTable {...focusProps(false, onQueryChange)} />);

    screen.getByRole('button', { name: 'Name' }).focus();
    await user.keyboard('{Enter}');
    expect(lastQuery(onQueryChange)).toMatchObject({ sort: { key: 'title', dir: 'asc' } });

    // The mocked reload: spinner while loading, then the rows arrive.
    rerender(<DataTable {...focusProps(true, onQueryChange)} />);
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    rerender(<DataTable {...focusProps(false, onQueryChange)} />);

    const nameSort = screen.getByRole('button', { name: 'Name' });
    expect(document.activeElement).toBe(nameSort);
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Typ' }));
  });

  it('leaves focus alone after a mouse sort reloaded', async () => {
    const onQueryChange = vi.fn();
    const { rerender } = render(<DataTable {...focusProps(false, onQueryChange)} />);

    await user.click(screen.getByRole('button', { name: 'Name' }));
    rerender(<DataTable {...focusProps(true, onQueryChange)} />);
    rerender(<DataTable {...focusProps(false, onQueryChange)} />);

    expect(screen.getByRole('button', { name: 'Name' })).not.toHaveFocus();
  });

  // Below lg the hidden date column's sort lives in a toolbar
  // twin. It sorts by creation date and reverses the order, and because the
  // toolbar stays mounted across the reload it keeps keyboard focus on its own
  // (unlike the header control, which needs the ref restore above).
  it('sorts a column hidden below lg through its toolbar twin and reverses the order', async () => {
    const onQueryChange = vi.fn();
    const hiddenCols: DataTableColumn<Row>[] = [
      { id: 'name', header: 'Name', render: (r) => r.name, sortKey: 'title' },
      { id: 'date', header: 'Datum', render: () => '–', sortKey: 'created_at', hideBelow: 'lg' },
    ];
    const twinProps = (loading: boolean) => ({
      rows: loading ? [] : rows,
      columns: hiddenCols,
      getRowId: (r: Row) => r.id,
      ariaLabel: 'Server-Tabelle',
      loading,
      server: { total: 100, onQueryChange },
      defaultRowsPerPage: 10,
    });
    const { rerender } = render(<DataTable {...twinProps(false)} />);

    const twin = screen.getByRole('button', { name: 'Datum: Aufsteigend sortieren' });
    expect(twin.closest('table')).toBeNull();
    // The header control keeps its plain header name.
    expect(screen.getByRole('button', { name: 'Datum' })).toBeInTheDocument();

    await user.click(twin);
    expect(lastQuery(onQueryChange)).toMatchObject({ sort: { key: 'created_at', dir: 'asc' } });

    // Keyboard activation of the twin reverses the order…
    rerender(<DataTable {...twinProps(true)} />);
    rerender(<DataTable {...twinProps(false)} />);
    const twinDesc = screen.getByRole('button', { name: 'Datum: Absteigend sortieren' });
    twinDesc.focus();
    await user.keyboard('{Enter}');
    expect(lastQuery(onQueryChange)).toMatchObject({ sort: { key: 'created_at', dir: 'desc' } });

    // …and the twin holds focus through the reload it triggered.
    rerender(<DataTable {...twinProps(true)} />);
    rerender(<DataTable {...twinProps(false)} />);
    expect(screen.getByRole('button', { name: 'Datum: Aufsteigend sortieren' })).toHaveFocus();
  });
});

// A table wider than the screen scrolls inside its own frame, and
// that frame must be operable by keyboard (axe: scrollable-region-focusable):
// it takes focus, is a region and carries the table's name.
describe('DataTable scrollable region', () => {
  it('is a focusable, named region that scrolls sideways', async () => {
    // The width forces the table past any phone screen, so the container,
    // not the page, is what has to scroll.
    const wide: DataTableColumn<Row>[] = [
      ...columns,
      { id: 'filler', header: 'Füllspalte', width: 800, render: () => '—' },
    ];
    const { container } = render(
      <DataTable
        rows={rows}
        columns={wide}
        getRowId={(r) => r.id}
        ariaLabel="Breite Tabelle"
      />,
    );

    const region = screen.getByRole('region', { name: 'Breite Tabelle' });
    expect(region).toHaveAttribute('tabindex', '0');
    expect(window.getComputedStyle(region).overflowX).toBe('auto');

    region.focus();
    expect(region).toHaveFocus();

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});

// A sortable column hidden below its breakpoint keeps its sort
// reachable. DataTable renders a toolbar twin that shows exactly below that
// breakpoint (CSS-driven, so the wide layout is untouched). A column that is
// hidden but not sortable gets no twin.
describe('DataTable narrow-screen sort twins', () => {
  it('sorts a hidden sortable column through the toolbar twin (client mode)', async () => {
    const user = userEvent.setup();
    interface DatedRow extends Row {
      date: string;
    }
    const datedRows: DatedRow[] = [
      { id: '1', name: 'Beta', type: 'text', date: '2026-02-01' },
      { id: '2', name: 'Alpha', type: 'text', date: '2026-01-01' },
    ];
    const twinColumns: DataTableColumn<DatedRow>[] = [
      { id: 'name', header: 'Name', render: (r) => r.name, sortValue: (r) => r.name },
      {
        id: 'date',
        header: 'Datum',
        hideBelow: 'lg',
        render: (r) => r.date,
        sortValue: (r) => r.date,
      },
      // Hidden below lg but not sortable → no twin.
      { id: 'note', header: 'Notiz', hideBelow: 'lg', render: () => '–' },
    ];
    const { container } = render(
      <DataTable
        rows={datedRows}
        columns={twinColumns}
        getRowId={(r) => r.id}
        ariaLabel="Schmale Tabelle"
        searchable={false}
      />,
    );

    // The never-hidden "Name" column has only its header control; the hidden
    // sortable "Datum" has a second one outside the table — the twin.
    expect(screen.getAllByRole('button', { name: 'Name' })).toHaveLength(1);
    const twin = screen.getByRole('button', { name: 'Datum: Aufsteigend sortieren' });
    expect(twin.closest('table')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Notiz' })).not.toBeInTheDocument();

    await user.click(twin);
    expect(screen.getAllByRole('row')[1]).toHaveTextContent('Alpha');
    await user.click(screen.getByRole('button', { name: 'Datum: Absteigend sortieren' }));
    expect(screen.getAllByRole('row')[1]).toHaveTextContent('Beta');

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});

describe('DataTable client mode (unchanged)', () => {
  it('still filters, sorts and pages in the browser, with no live region', async () => {
    const user = userEvent.setup();
    const clientRows: Row[] = [
      ...Array.from({ length: 12 }, (_, i) => ({
        id: `b${i}`,
        name: `Bahnhof ${i + 1}`,
        type: 'text',
      })),
      { id: 'q1', name: 'Quiz Eins', type: 'quiz' },
      { id: 'q2', name: 'Quiz Zwei', type: 'quiz' },
      { id: 'q3', name: 'Quiz Drei', type: 'quiz' },
    ];
    const clientColumns: DataTableColumn<Row>[] = [
      {
        id: 'name',
        header: 'Name',
        render: (r) => r.name,
        sortValue: (r) => r.name,
        searchValue: (r) => r.name,
      },
      { id: 'type', header: 'Typ', render: (r) => r.type },
    ];
    render(
      <DataTable
        rows={clientRows}
        columns={clientColumns}
        getRowId={(r) => r.id}
        ariaLabel="Client-Tabelle"
        defaultRowsPerPage={10}
      />,
    );
    expect(screen.getByText('1–10 von 15')).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();

    // Global search filters in the browser.
    await user.type(screen.getByRole('textbox', { name: 'Suchen' }), 'Quiz');
    expect(screen.getByText('1–3 von 3')).toBeInTheDocument();
    expect(screen.getByText('Quiz Zwei')).toBeInTheDocument();
    expect(screen.queryByText('Bahnhof 1')).not.toBeInTheDocument();

    // Clearing restores the full list; the sort header orders it in the browser.
    await user.clear(screen.getByRole('textbox', { name: 'Suchen' }));
    await user.click(screen.getByRole('button', { name: 'Name' }));
    expect(screen.getAllByRole('cell')[0]).toHaveTextContent('Bahnhof 1');
    await user.click(screen.getByRole('button', { name: 'Name' }));
    expect(screen.getAllByRole('cell')[0]).toHaveTextContent('Quiz Zwei');

    // Pagination slices client-side (still in desc order: page 2 = Bahnhof 5–1).
    await user.click(screen.getByRole('button', { name: 'Nächste Seite' }));
    expect(screen.getByText('11–15 von 15')).toBeInTheDocument();
    expect(screen.getByText('Bahnhof 1')).toBeInTheDocument();
    expect(screen.queryByText('Bahnhof 6')).not.toBeInTheDocument();
  });
});
