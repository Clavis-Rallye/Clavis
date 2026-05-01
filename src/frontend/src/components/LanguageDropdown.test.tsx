import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { ThemeProvider } from '@mui/material/styles';
import theme from '../theme';
import LanguageDropdown from './LanguageDropdown';

const OPTIONS = [
  { code: 'de' as const, label: 'Deutsch' },
  { code: 'en' as const, label: 'English' },
];

function renderDropdown(value: 'de' | 'en' = 'en', onChange = vi.fn()) {
  return {
    onChange,
    ...render(
      <ThemeProvider theme={theme}>
        <LanguageDropdown
          value={value}
          onChange={onChange}
          options={OPTIONS}
          label="Language"
        />
      </ThemeProvider>,
    ),
  };
}

describe('<LanguageDropdown>', () => {
  it('renders the current value as the visible selection', () => {
    renderDropdown('de');
    // MUI Select renders the selected label as button text; query by combobox.
    expect(screen.getByRole('combobox')).toHaveTextContent('Deutsch');
  });

  it('renders all provided options when opened', () => {
    renderDropdown('en');
    fireEvent.mouseDown(screen.getByRole('combobox'));
    const listbox = screen.getByRole('listbox');
    expect(within(listbox).getByText('Deutsch')).toBeInTheDocument();
    expect(within(listbox).getByText('English')).toBeInTheDocument();
  });

  it('invokes onChange with the new language code on selection', () => {
    const { onChange } = renderDropdown('en');
    fireEvent.mouseDown(screen.getByRole('combobox'));
    fireEvent.click(screen.getByRole('option', { name: 'Deutsch' }));
    expect(onChange).toHaveBeenCalledWith('de');
  });
});
