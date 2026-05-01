import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ThemeProvider } from '@mui/material/styles';
import theme from '../theme';
import ProgressIndicator from './ProgressIndicator';

const wrap = (ui: React.ReactElement) => (
  <ThemeProvider theme={theme}>{ui}</ThemeProvider>
);

describe('<ProgressIndicator>', () => {
  it('exposes progressbar role with current/total in aria-value*', () => {
    render(wrap(<ProgressIndicator current={2} total={4} />));
    const bar = screen.getByRole('progressbar');
    expect(bar.getAttribute('aria-valuemin')).toBe('0');
    expect(bar.getAttribute('aria-valuemax')).toBe('4');
    expect(bar.getAttribute('aria-valuenow')).toBe('2');
  });

  it('uses the provided aria-label', () => {
    render(
      wrap(
        <ProgressIndicator current={1} total={3} ariaLabel="Step progress" />,
      ),
    );
    expect(screen.getByRole('progressbar').getAttribute('aria-label')).toBe(
      'Step progress',
    );
  });

  it('clamps current below 0 to 0', () => {
    render(wrap(<ProgressIndicator current={-5} total={4} />));
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe(
      '0',
    );
  });

  it('clamps current above total to total', () => {
    render(wrap(<ProgressIndicator current={99} total={4} />));
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe(
      '4',
    );
  });

  it('treats total<1 as 1 to avoid divide-by-zero', () => {
    render(wrap(<ProgressIndicator current={0} total={0} />));
    const bar = screen.getByRole('progressbar');
    expect(bar.getAttribute('aria-valuemax')).toBe('1');
  });
});
