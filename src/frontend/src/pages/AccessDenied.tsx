import React from 'react';
import { useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Box, Card, CardContent, Typography, Button, Alert } from '@mui/material';

const AccessDenied: React.FC = () => {
  const { t } = useTranslation('auth');
  const navigate = useNavigate();

  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        minHeight: "70vh",
        px: 2
      }}>
      <Card sx={{ maxWidth: 480, width: '100%' }}>
        <CardContent sx={{ p: 4, textAlign: 'center' }}>
          <Typography variant="h4" component="h1" gutterBottom>
            {t('accessDenied.title')}
          </Typography>
          <Alert severity="warning" sx={{ mb: 3 }}>
            {t('accessDenied.message')}
          </Alert>
          <Button variant="contained" onClick={() => navigate('/dashboard', { replace: true })}>
            {t('accessDenied.backButton')}
          </Button>
        </CardContent>
      </Card>
    </Box>
  );
};

export default AccessDenied;
