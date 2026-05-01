import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { ThemeProvider } from '@mui/material/styles';
import Button from '@mui/material/Button';
import theme, { RADIUS_FULL } from '../../theme';

const renderWithTheme = (ui: React.ReactElement) =>
  render(<ThemeProvider theme={theme}>{ui}</ThemeProvider>);

describe('Button (theme)', () => {
  it('contained variant renders with primary background', () => {
    const { getByRole } = renderWithTheme(
      <Button variant="contained">Continue</Button>,
    );
    const btn = getByRole('button');
    expect(btn.className).toMatch(/MuiButton-contained/);
    expect(btn.className).toMatch(/MuiButton-colorPrimary/);
  });

  it('outlined variant renders with outlined class', () => {
    const { getByRole } = renderWithTheme(
      <Button variant="outlined">Back</Button>,
    );
    expect(getByRole('button').className).toMatch(/MuiButton-outlined/);
  });

  it('disabled state is reflected on the DOM', () => {
    const { getByRole } = renderWithTheme(
      <Button variant="contained" disabled>
        Continue
      </Button>,
    );
    const btn = getByRole('button') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.className).toMatch(/Mui-disabled/);
  });

  it('theme exposes pill-radius constant for buttons', () => {
    expect(RADIUS_FULL).toBe(9999);
    const overrides = theme.components?.MuiButton?.styleOverrides as
      | { root?: Record<string, unknown> }
      | undefined;
    expect(overrides?.root?.borderRadius).toBe(RADIUS_FULL);
  });
});
