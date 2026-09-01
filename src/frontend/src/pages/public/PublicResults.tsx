import React, { useEffect, useState } from 'react';
import { Link as RouterLink } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Card,
  CardContent,
  Container,
  Stack,
  Typography,
} from '@mui/material';
import EmojiEventsIcon from '@mui/icons-material/EmojiEvents';
import LinkOffIcon from '@mui/icons-material/LinkOff';
import {
  lookupPublicResults,
  type PublicResults as PublicResultsData,
} from '../../services/publicResults';
import { AnswerStateChip } from '../../components/AnswerState';
import ErrorAlert from '../../components/ErrorAlert';
import LanguageSwitcher from '../../components/LanguageSwitcher';
import LegalLinks from '../../components/LegalLinks';
import LoadingSpinner from '../../components/LoadingSpinner';
import { useInstanceConfig } from '../../config/instanceConfig';
import logoSmall from '../../assets/logos/logo-small.svg';
import i18n from '../../i18n';
import { formatDate } from '../../utils/formatDate';

type LookupState =
  | { status: 'loading' }
  | { status: 'ok'; data: PublicResultsData }
  | { status: 'not_found' }
  | { status: 'expired' }
  | { status: 'revoked' }
  | { status: 'error' };

// The UI ships de/en only; a snapshot frozen in another content language
// falls back to whatever the visitor's browser detected.
const SUPPORTED_UI_LANGUAGES = ['de', 'en'];

/**
 * The public final standing behind `/results#<slug>`.
 *
 * No account, no auth probe, no ProtectedRoute — the page renders from the
 * lookup alone. The slug is the URL fragment (browsers never send it) and is
 * posted to `/api/public/results/lookup` in the body, so no request line
 * carries it. The frame follows LegalShell (brand mark, one card) plus the
 * language switch, because the visitor may read the standing in either UI
 * language while the page opens in the run's first content language.
 */
const PublicResults: React.FC = () => {
  const { t } = useTranslation('runs');
  const { brand_name: brand } = useInstanceConfig();
  const [state, setState] = useState<LookupState>({ status: 'loading' });
  // Re-run counter behind the retry button on transient failures.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    void (async () => {
      const slug = window.location.hash.replace(/^#/, '');
      if (!slug) {
        // A link without a slug is indistinguishable from an unknown one.
        setState({ status: 'not_found' });
        return;
      }
      setState({ status: 'loading' });
      const result = await lookupPublicResults(slug, controller.signal);
      if (cancelled) return;
      if (result.kind === 'ok') {
        // The singleton, not the per-render value from useTranslation — its
        // identity changes when the language does, which would re-run this
        // effect and fire a second lookup.
        const lang = result.data.language;
        if (SUPPORTED_UI_LANGUAGES.includes(lang) && i18n.resolvedLanguage !== lang) {
          void i18n.changeLanguage(lang);
        }
        setState({ status: 'ok', data: result.data });
      } else {
        setState({ status: result.kind });
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [attempt]);

  const data = state.status === 'ok' ? state.data : null;
  const winners = data ? data.entries.filter((e) => e.is_winner) : [];
  const invalid = state.status === 'expired' || state.status === 'revoked';

  return (
    <Box sx={{ bgcolor: 'background.default', minHeight: '100dvh', py: 4 }}>
      <Container maxWidth="md">
        <Stack
          component="header"
          direction="row"
          spacing={1}
          sx={{ alignItems: 'center', justifyContent: 'space-between', mb: 3 }}
        >
          <Stack
            direction="row"
            spacing={1}
            component={RouterLink}
            to="/"
            style={{ textDecoration: 'none', color: 'inherit' }}
            sx={{ alignItems: 'center' }}
          >
            {/* Decorative — the wordmark next to it carries the name; an
                alt text repeating it is an axe image-redundant-alt finding. */}
            <Box component="img" src={logoSmall} alt="" sx={{ width: 32, height: 28 }} />
            <Typography variant="h6" component="span">
              {brand}
            </Typography>
          </Stack>
          <LanguageSwitcher />
        </Stack>

        {/* main lets the route focus land on the heading. */}
        <Card component="main">
          <CardContent sx={{ p: { xs: 3, sm: 4 } }}>
            {state.status === 'loading' && <LoadingSpinner />}

            {state.status === 'error' && (
              <ErrorAlert
                message={t('results.public.loadError')}
                onRetry={() => setAttempt((n) => n + 1)}
              />
            )}

            {(state.status === 'not_found' || invalid) && (
              <Stack spacing={2} sx={{ alignItems: 'center', textAlign: 'center', py: 4 }}>
                <LinkOffIcon color="disabled" sx={{ fontSize: 48 }} />
                <Typography variant="h2" component="h1">
                  {t('results.title')}
                </Typography>
                {/* Neutral wording only: it must not reveal which of
                    expired, revoked or unknown the slug is. */}
                <Typography variant="h6" component="p" color="text.secondary">
                  {invalid ? t('results.public.invalid') : t('results.public.notFound')}
                </Typography>
              </Stack>
            )}

            {data && (
              <>
                <Stack
                  direction={{ xs: 'column', sm: 'row' }}
                  spacing={{ xs: 1, sm: 2 }}
                  sx={{ alignItems: { sm: 'center' }, flexWrap: 'wrap', mb: 1 }}
                >
                  <Typography variant="h2" component="h1" sx={{ fontWeight: 700 }}>
                    {data.run_name}
                  </Typography>
                  {/* The header stack is a column on phones with the default
                      stretch alignment. The chip must keep its own width
                      there. */}
                  <AnswerStateChip
                    state="scored"
                    label={t('results.labelFinal')}
                    size="medium"
                    sx={{ alignSelf: { xs: 'flex-start' } }}
                  />
                </Stack>
                {data.ended_at && (
                  <Typography variant="h5" component="p" color="text.secondary" sx={{ mb: 3 }}>
                    {t('results.endedOn', {
                      // The page language decides, not the browser locale.
                      date: formatDate(data.ended_at, i18n.language),
                    })}
                  </Typography>
                )}

                {winners.length > 0 && (
                  <Stack
                    direction="row"
                    spacing={{ xs: 2, md: 3 }}
                    sx={{ alignItems: 'center', flexWrap: 'wrap', mb: 4 }}
                  >
                    <EmojiEventsIcon color="warning" sx={{ fontSize: { xs: 48, md: 72 } }} />
                    <Stack spacing={0.5}>
                      <Typography variant="overline" component="p">
                        {t('results.winnerLabel', { count: winners.length })}
                      </Typography>
                      <Stack direction="row" spacing={{ xs: 2, md: 4 }} sx={{ flexWrap: 'wrap' }}>
                        {winners.map((w, idx) => (
                          <Box key={`${w.rank}-${w.team_name ?? 'unnamed'}-${idx}`}>
                            <Typography variant="h3" component="p">
                              {w.team_name ??
                                t('results.public.unnamedTeam', { index: idx + 1 })}
                            </Typography>
                            <Typography variant="h5" component="p" color="text.secondary">
                              {t('participant.leaderboard.points', { points: w.points })}
                            </Typography>
                          </Box>
                        ))}
                      </Stack>
                    </Stack>
                  </Stack>
                )}

                <Stack>
                  {data.entries.map((entry, idx) => (
                    <Stack
                      key={`${entry.rank}-${entry.team_name ?? 'unnamed'}-${idx}`}
                      direction="row"
                      spacing={{ xs: 1, md: 2 }}
                      sx={{
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        py: { xs: 0.75, md: 1.25 },
                        borderBottom: '1px solid',
                        borderColor: 'divider',
                        gap: 1,
                      }}
                    >
                      <Stack
                        direction="row"
                        spacing={{ xs: 1, md: 2 }}
                        sx={{ alignItems: 'center', minWidth: 0 }}
                      >
                        <Typography
                          variant="h4"
                          component="p"
                          sx={{ minWidth: { xs: 36, md: 56 }, fontWeight: 700, color: 'text.secondary' }}
                        >
                          {entry.rank}.
                        </Typography>
                        <Typography variant="h4" component="p">
                          {entry.team_name ??
                            t('results.public.unnamedTeam', { index: idx + 1 })}
                        </Typography>
                        {entry.is_winner && (
                          <EmojiEventsIcon color="warning" sx={{ fontSize: { xs: 28, md: 36 } }} />
                        )}
                      </Stack>
                      <Typography
                        variant="h5"
                        component="p"
                        sx={{ fontWeight: 700, whiteSpace: 'nowrap' }}
                      >
                        {t('participant.leaderboard.points', { points: entry.points })}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>
              </>
            )}
          </CardContent>
        </Card>
        <LegalLinks />
      </Container>
    </Box>
  );
};

export default PublicResults;
