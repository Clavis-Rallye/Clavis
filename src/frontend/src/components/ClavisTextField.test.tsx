import { describe, it, expect, vi } from 'vitest';
import { useState } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { ThemeProvider } from '@mui/material/styles';
import theme from '../theme';
import ClavisTextField from './ClavisTextField';

const wrap = (ui: React.ReactElement) => (
  <ThemeProvider theme={theme}>{ui}</ThemeProvider>
);

describe('<ClavisTextField>', () => {
  it('does not render the clear icon when value is empty', () => {
    render(wrap(<ClavisTextField label="Team Name" value="" onChange={() => {}} />));
    expect(screen.queryByLabelText('Eingabe löschen')).toBeNull();
  });

  it('renders the clear icon when value is non-empty', () => {
    render(
      wrap(<ClavisTextField label="Team Name" value="Foxes" onChange={() => {}} />),
    );
    expect(screen.getByLabelText('Eingabe löschen')).toBeInTheDocument();
  });

  it('calls onClear when the clear icon is clicked', () => {
    const onClear = vi.fn();
    render(
      wrap(
        <ClavisTextField
          label="Team Name"
          value="Foxes"
          onChange={() => {}}
          onClear={onClear}
        />,
      ),
    );
    fireEvent.click(screen.getByLabelText('Eingabe löschen'));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it('falls back to onChange with empty string when onClear is not provided', () => {
    function Harness() {
      const [v, setV] = useState('Foxes');
      return (
        <ClavisTextField
          label="Team Name"
          value={v}
          onChange={(e) => setV(e.target.value)}
        />
      );
    }
    render(wrap(<Harness />));
    expect((screen.getByLabelText('Team Name') as HTMLInputElement).value).toBe(
      'Foxes',
    );
    fireEvent.click(screen.getByLabelText('Eingabe löschen'));
    expect((screen.getByLabelText('Team Name') as HTMLInputElement).value).toBe('');
  });

  it('renders aria-invalid and aria-describedby when error + helperText are set', () => {
    render(
      wrap(
        <ClavisTextField
          label="Team Name"
          value=""
          onChange={() => {}}
          error
          helperText="invalid"
        />,
      ),
    );
    const input = screen.getByLabelText('Team Name');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input.getAttribute('aria-describedby')).toBeTruthy();
  });

  // WCAG 2.1 SC 4.1.3: an error must reach a screen reader without focus.
  it('marks the helper text as a live region while in error state', () => {
    const { rerender } = render(
      wrap(
        <ClavisTextField label="Team Name" value="" onChange={() => {}} helperText=" " />,
      ),
    );
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();

    rerender(
      wrap(
        <ClavisTextField
          label="Team Name"
          value=""
          onChange={() => {}}
          error
          helperText="Invalid credentials"
        />,
      ),
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Invalid credentials');
  });
});
