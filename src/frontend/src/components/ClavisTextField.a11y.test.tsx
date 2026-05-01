import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { axe } from 'vitest-axe';
import { ThemeProvider } from '@mui/material/styles';
import Stack from '@mui/material/Stack';
import theme from '../theme';
import ClavisTextField from './ClavisTextField';

describe('<ClavisTextField> a11y', () => {
  it('has no axe violations across empty / filled / error states', async () => {
    const { container } = render(
      <ThemeProvider theme={theme}>
        <Stack spacing={2}>
          <ClavisTextField label="Empty" value="" onChange={() => {}} />
          <ClavisTextField label="Filled" value="Foxes" onChange={() => {}} />
          <ClavisTextField
            label="WithError"
            value="bad"
            onChange={() => {}}
            error
            helperText="This name can't be used."
          />
        </Stack>
      </ThemeProvider>,
    );
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
