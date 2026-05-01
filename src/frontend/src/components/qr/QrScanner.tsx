/**
 * Reads a QR code from the camera with `html5-qrcode` (Apache-2.0). It decodes
 * on the device and makes no network call, as the project's privacy rules
 * require. It asks for the camera when it mounts, so mount it only on a user
 * action. The stream stops on unmount and on the first decode.
 *
 * html5-qrcode's `scanner.stop()` throws synchronously while the scanner is
 * still starting, and React StrictMode's double mount gets there. `safeStop`
 * therefore checks for the SCANNING state and also catches, because the
 * library's behaviour differs between versions.
 */
import { useEffect, useId, useRef, useState } from 'react';

import {
  Html5Qrcode,
  Html5QrcodeScannerState,
  Html5QrcodeSupportedFormats,
} from 'html5-qrcode';

interface QrScannerProps {
  /** Fired exactly once per successful decode. */
  onDecode(text: string): void;
  /** Fired on camera-permission denial or device-not-available. */
  onError?(error: Error): void;
  /** Square viewfinder side-length in CSS pixels. Default 240. */
  size?: number;
}

const safeStop = async (scanner: Html5Qrcode | null): Promise<void> => {
  if (!scanner) return;
  try {
    if (scanner.getState() === Html5QrcodeScannerState.SCANNING) {
      await scanner.stop();
    }
  } catch {
    // Already stopped or never started.
  }
  try {
    scanner.clear();
  } catch {
    // ignore
  }
};

const QrScanner: React.FC<QrScannerProps> = ({
  onDecode,
  onError,
  size = 240,
}) => {
  const viewfinderId = useId().replace(/[:]/g, '_');
  const scannerRef = useRef<Html5Qrcode | null>(null);
  const [isStarting, setIsStarting] = useState(true);
  const decodedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    const scanner = new Html5Qrcode(viewfinderId, {
      verbose: false,
      formatsToSupport: [Html5QrcodeSupportedFormats.QR_CODE],
    });
    scannerRef.current = scanner;

    const start = async () => {
      try {
        await scanner.start(
          { facingMode: 'environment' },
          { fps: 10, qrbox: { width: size - 40, height: size - 40 } },
          (text) => {
            if (decodedRef.current || cancelled) return;
            decodedRef.current = true;
            void safeStop(scanner).then(() => {
              if (!cancelled) onDecode(text);
            });
          },
          /* onDecodeError */ () => {
            // A frame without a code is normal.
          },
        );
        if (cancelled) {
          // Unmounted while the start was pending.
          await safeStop(scanner);
          return;
        }
        setIsStarting(false);
      } catch (err) {
        if (!cancelled && onError) {
          onError(err instanceof Error ? err : new Error(String(err)));
        }
      }
    };
    void start();

    return () => {
      cancelled = true;
      void safeStop(scannerRef.current);
      scannerRef.current = null;
    };
    // Size and callbacks are read once at mount. Remount to change them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      id={viewfinderId}
      role="region"
      aria-label="QR code camera viewfinder"
      aria-busy={isStarting}
      style={{
        width: size,
        height: size,
        background: 'var(--color-neutral-95, #F5EFF7)',
        borderRadius: 'var(--radius-md, 12px)',
        overflow: 'hidden',
      }}
    />
  );
};

export default QrScanner;
