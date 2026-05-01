import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import { axe } from 'vitest-axe';
import { ThemeProvider } from '@mui/material/styles';
import Typography from '@mui/material/Typography';
import theme from '../theme';
import LanguageDropdown from './LanguageDropdown';

describe('<LanguageDropdown> a11y', () => {
  it('has no axe violations with label + describedby helper', async () => {
    const { container } = render(
      <ThemeProvider theme={theme}>
        <LanguageDropdown
          value="en"
          onChange={vi.fn()}
          label="Language"
          describedById="lang-helper"
          options={[
            { code: 'de', label: 'Deutsch' },
            { code: 'en', label: 'English' },
          ]}
        />
        <Typography id="lang-helper" variant="body2">
          You can change this later.
        </Typography>
      </ThemeProvider>,
    );

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
