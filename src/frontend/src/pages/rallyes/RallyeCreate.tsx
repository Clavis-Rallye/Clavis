import React, { useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Box, Button, Stack, Typography } from '@mui/material';
import MultilingualField, { type MultilingualFieldHandle } from '../../components/MultilingualField';
import ErrorAlert from '../../components/ErrorAlert';
import { createRallye } from '../../api/content';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type { MultilingualString } from '../../api/types';

const RallyeCreate: React.FC = () => {
  const { t } = useTranslation('content');
  const { user } = useAuth();
  const navigate = useNavigate();

  const [title, setTitle] = useState<MultilingualString>({ de: '', en: '' });
  const [description, setDescription] = useState<MultilingualString>({ de: '', en: '' });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Revealed on an incomplete create click. The field itself stays untouched
  // until then.
  const [showErrors, setShowErrors] = useState(false);
  const titleRef = useRef<MultilingualFieldHandle>(null);

  const deTitleMissing = !title.de?.trim();
  const canSubmit = !!user?.organization_slug && !submitting;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!user?.organization_slug) return;
    if (deTitleMissing) {
      setShowErrors(true);
      titleRef.current?.focus();
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const rallye = await createRallye(user.organization_slug, { title, description });
      navigate(`/rallyes/${rallye.id}`, { state: { created: true } });
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
      setSubmitting(false);
    }
  };

  return (
    <Box component="form" onSubmit={handleSubmit} noValidate>
      <Typography variant="h2" component="h1" gutterBottom>
        {t('rallye.create.title')}
      </Typography>
      {error && <ErrorAlert message={error} />}
      <Stack spacing={3} sx={{ mt: 3 }}>
        <MultilingualField
          ref={titleRef}
          value={title}
          onChange={setTitle}
          label={t('rallye.create.titleLabel')}
          required
          showErrors={showErrors}
        />

        <MultilingualField
          value={description}
          onChange={setDescription}
          label={t('rallye.create.descriptionLabel')}
          richText
        />

        <Stack direction="row" spacing={2} sx={{
          justifyContent: "flex-end"
        }}>
          <Button variant="outlined" onClick={() => navigate('/rallyes')} disabled={submitting}>
            {t('actions.cancel', { ns: 'common' })}
          </Button>
          <Button type="submit" variant="contained" disabled={!canSubmit}>
            {t('rallye.create.submit')}
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
};

export default RallyeCreate;
