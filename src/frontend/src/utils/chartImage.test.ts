import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderChartPng } from './chartImage';

describe('renderChartPng', () => {
  const drawImage = vi.fn();
  const fillText = vi.fn();
  const fillRect = vi.fn();
  const scale = vi.fn();
  const toBlob = vi.fn();
  const createObjectURL = vi.fn().mockReturnValue('blob:chart-svg');
  const revokeObjectURL = vi.fn();
  const originalImage = globalThis.Image;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, 'getComputedStyle').mockImplementation(
      (element) =>
        ({
          getPropertyValue: (property: string) =>
            property === 'fill' && element.tagName.toLowerCase() === 'rect' ? 'rgb(40, 80, 169)' : '',
        }) as CSSStyleDeclaration,
    );
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      drawImage,
      fillText,
      fillRect,
      scale,
      measureText: (text: string) => ({ width: text.length * 7 }),
      fillStyle: '',
      font: '',
      textBaseline: '',
    } as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation((callback, type) => {
      toBlob(type);
      callback(new Blob(['png'], { type: 'image/png' }));
    });
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, writable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, writable: true });

    class MockImage {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;

      set src(_value: string) {
        this.onload?.();
      }
    }
    globalThis.Image = MockImage as unknown as typeof Image;
  });

  afterEach(() => {
    globalThis.Image = originalImage;
    vi.restoreAllMocks();
  });

  it('scales the canvas, preserves a long title, wraps the legend and inlines SVG styles', async () => {
    const root = document.createElement('div');
    root.innerHTML = `
      <ul class="MuiChartsLegend-root">
        <li><svg width="12" height="12"></svg></li>
        <li><svg width="12" height="12"></svg></li>
      </ul>
      <div class="MuiChartsSurface-root">
        <svg class="MuiChartsSvgLayer-root" width="400" height="300"><rect /></svg>
      </div>
    `;
    const svg = root.querySelector<SVGSVGElement>('.MuiChartsSvgLayer-root')!;
    vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue({
      width: 400,
      height: 300,
    } as DOMRect);
    const title = 'A long analytics chart title that must remain fully visible';

    const png = await renderChartPng(svg, {
      title,
      legend: [
        { label: 'Attempts', color: '#2850A9' },
        { label: 'Participants', color: '#2E8FA9' },
        { label: 'Submissions', color: '#C0562B' },
        { label: 'Scored', color: '#3E8E3E' },
        { label: 'Unscored', color: '#8A8D91' },
      ],
    });

    const canvas = vi.mocked(HTMLCanvasElement.prototype.toBlob).mock.instances[0] as HTMLCanvasElement;
    const logicalWidth = Math.max(400, title.length * 7 + 32);
    expect(canvas.width).toBe(logicalWidth * 2);
    expect(canvas.height).toBe(808);
    expect(scale).toHaveBeenCalledWith(2, 2);
    expect(fillText).toHaveBeenCalledWith(title, 16, 20);
    expect(fillText).toHaveBeenCalledWith('Attempts', 20, 356);
    expect(fillText).toHaveBeenCalledWith('Unscored', 20, 388);
    expect(drawImage).toHaveBeenCalledWith(expect.any(Object), 0, 40, 400, 300);
    expect(fillRect).toHaveBeenCalledWith(0, 0, logicalWidth, 404);
    expect(fillRect).toHaveBeenCalledWith(0, 350, 12, 12);
    expect(fillRect).toHaveBeenCalledWith(0, 382, 12, 12);
    expect(toBlob).toHaveBeenCalledWith('image/png');
    expect(png.type).toBe('image/png');

    const serializedSvg = await (createObjectURL.mock.calls[0][0] as Blob).text();
    expect(serializedSvg).toContain('fill="rgb(40, 80, 169)"');
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:chart-svg');
  });

  it('throws synchronously before canvas work when the source is not an SVG element', () => {
    const div = document.createElement('div');

    expect(() => renderChartPng(div as unknown as SVGSVGElement, {
      title: 'Invalid chart',
      legend: [],
    })).toThrow('Chart image source must be an SVGSVGElement.');
    expect(HTMLCanvasElement.prototype.getContext).not.toHaveBeenCalled();
    expect(createObjectURL).not.toHaveBeenCalled();
  });
});
