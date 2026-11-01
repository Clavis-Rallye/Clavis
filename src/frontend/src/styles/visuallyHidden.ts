/**
 * Present for assistive tech, invisible to sight.
 *
 * Every length is a string with its unit: MUI reads a bare `width: 1` in sx
 * as 100 %, so a "1 x 1" box built that way measures the whole page.
 */
export const visuallyHiddenSx = {
  position: 'absolute',
  width: '1px',
  height: '1px',
  padding: 0,
  margin: '-1px',
  overflow: 'hidden',
  clip: 'rect(0 0 0 0)',
  whiteSpace: 'nowrap',
  border: 0,
} as const;
