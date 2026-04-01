import React from 'react';
import { Alert, Button } from '@mui/material';
import { useTranslation } from 'react-i18next';

interface ErrorAlertProps {
  message: string;
  onRetry?: () => void;
  severity?: 'error' | 'warning';
}

const ErrorAlert: React.FC<ErrorAlertProps> = ({ message, onRetry, severity = 'error' }) => {
  const { t } = useTranslation('common');
  return (
    <Alert
      severity={severity}
      action={
        onRetry ? (
          <Button color="inherit" size="small" onClick={onRetry}>
            {t('actions.retry')}
          </Button>
        ) : undefined
      }
    >
      {message}
    </Alert>
  );
};

export default ErrorAlert;
