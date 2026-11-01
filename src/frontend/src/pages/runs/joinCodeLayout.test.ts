import { describe, it, expect } from 'vitest';
import { MIN_CODE_PX, presentationLayout } from './joinCodeLayout';

// The windows of the proof: a projector, a laptop in full
// screen and with the browser frame, a small window. Six letters, as every
// join code has.
describe('presentationLayout', () => {
  it.each([
    [1920, 1080],
    [1366, 768],
    [1366, 657],
    [1024, 640],
    [800, 600],
  ])('at %i x %i the QR code takes 60 %% and more, the code 96 px and more, and both fit', (w, h) => {
    const l = presentationLayout(w, h, 6);
    expect(l.qrPx).toBeGreaterThanOrEqual(0.6 * Math.min(w, h));
    expect(l.codePx).toBeGreaterThanOrEqual(MIN_CODE_PX);
    const codeWidth = 6 * l.codePx * 0.64;
    if (l.direction === 'row') {
      expect(2 * l.padPx + l.qrPx + l.padPx + codeWidth).toBeLessThanOrEqual(w);
      expect(l.qrPx).toBeLessThanOrEqual(h);
    } else {
      expect(2 * l.padPx + l.qrPx).toBeLessThanOrEqual(w);
    }
  });

  it('puts the code beside the QR code in landscape and below it in portrait', () => {
    expect(presentationLayout(1920, 1080, 6).direction).toBe('row');
    expect(presentationLayout(390, 844, 6).direction).toBe('column');
  });
});
