import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Stack, TextField, Typography } from '@mui/material';
import { Html5Qrcode } from 'html5-qrcode';
import { createSubmission } from '../../api/runs';
import ErrorAlert from '../../components/ErrorAlert';
import type { ChallengeFormProps } from './TextChallengeForm';
import { SubmitAnswerButton, useAnswerSend } from './TextChallengeForm';

const SCANNER_ID = 'qr-scanner-region';

const QrScanChallengeForm: React.FC<ChallengeFormProps> = ({
  orgSlug,
  runId,
  challenge,
  alreadySubmitted,
  onSubmitted,
  submitAction,
  initialContent,
  onReload,
}) => {
  const { t } = useTranslation('runs');
  const [scanning, setScanning] = useState(false);
  const [scannedValue, setScannedValue] = useState<string | null>(null);
  const [manualValue, setManualValue] = useState(
    typeof initialContent?.scanned_value === 'string' ? initialContent.scanned_value : '',
  );
  const [cameraError, setCameraError] = useState(false);
  const { submitting, begin, end, report } = useAnswerSend();
  const [error, setError] = useState<string | null>(null);
  const qrRef = useRef<Html5Qrcode | null>(null);

  useEffect(() => {
    return () => {
      // After a scan the success callback has stopped the scanner already, and stop() throws
      // synchronously on a scanner that does not run. So stop a running one only, and let
      // nothing leave the cleanup.
      try {
        if (qrRef.current?.isScanning) {
          qrRef.current.stop().catch(() => {
            // Closing the camera is best effort; if the teardown rejects, the user can do nothing about it.
          });
        }
      } catch {
        // Closing the camera is best effort; the scanner stopped between the check and the call.
      }
    };
  }, []);

  const startScan = async () => {
    setCameraError(false);
    setScannedValue(null);
    setError(null);
    setScanning(true);
    try {
      const qr = new Html5Qrcode(SCANNER_ID);
      qrRef.current = qr;
      await qr.start(
        { facingMode: 'environment' },
        { fps: 10, qrbox: 250 },
        (decodedText) => {
          setScannedValue(decodedText);
          setScanning(false);
          qr.stop().catch(() => {
            // Closing the camera is best effort; the scanned value is already captured.
          });
        },
        undefined,
      );
    } catch {
      setCameraError(true);
      setScanning(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const value = scannedValue ?? manualValue.trim();
    if (!value) return;
    if (!begin(e.currentTarget as HTMLFormElement)) return;
    setError(null);
    // What this member tried to send. A teammate's earlier answer refuses it,
    // and the view shows it instead of dropping it.
    let attempted: ChallengeFormProps['initialContent'];
    try {
      const content = { scanned_value: value };
      attempted = content;
      const result = submitAction
        ? await submitAction(content)
        : await createSubmission(orgSlug, runId, {
            run_challenge_id: challenge.id,
            content,
          });
      onSubmitted(result);
    } catch (err) {
      await report(err, attempted, onReload, setError);
      end();
    }
  };

  if (alreadySubmitted) {
    return (
      <Typography variant="body2" sx={{
        color: "text.secondary"
      }}>
        {t('participant.challenge.alreadySubmitted')}
      </Typography>
    );
  }

  const submitValue = scannedValue ?? manualValue.trim();

  return (
    <Box component="form" onSubmit={handleSubmit}>
      <Stack spacing={2}>
        {error && <ErrorAlert message={error} />}
        {cameraError && (
          <ErrorAlert message={t('participant.challenge.qr.cameraError')} severity="warning" />
        )}

        {/* The scanner region stays mounted, so Html5Qrcode can find the div. */}
        {/* `role="region"` is what makes the aria-label legal: on a bare div the
            label is a prohibited attribute and screen readers drop it. */}
        <Box
          id={SCANNER_ID}
          role="region"
          aria-label={t('participant.challenge.qr.cameraViewport')}
          sx={{ width: '100%', minHeight: scanning ? 280 : 0 }}
        />

        {/* Hidden status region that announces the scan result to screen readers. */}
        <div aria-live="polite" role="status" style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0,0,0,0)' }}>
          {scannedValue ? t('participant.challenge.qr.scannedStatus', { code: scannedValue }) : ''}
        </div>

        {scannedValue ? (
          <Stack spacing={1}>
            <Typography variant="body2">
              {t('participant.challenge.qr.scanned')}: <strong>{scannedValue}</strong>
            </Typography>
            <Button variant="outlined" size="small" onClick={() => setScannedValue(null)}>
              {t('participant.challenge.qr.rescan')}
            </Button>
          </Stack>
        ) : (
          !scanning && (
            <Button variant="outlined" onClick={startScan} disabled={submitting}>
              {t('participant.challenge.qr.startScan')}
            </Button>
          )
        )}

        <Typography variant="caption" sx={{
          color: "text.secondary"
        }}>
          {t('participant.challenge.qr.scanInstruction')}
        </Typography>
        <TextField
          label={t('participant.challenge.qr.manualLabel')}
          fullWidth
          size="small"
          value={manualValue}
          onChange={(e) => setManualValue(e.target.value)}
          disabled={submitting || !!scannedValue}
        />

        <SubmitAnswerButton submitting={submitting} disabled={!submitValue} />
      </Stack>
    </Box>
  );
};

export default QrScanChallengeForm;
