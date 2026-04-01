import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Badge, Box, FormHelperText, Stack, Tab, Tabs, TextField, Tooltip } from '@mui/material';
import { useTranslation } from 'react-i18next';
import type { MultilingualString } from '../api/types';
import { useOrg } from '../contexts/OrgContext';
import { REQUIRED_LOCALE, isRtlLocale, localeLabel, type Locale } from '../i18n/contentLocales';
import { isRichTextEmpty } from '../utils/richtext';
import RichTextEditor from './RichTextEditor';

export interface MultilingualFieldHandle {
  /** Switches to the required locale's tab if needed, then focuses its input. */
  focus: () => void;
}

interface MultilingualFieldProps {
  value: MultilingualString;
  onChange: (next: MultilingualString) => void;
  label: string;
  multiline?: boolean;
  /**
   * Render the rich-text editor per locale instead of a text input.
   * The value then holds HTML. Legacy plain text is converted on first edit.
   */
  richText?: boolean;
  required?: boolean;
  minRows?: number;
  id?: string;
  helperText?: string;
  placeholder?: string;
  /**
   * Reveals a missing required translation even before it was touched. The
   * page sets this once a create/save click found the form incomplete, so
   * every affected field turns red together.
   */
  showErrors?: boolean;
}

const MultilingualField = forwardRef<MultilingualFieldHandle, MultilingualFieldProps>(
  (
    {
      value,
      onChange,
      label,
      multiline = false,
      richText = false,
      required = false,
      minRows = 3,
      id,
      helperText,
      placeholder,
      showErrors = false,
    },
    ref,
  ) => {
    const { t, i18n } = useTranslation('content');
    const { languages } = useOrg();
    // The org's enabled content locales, never empty (German is always required).
    const locales: Locale[] = languages.length ? languages : [REQUIRED_LOCALE];
    const [active, setActive] = useState<Locale>(REQUIRED_LOCALE);
    // If the org disabled the locale we were on, fall back to the first enabled.
    const activeLocale: Locale = locales.includes(active) ? active : locales[0];
    // Set on blur, so an untouched empty required field doesn't start red. Only
    // leaving the field, or the page's showErrors on an incomplete submit, does.
    const [touched, setTouched] = useState(false);
    const pendingFocusRef = useRef(false);

    const inputId = id ?? `multilingual-${label.toLowerCase().replace(/\s+/g, '-')}`;
    const isFilled = (locale: Locale): boolean =>
      richText ? !isRichTextEmpty(value[locale]) : !!value[locale]?.trim();
    const isRequiredMissing = required && !isFilled(REQUIRED_LOCALE);
    const revealErrors = touched || showErrors;
    const showRequiredError = isRequiredMissing && revealErrors;

    useImperativeHandle(ref, () => ({
      focus: () => {
        if (activeLocale === REQUIRED_LOCALE) {
          document.getElementById(`${inputId}-${REQUIRED_LOCALE}`)?.focus();
        } else {
          pendingFocusRef.current = true;
          setActive(REQUIRED_LOCALE);
        }
      },
    }));

    useEffect(() => {
      if (pendingFocusRef.current && activeLocale === REQUIRED_LOCALE) {
        pendingFocusRef.current = false;
        document.getElementById(`${inputId}-${REQUIRED_LOCALE}`)?.focus();
      }
    }, [activeLocale, inputId]);

    const isRtl = isRtlLocale(activeLocale);
    const showError = activeLocale === REQUIRED_LOCALE && showRequiredError;
    const resolvedHelper =
      helperText ?? (activeLocale === REQUIRED_LOCALE ? undefined : t('multilingual.optionalHint'));
    const handleBlur = () => setTouched(true);

    return (
      <Stack spacing={1} sx={{ width: '100%' }}>
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: 1,
          }}
        >
          <Box component="label" htmlFor={`${inputId}-${activeLocale}`} sx={{ fontWeight: 500 }}>
            {label}
            {required && <span aria-hidden="true"> *</span>}
          </Box>
          <Tabs
            value={activeLocale}
            onChange={(_, next: Locale) => setActive(next)}
            variant="scrollable"
            scrollButtons={false}
            sx={{ minHeight: 36 }}
            aria-label={locales.map((l) => localeLabel(l, i18n.language)).join(' / ')}
          >
            {locales.map((locale) => {
              // Only the required locale can ever be marked missing — an
              // optional field never shows the mark, whatever it holds.
              const tabError = locale === REQUIRED_LOCALE && showRequiredError;
              const badge = (
                <Badge
                  color={tabError ? 'error' : 'primary'}
                  variant="dot"
                  invisible={!tabError}
                  sx={{ '& .MuiBadge-badge': { right: -6, top: 4 } }}
                >
                  <span>{localeLabel(locale, i18n.language)}</span>
                </Badge>
              );
              return (
                <Tab
                  key={locale}
                  value={locale}
                  label={
                    tabError ? (
                      <Tooltip title={t('multilingual.missingTranslation')}>{badge}</Tooltip>
                    ) : (
                      badge
                    )
                  }
                  sx={{
                    minHeight: 36,
                    py: 0,
                    textTransform: 'none',
                    fontSize: 13,
                    // Subtle highlight on the required tab when empty so the
                    // author can't miss it even if they're editing AR/UK.
                    color: tabError ? 'error.main' : undefined,
                  }}
                />
              );
            })}
          </Tabs>
        </Box>
        {richText ? (
          <Box onBlur={handleBlur}>
            <RichTextEditor
              key={activeLocale}
              id={`${inputId}-${activeLocale}`}
              value={value[activeLocale] ?? ''}
              onChange={(html) => onChange({ ...value, [activeLocale]: html })}
              ariaLabel={`${label} (${localeLabel(activeLocale, i18n.language)})`}
              dir={isRtl ? 'rtl' : 'ltr'}
              error={showError}
              placeholder={placeholder}
            />
            {(resolvedHelper || showError) && (
              <FormHelperText error={showError} sx={{ mx: 1.75 }}>
                {showError ? t('multilingual.requiredHint') : resolvedHelper}
              </FormHelperText>
            )}
          </Box>
        ) : (
          <TextField
            key={activeLocale}
            id={`${inputId}-${activeLocale}`}
            value={value[activeLocale] ?? ''}
            onChange={(e) => onChange({ ...value, [activeLocale]: e.target.value })}
            onBlur={handleBlur}
            fullWidth
            multiline={multiline}
            minRows={multiline ? minRows : undefined}
            required={required && activeLocale === REQUIRED_LOCALE}
            error={showError}
            // Every plain field shows the hint "Pflichtfeld: bitte auf
            // Deutsch ausfüllen.", as the rich text variant above does, not
            // only the red mark.
            helperText={showError ? t('multilingual.requiredHint') : resolvedHelper}
            placeholder={placeholder}
            slotProps={{
              htmlInput: {
                'aria-label': `${label} (${localeLabel(activeLocale, i18n.language)})`,
                dir: isRtl ? 'rtl' : 'ltr',
              },
            }}
          />
        )}
      </Stack>
    );
  },
);

MultilingualField.displayName = 'MultilingualField';

export default MultilingualField;
