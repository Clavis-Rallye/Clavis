/**
 * Sizes of the large join code, apart from the component
 * so a test can check them for any window without rendering.
 */

/** Monospace advance plus the letter spacing below, as a share of the font size. */
const CODE_ADVANCE_EM = 0.64;
export const CODE_LETTER_SPACING_EM = 0.04;
/** The smallest size of the code in letters. */
export const MIN_CODE_PX = 96;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

interface PresentationLayout {
  direction: 'row' | 'column';
  qrPx: number;
  codePx: number;
  namePx: number;
  linkPx: number;
  padPx: number;
}

/**
 * Sizes for a window of `width` x `height` CSS pixels. The QR code takes the
 * larger of two layouts: QR beside the code (landscape) or above it
 * (portrait). The header with the name and the close button is measured from
 * the window, not from the DOM, so the numbers are the same in a test.
 *
 * The QR side is at least 60 % of the shorter window side. That
 * holds down to a window of about 800 x 600: below it the view still works,
 * with a smaller QR code.
 */
export function presentationLayout(width: number, height: number, codeLength: number): PresentationLayout {
  const short = Math.min(width, height);
  const padPx = Math.round(clamp(short * 0.03, 12, 40));
  const namePx = Math.round(clamp(short * 0.05, 22, 56));
  const linkPx = Math.round(clamp(short * 0.03, 16, 36));
  const headerPx = Math.max(48, Math.round(namePx * 1.25));
  const bodyW = width - 2 * padPx;
  const bodyH = height - 2 * padPx - headerPx - padPx / 2;
  // The code never grows wider than the body, and is never below 96 px on a
  // window wide enough for six letters at that size.
  const codeFit = bodyW / (Math.max(codeLength, 1) * CODE_ADVANCE_EM);
  const codePx = Math.floor(Math.min(clamp(short * 0.13, MIN_CODE_PX, 220), Math.max(codeFit, 48)));
  const codeW = codeLength * codePx * CODE_ADVANCE_EM;
  const gap = padPx;
  const rowQr = Math.min(bodyH, bodyW - codeW - gap);
  const columnQr = Math.min(bodyW, bodyH - codePx - linkPx * 1.4 - gap);
  const direction = rowQr >= columnQr ? 'row' : 'column';
  const qrPx = Math.max(Math.floor(Math.max(rowQr, columnQr)), 96);
  return { direction, qrPx, codePx, namePx, linkPx, padPx };
}
