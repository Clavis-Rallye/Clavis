/**
 * The value axis of a chart that counts things: teams, submissions, points.
 *
 * It starts at zero, steps in whole numbers and writes them in the reader's
 * format. A chart whose values are all zero gets the range 0 to 1: without it
 * the library draws one tick that reads "0.00…".
 */
export function countAxis(label: string, values: readonly (number | null | undefined)[], lang: string) {
  const format = new Intl.NumberFormat(lang, { maximumFractionDigits: 1 });
  const hasValue = values.some((v) => typeof v === 'number' && v > 0);
  return {
    label,
    min: 0,
    ...(hasValue ? {} : { max: 1 }),
    tickMinStep: 1,
    valueFormatter: (value: number | null) => (value == null ? '' : format.format(value)),
  };
}
