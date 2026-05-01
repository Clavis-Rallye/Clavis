/**
 * Renders a string as a QR code with `qrcode.react` (ISC), which draws in the
 * browser and makes no network call. The InviteTeam step shows it at 200 × 200
 * px or more. The foreground uses the token `--color-on-surface`, so the code
 * contrasts its surface in the light and the dark theme.
 */
import { QRCodeSVG } from 'qrcode.react';

interface QrCodeDisplayProps {
  /** The string to encode (typically the join URL). */
  value: string;
  /** Square side length in CSS pixels. Defaults to 200. */
  size?: number;
  /** Optional accessible label. Defaults to "QR code". */
  ariaLabel?: string;
}

const QrCodeDisplay: React.FC<QrCodeDisplayProps> = ({
  value,
  size = 200,
  ariaLabel = 'QR code',
}) => {
  return (
    <div
      role="img"
      aria-label={ariaLabel}
      style={{
        background: 'var(--color-neutral-100, #FFFFFF)',
        padding: 'var(--spacing-2, 16px)',
        borderRadius: 'var(--radius-md, 12px)',
        display: 'inline-block',
        lineHeight: 0,
      }}
    >
      {/* qrcode.react gives its SVG role=img. The outer div carries the
          name, so the inner image is hidden: a screen reader announces one
          image, and axe raises no "svg-img-alt" warning. */}
      <span aria-hidden="true">
        <QRCodeSVG
          value={value}
          size={size}
          level="M"
          bgColor="transparent"
          fgColor="var(--color-on-surface, #1D1B20)"
        />
      </span>
    </div>
  );
};

export default QrCodeDisplay;
