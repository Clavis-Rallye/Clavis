import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { axe } from 'vitest-axe';
import { ThemeProvider } from '@mui/material/styles';
import Button from '@mui/material/Button';
import Stack from '@mui/material/Stack';
import theme from '../../theme';

describe('Button a11y', () => {
  it('has no axe violations across primary variants', async () => {
    const { container } = render(
      <ThemeProvider theme={theme}>
        <Stack spacing={1}>
          <Button variant="contained">Continue</Button>
          <Button variant="outlined">Back</Button>
          <Button variant="contained" disabled>
            Disabled
          </Button>
        </Stack>
      </ThemeProvider>,
    );
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
