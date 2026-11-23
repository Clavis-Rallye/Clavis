import { createElement } from 'react';
import { render } from '@testing-library/react';
import { Card, ThemeProvider } from '@mui/material';
import { describe, it, expect } from 'vitest';
import theme from './theme';

type StyleObject = Record<string, unknown>;
const asObject = (v: unknown): StyleObject => v as StyleObject;

// WCAG 2.x relative luminance and contrast ratio (https://www.w3.org/TR/WCAG21/#dfn-contrast-ratio)
const channelLuminance = (byte: number): number => {
  const c = byte / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
};

const relativeLuminance = (hex: string): number => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return (
    0.2126 * channelLuminance(r) + 0.7152 * channelLuminance(g) + 0.0722 * channelLuminance(b)
  );
};

const contrastRatio = (foreground: string, background: string): number => {
  const l1 = relativeLuminance(foreground);
  const l2 = relativeLuminance(background);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
};

describe('MUI Theme', () => {
  it('warning.dark reaches at least 4.5:1 on white', () => {
    expect(contrastRatio(theme.palette.warning.dark, '#FFFFFF')).toBeGreaterThanOrEqual(4.5);
  });

  it('renders error state with 2px error.main border on OutlinedInput', () => {
    const overrides = theme.components?.MuiOutlinedInput?.styleOverrides;
    const root = asObject((overrides as { root?: unknown })?.root);
    const errorRule = asObject(root['&.Mui-error .MuiOutlinedInput-notchedOutline']);
    expect(errorRule.borderColor).toBe(theme.palette.error.main);
    expect(errorRule.borderWidth).toBe(2);
  });

  it('shows a focus-visible ring on Button', () => {
    const overrides = theme.components?.MuiButton?.styleOverrides;
    const root = asObject((overrides as { root?: unknown })?.root);
    const focus = asObject(root['&:focus-visible']);
    expect(typeof focus.outline).toBe('string');
    expect(focus.outline).toMatch(/2px/);
  });

  it('shows a single focus ring on focused OutlinedInput via the notched border', () => {
    const overrides = theme.components?.MuiOutlinedInput?.styleOverrides;
    const root = asObject((overrides as { root?: unknown })?.root);
    const focus = asObject(root['&.Mui-focused .MuiOutlinedInput-notchedOutline']);
    // The focus indicator is the notched outline thickened to a 2px primary
    // border, not a separate `outline`, which would stack a second ring.
    expect(focus.borderColor).toBe(theme.palette.primary.main);
    expect(focus.borderWidth).toBe(2);
    expect(focus.outline).toBeUndefined();
  });

  it('includes a prefers-reduced-motion override in CssBaseline', () => {
    const css = theme.components?.MuiCssBaseline?.styleOverrides;
    expect(typeof css).toBe('string');
    expect(css as string).toMatch(/prefers-reduced-motion: reduce/);
    expect(css as string).toMatch(/transition-duration: 0\.01ms/);
  });

  describe('contrast of form-field tones', () => {
    const hexOf = (v: unknown): string => String(v).toUpperCase();
    const blendOnWhite = (hex: string, opacity: number): string => {
      const mixed = [1, 3, 5].map((i) => {
        const byte = parseInt(hex.slice(i, i + 2), 16);
        return Math.round(byte * opacity + 255 * (1 - opacity))
          .toString(16)
          .padStart(2, '0');
      });
      return `#${mixed.join('')}`.toUpperCase();
    };
    // InputBase puts the `input` slot on <input> and on <textarea> alike, so one rule covers both.
    // Without a rule the library paints the placeholder in the text colour at opacity 0.42.
    it('placeholder of input and textarea is at least 4.5:1 on white', () => {
      const overrides = asObject(theme.components?.MuiInputBase?.styleOverrides ?? {});
      const rule = asObject(asObject(overrides.input ?? {})['&::placeholder'] ?? {});
      const colour = hexOf(rule.color ?? theme.palette.text.primary);
      const opacity = typeof rule.opacity === 'number' ? rule.opacity : 0.42;
      expect(contrastRatio(blendOnWhite(colour, opacity), '#FFFFFF')).toBeGreaterThanOrEqual(4.5);
    });

    it('border of the outlined field at rest is at least 3:1 on white and not darker than hover', () => {
      const root = asObject(
        asObject(theme.components?.MuiOutlinedInput?.styleOverrides).root,
      );
      const rest = hexOf(asObject(root['& .MuiOutlinedInput-notchedOutline']).borderColor);
      const hover = hexOf(asObject(root['&:hover .MuiOutlinedInput-notchedOutline']).borderColor);
      expect(contrastRatio(rest, '#FFFFFF')).toBeGreaterThanOrEqual(3);
      expect(contrastRatio(rest, '#FFFFFF')).toBeLessThanOrEqual(contrastRatio(hover, '#FFFFFF'));
    });

    it('draws the card border in the divider tone', () => {
      const card = asObject(asObject(theme.components?.MuiCard?.styleOverrides).root);
      expect(card.borderColor).toBe(theme.palette.divider);
    });

    it('clips the card instead of hiding its overflow, so a sticky child sticks to the page', () => {
      const card = asObject(asObject(theme.components?.MuiCard?.styleOverrides).root);
      expect(card.overflow).toBe('clip');
      expect(card.display).toBe('flow-root');
    });

    it('computes the overflow of a rendered card as clip', () => {
      const { container } = render(
        createElement(
          ThemeProvider,
          { theme },
          createElement(Card, null, 'content'),
        ),
      );
      const style = window.getComputedStyle(container.firstElementChild as Element);
      expect(style.overflow).toBe('clip');
    });
  });
});
