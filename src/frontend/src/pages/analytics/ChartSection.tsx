import React, { cloneElement, isValidElement, useEffect, useId, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Box, Card, CardContent, Collapse, IconButton, Stack, Tooltip, Typography } from '@mui/material';
import ImageOutlinedIcon from '@mui/icons-material/ImageOutlined';
import TableChartOutlinedIcon from '@mui/icons-material/TableChartOutlined';
import ErrorAlert from '../../components/ErrorAlert';
import { downloadBlob } from '../../utils/download';
import { renderChartPng } from '../../utils/chartImage';
import type { ChartImageLegendEntry } from '../../utils/chartImage';
import { PrintPatternDefs, PrintSwatch } from './printPatterns';
import { patternId } from './printPatternId';

// The printable width of an A4 page with 15 mm margins (180 mm at 96 dpi),
// less the card's padding. A chart drawn at screen width and then scaled down
// prints with tick labels of about 4 pt and empty space below it. Drawn at
// this width, it prints at its real size.
export const PRINT_CHART_WIDTH_PX = 640;

/**
 * True between the browser's beforeprint and afterprint events. The state
 * change is flushed at once, so the print layout already holds the chart at
 * its print width.
 */
function usePrinting(): boolean {
  const [printing, setPrinting] = useState(false);
  useEffect(() => {
    const before = () => flushSync(() => setPrinting(true));
    const after = () => setPrinting(false);
    window.addEventListener('beforeprint', before);
    window.addEventListener('afterprint', after);
    return () => {
      window.removeEventListener('beforeprint', before);
      window.removeEventListener('afterprint', after);
    };
  }, []);
  return printing;
}

interface ChartSectionProps {
  title: string;
  /** The MUI X Chart element. Omitted (with `empty`) renders only the empty message. */
  chart?: React.ReactNode;
  /** The accessible data table carrying the same values as the chart. */
  table: React.ReactNode;
  /** Label for the table-toggle icon button, e.g. "Show data table for …". */
  toggleLabel: string;
  fileName?: string;
  legend?: ChartImageLegendEntry[];
  empty?: boolean;
  emptyMessage?: string;
}

/**
 * Pairs a chart with a toggleable, visible data table of the same values,
 * because every chart needs a keyboard and screen-reader accessible equivalent.
 * The table is collapsed by default. Toggling it never re-fetches or resets
 * chart state, so a plain local boolean is safe.
 */
const ChartSection: React.FC<ChartSectionProps> = ({
  title,
  chart,
  table,
  toggleLabel,
  fileName,
  legend = [],
  empty,
  emptyMessage,
}) => {
  const { t } = useTranslation('runs');
  const [tableOpen, setTableOpen] = useState(false);
  const [imageError, setImageError] = useState<string | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const imageLabel = t('analytics.export.image', { title });
  const printing = usePrinting();
  // A chart with several series prints each series in its own fill: its
  // colour with a line pattern, so the print reads in black and white too.
  // The library's legend gives way to a printed legend with the same fills.
  const patternPrefix = `chart${useId().replace(/[^a-zA-Z0-9]/g, '')}`;
  const seriesOf = isValidElement<{ series?: Record<string, unknown>[] }>(chart) ? chart.props.series : undefined;
  const patterned = legend.length >= 2 && Array.isArray(seriesOf) && seriesOf.length === legend.length;
  let chartNode = chart;
  if (printing && isValidElement<{ width?: number; series?: Record<string, unknown>[]; hideLegend?: boolean }>(chart)) {
    chartNode = cloneElement(chart, {
      width: PRINT_CHART_WIDTH_PX,
      ...(patterned
        ? {
            series: seriesOf!.map((series, index) => ({
              ...series,
              color: `url(#${patternId(patternPrefix, index)})`,
            })),
            hideLegend: true,
          }
        : {}),
    });
  }

  const handleImageExport = async () => {
    setImageError(null);
    const candidates = Array.from(
      chartRef.current?.querySelectorAll<SVGSVGElement>('svg') ?? [],
    ).filter((svg) => !svg.closest('.MuiChartsLegend-root'));
    let svg = candidates.at(-1) ?? null;
    let largestArea = 0;
    candidates.forEach((candidate) => {
      const bounds = candidate.getBoundingClientRect();
      const area = bounds.width * bounds.height;
      if (area > largestArea) {
        svg = candidate;
        largestArea = area;
      }
    });
    if (!svg || !fileName) {
      setImageError(t('analytics.export.error'));
      return;
    }
    try {
      const blob = await renderChartPng(svg, { title, legend });
      downloadBlob(fileName, blob);
    } catch {
      setImageError(t('analytics.export.error'));
    }
  };

  return (
    <Card className="print-card">
      <CardContent>
        {patterned && <PrintPatternDefs prefix={patternPrefix} colors={legend.map((entry) => entry.color)} />}
        <Stack className="print-keep-with-next" direction="row" spacing={1} sx={{ alignItems: 'center', mb: 1 }}>
          <Typography variant="h6" component="h2" sx={{ flexGrow: 1 }}>
            {title}
          </Typography>
          {!empty && chart && fileName && (
            <Tooltip title={imageLabel}>
              <IconButton
                className="print-hidden"
                size="small"
                aria-label={imageLabel}
                onClick={() => void handleImageExport()}
              >
                <ImageOutlinedIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
          <Tooltip title={toggleLabel}>
            <IconButton
              className="print-hidden"
              size="small"
              aria-label={toggleLabel}
              aria-expanded={tableOpen}
              onClick={() => setTableOpen((v) => !v)}
            >
              <TableChartOutlinedIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Stack>

        {empty ? (
          <Typography color="text.secondary" variant="body2">
            {emptyMessage}
          </Typography>
        ) : (
          <Box ref={chartRef} role="img" aria-label={title} className="print-keep">
            {chartNode}
          </Box>
        )}

        {patterned && !empty && (
          <Box
            className="print-only print-keep"
            style={{ display: 'none' }}
            data-testid="print-legend"
            sx={{ mt: 1 }}
          >
            <Stack direction="row" sx={{ flexWrap: 'wrap', columnGap: 2, rowGap: 0.5 }}>
              {legend.map((entry, index) => (
                <Stack key={entry.label} direction="row" spacing={0.75} sx={{ alignItems: 'center' }}>
                  <PrintSwatch prefix={patternPrefix} index={index} />
                  <Typography variant="caption">{entry.label}</Typography>
                </Stack>
              ))}
            </Stack>
          </Box>
        )}

        {imageError && (
          <Box sx={{ mt: 2 }}>
            <ErrorAlert message={imageError} />
          </Box>
        )}

        <Collapse className="print-hidden" in={tableOpen} timeout="auto" unmountOnExit>
          <Box sx={{ mt: 2 }}>{table}</Box>
        </Collapse>
        <Box className="print-only" style={{ display: 'none' }} sx={{ mt: 2 }}>
          {table}
        </Box>
      </CardContent>
    </Card>
  );
};

export default ChartSection;
