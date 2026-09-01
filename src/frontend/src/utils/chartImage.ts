export interface ChartImageLegendEntry {
  label: string;
  color: string;
}

interface RenderChartPngOptions {
  title: string;
  legend: ChartImageLegendEntry[];
  scale?: number;
}

const STYLE_PROPERTIES = [
  'fill',
  'stroke',
  'stroke-width',
  'font-family',
  'font-size',
  'font-weight',
  'opacity',
] as const;

const TITLE_ROW_HEIGHT = 40;
const TITLE_HORIZONTAL_PADDING = 16;
const LEGEND_ROW_HEIGHT = 32;
const LEGEND_SWATCH_SIZE = 12;
const LEGEND_GAP = 8;
const LEGEND_ITEM_GAP = 20;

function fallbackDimension(svg: SVGSVGElement, attribute: 'width' | 'height'): number {
  const attributeValue = Number.parseFloat(svg.getAttribute(attribute) ?? '');
  if (Number.isFinite(attributeValue) && attributeValue > 0) return attributeValue;

  const viewBox = svg.getAttribute('viewBox')?.trim().split(/[ ,]+/).map(Number);
  const viewBoxValue = viewBox?.[attribute === 'width' ? 2 : 3];
  return Number.isFinite(viewBoxValue) && (viewBoxValue ?? 0) > 0 ? (viewBoxValue ?? 0) : 0;
}

/** Renders a chart SVG, its title and its wrapped legend to a downloadable PNG blob. */
export function renderChartPng(
  svg: SVGSVGElement,
  options: RenderChartPngOptions,
): Promise<Blob> {
  if (!(svg instanceof SVGSVGElement)) {
    throw new TypeError('Chart image source must be an SVGSVGElement.');
  }
  return renderValidChartPng(svg, options);
}

async function renderValidChartPng(
  svg: SVGSVGElement,
  { title, legend, scale = 2 }: RenderChartPngOptions,
): Promise<Blob> {
  if (!Number.isFinite(scale) || scale <= 0) {
    throw new Error('Chart image scale must be greater than zero.');
  }

  const bounds = svg.getBoundingClientRect();
  const chartWidth = bounds.width || fallbackDimension(svg, 'width');
  const chartHeight = bounds.height || fallbackDimension(svg, 'height');
  if (chartWidth <= 0 || chartHeight <= 0) {
    throw new Error('Chart SVG has no rendered dimensions.');
  }

  const clone = svg.cloneNode(true) as SVGSVGElement;
  const sourceElements = [svg, ...svg.querySelectorAll<Element>('*')];
  const clonedElements = [clone, ...clone.querySelectorAll<Element>('*')];

  sourceElements.forEach((source, index) => {
    const computedStyle = getComputedStyle(source);
    const clonedElement = clonedElements[index];
    STYLE_PROPERTIES.forEach((property) => {
      clonedElement.setAttribute(property, computedStyle.getPropertyValue(property));
    });
  });

  clone.setAttribute('width', String(chartWidth));
  clone.setAttribute('height', String(chartHeight));
  const serializedSvg = new XMLSerializer().serializeToString(clone);
  const svgBlob = new Blob([serializedSvg], { type: 'image/svg+xml;charset=utf-8' });
  const svgUrl = URL.createObjectURL(svgBlob);
  const image = new Image();

  try {
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error('Chart SVG could not be loaded.'));
      image.src = svgUrl;
    });
  } finally {
    URL.revokeObjectURL(svgUrl);
  }

  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Canvas 2D context is unavailable.');

  context.font = '700 18px sans-serif';
  const titleWidth = context.measureText(title).width + TITLE_HORIZONTAL_PADDING * 2;
  const logicalWidth = Math.max(chartWidth, titleWidth);

  context.font = '14px sans-serif';
  let legendX = 0;
  let legendRow = 0;
  const legendLayout = legend.map((entry) => {
    const itemWidth = LEGEND_SWATCH_SIZE + LEGEND_GAP
      + context.measureText(entry.label).width + LEGEND_ITEM_GAP;
    if (legendX > 0 && legendX + itemWidth - LEGEND_ITEM_GAP > logicalWidth) {
      legendRow += 1;
      legendX = 0;
    }
    const positionedEntry = { entry, x: legendX, row: legendRow };
    legendX += itemWidth;
    return positionedEntry;
  });
  const legendRows = legend.length > 0 ? legendRow + 1 : 0;
  const legendHeight = legendRows * LEGEND_ROW_HEIGHT;
  const logicalHeight = TITLE_ROW_HEIGHT + chartHeight + legendHeight;

  canvas.width = Math.ceil(logicalWidth * scale);
  canvas.height = Math.ceil(logicalHeight * scale);

  context.scale(scale, scale);
  context.fillStyle = '#ffffff';
  context.fillRect(0, 0, logicalWidth, logicalHeight);

  context.fillStyle = '#000000';
  context.font = '700 18px sans-serif';
  context.textBaseline = 'middle';
  context.fillText(title, TITLE_HORIZONTAL_PADDING, TITLE_ROW_HEIGHT / 2);
  context.drawImage(image, 0, TITLE_ROW_HEIGHT, chartWidth, chartHeight);

  context.font = '14px sans-serif';
  legendLayout.forEach(({ entry, x, row }) => {
    const legendY = TITLE_ROW_HEIGHT + chartHeight + row * LEGEND_ROW_HEIGHT
      + (LEGEND_ROW_HEIGHT - LEGEND_SWATCH_SIZE) / 2;
    context.fillStyle = entry.color;
    context.fillRect(x, legendY, LEGEND_SWATCH_SIZE, LEGEND_SWATCH_SIZE);
    context.fillStyle = '#000000';
    context.fillText(
      entry.label,
      x + LEGEND_SWATCH_SIZE + LEGEND_GAP,
      legendY + LEGEND_SWATCH_SIZE / 2,
    );
  });

  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error('Chart PNG could not be encoded.'));
    }, 'image/png');
  });
}
