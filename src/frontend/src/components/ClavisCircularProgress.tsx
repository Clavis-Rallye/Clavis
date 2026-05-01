/**
 * A wrapper around @material/web's `<md-circular-progress>`, the official M3
 * implementation (https://m3.material.io/components/progress-indicators).
 *
 * It sets three defaults: the design's size (44 px), the brand colour #4D69B9
 * and an accessible name, so no spinner is unnamed. Track colour, stroke caps,
 * animation and gap geometry stay with the M3 implementation on purpose.
 */
import { useRef } from 'react';
import { useTranslation } from 'react-i18next';
// Side-effect import: registers <md-circular-progress> as a custom element.
// The import sits here, not in main.tsx, so the @material/web payload only
// enters the bundle when a page imports ClavisCircularProgress.
import '@material/web/progress/circular-progress.js';
import type { MdCircularProgress } from '@material/web/progress/circular-progress.js';

interface ClavisCircularProgressProps {
  /** Diameter in CSS pixels. Default 44, the size of the design. */
  size?: number;
  /** Active arc colour. Default #4D69B9 (Clavis brand). */
  activeColor?: string;
  /** 0–1 (determinate) value. Omit + pass `indeterminate` for a spinner. */
  value?: number;
  /** Spinner mode (M3's native indeterminate animation). */
  indeterminate?: boolean;
  /**
   * Accessible name that says what loads. It falls back to "Loading"
   * (`common:aria.loading`), so a screen reader never announces a bare
   * "progress bar".
   */
  ariaLabel?: string;
}

const ClavisCircularProgress: React.FC<ClavisCircularProgressProps> = ({
  size = 44,
  activeColor = '#4D69B9',
  value,
  indeterminate,
  ariaLabel,
}) => {
  const { t } = useTranslation('common');
  const ref = useRef<MdCircularProgress | null>(null);
  return (
    <md-circular-progress
      ref={ref}
      value={value}
      indeterminate={indeterminate}
      aria-label={ariaLabel ?? t('aria.loading')}
      style={{
        ['--md-circular-progress-size' as string]: `${size}px`,
        ['--md-circular-progress-active-indicator-color' as string]: activeColor,
      } as React.CSSProperties}
    />
  );
};

export default ClavisCircularProgress;
