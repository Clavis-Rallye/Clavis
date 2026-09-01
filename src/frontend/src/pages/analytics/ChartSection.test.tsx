import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'vitest-axe';
import ChartSection, { PRINT_CHART_WIDTH_PX } from './ChartSection';
import { renderChartPng } from '../../utils/chartImage';
import { downloadBlob } from '../../utils/download';

vi.mock('../../utils/chartImage', () => ({ renderChartPng: vi.fn() }));
vi.mock('../../utils/download', () => ({ downloadBlob: vi.fn() }));

const renderSection = (empty = false) =>
  render(
    <ChartSection
      title="Stationen-Trichter"
      toggleLabel="Datentabelle ein-/ausblenden"
      fileName="run-analytics-run-1-stations.png"
      legend={[{ label: 'Versucht', color: '#2850A9' }]}
      empty={empty}
      emptyMessage="Keine Daten"
      chart={
        <>
          <ul className="MuiChartsLegend-root">
            <li><svg data-testid="legend-swatch-1" width="12" height="12" /></li>
            <li><svg data-testid="legend-swatch-2" width="12" height="12" /></li>
          </ul>
          <div className="MuiChartsSurface-root">
            <svg data-testid="chart-surface" className="MuiChartsSvgLayer-root" width="400" height="300">
              <rect width="400" height="300" />
            </svg>
          </div>
        </>
      }
      table={<table><tbody><tr><td>Wert</td></tr></tbody></table>}
    />,
  );

describe('ChartSection', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(renderChartPng).mockResolvedValue(new Blob(['png'], { type: 'image/png' }));
  });

  it('omits the image action in the empty state', () => {
    renderSection(true);

    expect(screen.queryByRole('button', { name: 'Stationen-Trichter als Bild speichern' })).not.toBeInTheDocument();
  });

  it('exports the chart SVG and excludes preceding legend swatches', async () => {
    renderSection();

    const button = screen.getByRole('button', { name: 'Stationen-Trichter als Bild speichern' });
    await userEvent.click(button);

    const chartSurface = screen.getByTestId('chart-surface');
    expect(chartSurface).toHaveAttribute('width', '400');
    expect(chartSurface).toHaveAttribute('height', '300');
    expect(renderChartPng).toHaveBeenCalledWith(chartSurface, {
      title: 'Stationen-Trichter',
      legend: [{ label: 'Versucht', color: '#2850A9' }],
    });
    expect(renderChartPng).not.toHaveBeenCalledWith(screen.getByTestId('legend-swatch-1'), expect.anything());
    expect(renderChartPng).not.toHaveBeenCalledWith(screen.getByTestId('legend-swatch-2'), expect.anything());
    expect(downloadBlob).toHaveBeenCalledWith(
      'run-analytics-run-1-stations.png',
      expect.any(Blob),
    );
  });

  it('shows the localized export error when rendering fails', async () => {
    vi.mocked(renderChartPng).mockRejectedValueOnce(new Error('canvas failed'));
    renderSection();

    await userEvent.click(screen.getByRole('button', { name: 'Stationen-Trichter als Bild speichern' }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Export fehlgeschlagen.'));
    expect(downloadBlob).not.toHaveBeenCalled();
  });

  it('keeps only the print-only table copy visible for printing when expanded', async () => {
    renderSection();
    await userEvent.click(screen.getByRole('button', { name: 'Datentabelle ein-/ausblenden' }));

    const tables = screen.getAllByRole('table', { hidden: true });
    expect(tables).toHaveLength(2);
    expect(tables[0].closest('.print-hidden')).toBeInTheDocument();
    expect(tables[1].closest('.print-only')).toBeInTheDocument();
  });

  it('keeps a print-only table copy available while the screen table is collapsed', () => {
    const { container } = renderSection();

    const printTable = container.querySelector('.print-only table');
    expect(printTable).toBeInTheDocument();
    expect(printTable?.closest('.print-only')).toHaveClass('print-only');
  });

  it('has no axe accessibility violations', async () => {
    const { container } = renderSection();

    expect(await axe(container)).toHaveNoViolations();
  });

  // A chart drawn at screen width and scaled down prints with tiny labels. While
  // printing, the chart gets the printable width of an A4 page, and afterwards
  // its own width again.
  it('draws the chart at the print width between beforeprint and afterprint', () => {
    const Probe = ({ width }: { width?: number }) => <div data-testid="probe">{width ?? 'auto'}</div>;
    render(
      <ChartSection
        title="Punkteverteilung"
        toggleLabel="Datentabelle ein-/ausblenden"
        chart={<Probe />}
        table={<table><tbody><tr><td>Wert</td></tr></tbody></table>}
      />,
    );
    expect(screen.getByTestId('probe')).toHaveTextContent('auto');

    act(() => {
      window.dispatchEvent(new Event('beforeprint'));
    });
    expect(screen.getByTestId('probe')).toHaveTextContent(String(PRINT_CHART_WIDTH_PX));

    act(() => {
      window.dispatchEvent(new Event('afterprint'));
    });
    expect(screen.getByTestId('probe')).toHaveTextContent('auto');
  });

  // The print report: on a black-and-white printer the series colours turn
  // into similar greys. While printing, each series of a chart with several
  // series gets its own fill pattern, and the printed legend shows the same
  // fills.
  it('prints each series of a multi-series chart in its own pattern, with a matching legend', () => {
    type ProbeProps = { series?: { dataKey: string; color: string }[]; hideLegend?: boolean };
    const Probe = ({ series = [], hideLegend }: ProbeProps) => (
      <div data-testid="probe" data-hide-legend={String(Boolean(hideLegend))}>
        {series.map((s) => s.color).join(' ')}
      </div>
    );
    const { container } = render(
      <ChartSection
        title="Beteiligung"
        toggleLabel="Datentabelle ein-/ausblenden"
        legend={[
          { label: 'Teams', color: '#2850A9' },
          { label: 'Teilnehmende', color: '#3E8E3E' },
        ]}
        chart={
          <Probe
            series={[
              { dataKey: 'teams', color: '#2850A9' },
              { dataKey: 'participants', color: '#3E8E3E' },
            ]}
          />
        }
        table={<table><tbody><tr><td>Wert</td></tr></tbody></table>}
      />,
    );
    // On screen: the plain colours and the library's legend.
    expect(screen.getByTestId('probe')).toHaveTextContent('#2850A9 #3E8E3E');
    expect(screen.getByTestId('probe')).toHaveAttribute('data-hide-legend', 'false');

    act(() => {
      window.dispatchEvent(new Event('beforeprint'));
    });
    const fills = screen.getByTestId('probe').textContent!.split(' ');
    expect(fills).toHaveLength(2);
    expect(fills[0]).toMatch(/^url\(#chart.+-print-fill-0\)$/);
    expect(fills[1]).toMatch(/^url\(#chart.+-print-fill-1\)$/);
    expect(screen.getByTestId('probe')).toHaveAttribute('data-hide-legend', 'true');

    // Each fill is defined, and the second one has a pattern over its colour.
    const id = fills[1].slice('url(#'.length, -1);
    const pattern = container.querySelector(`pattern[id="${id}"]`)!;
    expect(pattern.querySelector('rect')).toHaveAttribute('fill', '#3E8E3E');
    expect(pattern.children.length).toBeGreaterThan(1);
    expect(screen.getByTestId('print-legend')).toHaveTextContent('TeamsTeilnehmende');

    act(() => {
      window.dispatchEvent(new Event('afterprint'));
    });
    expect(screen.getByTestId('probe')).toHaveTextContent('#2850A9 #3E8E3E');
  });

  it('leaves a one-series chart in its colour, because its title names the series', () => {
    const Probe = ({ series = [] }: { series?: { color: string }[] }) => (
      <div data-testid="probe">{series.map((s) => s.color).join(' ')}</div>
    );
    render(
      <ChartSection
        title="Punkteverteilung"
        toggleLabel="Datentabelle ein-/ausblenden"
        legend={[{ label: 'Punkte', color: '#2850A9' }]}
        chart={<Probe series={[{ color: '#2850A9' }]} />}
        table={<table><tbody><tr><td>Wert</td></tr></tbody></table>}
      />,
    );
    act(() => {
      window.dispatchEvent(new Event('beforeprint'));
    });
    expect(screen.getByTestId('probe')).toHaveTextContent('#2850A9');
    expect(screen.queryByTestId('print-legend')).not.toBeInTheDocument();
  });
});
