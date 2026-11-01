import React from 'react';
import { patternId } from './printPatternId';

/**
 * Fills for the print of a chart with several series. A black-and-white
 * printer turns the series colours into similar greys. So each series keeps
 * its colour and gets its own line pattern on top: the first stays solid, the
 * others are hatched, dotted or lined. The printed legend shows the same
 * fills, so a reader matches a bar to its series without colour.
 */

const SIZE = 8;

// White lines read on every series colour of the app, which all lie in the
// mid band (OKLCH L 0.43 to 0.77).
const LINE = { stroke: '#FFFFFF', strokeWidth: 2 };

function overlay(index: number): React.ReactNode {
  switch (index % 8) {
    case 0:
      return null;
    case 1:
      return <path d={`M0,${SIZE} L${SIZE},0`} {...LINE} />;
    case 2:
      return <circle cx={SIZE / 2} cy={SIZE / 2} r={1.6} fill="#FFFFFF" />;
    case 3:
      return <path d={`M0,${SIZE / 2} L${SIZE},${SIZE / 2}`} {...LINE} />;
    case 4:
      return <path d={`M0,${SIZE} L${SIZE},0 M0,0 L${SIZE},${SIZE}`} {...LINE} strokeWidth={1.5} />;
    case 5:
      return <path d={`M0,0 L${SIZE},${SIZE}`} {...LINE} />;
    case 6:
      return <path d={`M${SIZE / 2},0 L${SIZE / 2},${SIZE}`} {...LINE} />;
    default:
      return (
        <>
          <circle cx={2} cy={2} r={1.2} fill="#FFFFFF" />
          <circle cx={6} cy={6} r={1.2} fill="#FFFFFF" />
        </>
      );
  }
}

/** The pattern definitions, one per series. Rendered once per chart, invisible. */
export const PrintPatternDefs: React.FC<{ prefix: string; colors: string[] }> = ({ prefix, colors }) => (
  <svg width={0} height={0} style={{ position: 'absolute' }} aria-hidden focusable="false">
    <defs>
      {colors.map((color, index) => (
        <pattern
          key={patternId(prefix, index)}
          id={patternId(prefix, index)}
          width={SIZE}
          height={SIZE}
          patternUnits="userSpaceOnUse"
        >
          <rect width={SIZE} height={SIZE} fill={color} />
          {overlay(index)}
        </pattern>
      ))}
    </defs>
  </svg>
);

/** One legend swatch in the fill of its series. */
export const PrintSwatch: React.FC<{ prefix: string; index: number }> = ({ prefix, index }) => (
  <svg width={18} height={12} aria-hidden focusable="false" style={{ flexShrink: 0 }}>
    <rect width={18} height={12} rx={2} fill={`url(#${patternId(prefix, index)})`} stroke="#000000" strokeWidth={0.5} />
  </svg>
);
