import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { isRichTextEmpty } from '../utils/richtext';
import { TIPS_LABEL_MAX_LENGTH, tipsProblem, type TipsProblem } from '../utils/stationTips';
import { Box, Button, IconButton, Stack, TextField, Typography } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import { useTranslation } from 'react-i18next';
import type { MultilingualString, StationTipLink, StationTips } from '../api/types';
import MultilingualField from './MultilingualField';

interface StationTipsFieldProps {
  value: StationTips | null;
  onChange: (next: StationTips | null) => void;
  disabled?: boolean;
  /**
   * Set by the page once a save was refused for the tips. The field that the
   * refusal names then carries aria-invalid.
   */
  showErrors?: boolean;
}

export interface StationTipsFieldHandle {
  /** Moves the focus to the field the refusal names. */
  focusProblem: () => void;
}

const MAX_LINKS = 10;

const isValidUrl = (url: string) => /^https?:\/\//i.test(url);

/** The id of the refusal text. One tips editor stands on a page. */
const PROBLEM_ID = 'station-tips-problem';
/** Holds the aria-invalid a field had before the refusal marked it. */
const PREVIOUS_INVALID = 'data-tips-previous-invalid';

type ProblemField = { kind: 'header' } | { kind: 'label' | 'url'; index: number } | { kind: 'add' };

const hasLabel = (link: StationTipLink) => Object.values(link.label ?? {}).some((v) => v.trim());

/** The field a refusal is about. Follows the order of `tipsProblem`. */
const problemField = (problem: TipsProblem | null, links: StationTipLink[]): ProblemField | null => {
  switch (problem) {
    case null:
      return null;
    case 'header':
    case 'headerTooLong':
      return { kind: 'header' };
    case 'links':
      return { kind: 'add' };
    case 'label':
      return { kind: 'label', index: links.findIndex((link) => !hasLabel(link)) };
    case 'labelTooLong':
      return {
        kind: 'label',
        index: links.findIndex((link) =>
          Object.values(link.label ?? {}).some((v) => v.length > TIPS_LABEL_MAX_LENGTH),
        ),
      };
    case 'url':
      return { kind: 'url', index: links.findIndex((link) => !/^https?:\/\/\S+$/i.test(link.url.trim())) };
  }
};

const fieldKey = (field: ProblemField | null): string | null => {
  if (!field || field.kind === 'add') return null;
  return field.kind === 'header' ? 'header' : `${field.kind}-${field.index}`;
};

/**
 * The input behind a key of `fieldKey`. MultilingualField ends the id of its
 * input with the language of the open tab, so the heading and the label are
 * found by the start of their id. Its helper text starts the same way, hence
 * the element names.
 */
const findField = (root: HTMLElement, key: string): HTMLElement | null => {
  const [kind, index] = key.split('-');
  if (kind === 'url') return root.querySelector<HTMLElement>(`[id="station-tips-link-url-${index}"]`);
  const prefix = kind === 'header' ? 'station-tips-header-' : `station-tips-link-label-${index}-`;
  return root.querySelector<HTMLElement>(
    `input[id^="${prefix}"], textarea[id^="${prefix}"], [role="textbox"][id^="${prefix}"]`,
  );
};

const describedBy = (el: Element) => (el.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean);

/** Takes back what `markField` set, on every field under the root. */
const unmarkFields = (root: HTMLElement) => {
  root.querySelectorAll(`[aria-describedby~="${PROBLEM_ID}"]`).forEach((el) => {
    const rest = describedBy(el).filter((id) => id !== PROBLEM_ID);
    if (rest.length) el.setAttribute('aria-describedby', rest.join(' '));
    else el.removeAttribute('aria-describedby');
  });
  root.querySelectorAll(`[${PREVIOUS_INVALID}]`).forEach((el) => {
    const previous = el.getAttribute(PREVIOUS_INVALID);
    if (previous) el.setAttribute('aria-invalid', previous);
    else el.removeAttribute('aria-invalid');
    el.removeAttribute(PREVIOUS_INVALID);
  });
};

/** Ties the refusal text to the field, and marks it invalid after a refused save. */
const markField = (el: HTMLElement, invalid: boolean) => {
  const ids = describedBy(el);
  if (!ids.includes(PROBLEM_ID)) el.setAttribute('aria-describedby', [...ids, PROBLEM_ID].join(' '));
  if (invalid && el.getAttribute('aria-invalid') !== 'true') {
    el.setAttribute(PREVIOUS_INVALID, el.getAttribute('aria-invalid') ?? '');
    el.setAttribute('aria-invalid', 'true');
  }
};

/**
 * Creator editor for a station's optional tips & links popup.
 * Participants see this as a dialog they can open from the station screen —
 * see StationTipsDialog for the read-only counterpart.
 *
 * The header + links are collapsed to `null` the moment both are empty, so
 * a station that was never given tips submits `tips: null` rather than an
 * empty-but-present object.
 */
const StationTipsField = forwardRef<StationTipsFieldHandle, StationTipsFieldProps>(
  ({ value, onChange, disabled = false, showErrors = false }, ref) => {
    const { t } = useTranslation('content');
    const rootRef = useRef<HTMLDivElement>(null);
    const addRef = useRef<HTMLButtonElement>(null);

    const header: MultilingualString = value?.header ?? {};
    const links: StationTipLink[] = value?.links ?? [];
    const problem = tipsProblem(value);
    const refused = problemField(problem, links);
    const refusedKey = fieldKey(refused);

    useImperativeHandle(ref, () => ({
      focusProblem: () => {
        const root = rootRef.current;
        if (!root || !refused) return;
        if (refusedKey) findField(root, refusedKey)?.focus();
        else addRef.current?.focus();
      },
    }));

    // The heading and the label are MultilingualFields, which take no aria
    // attributes for their input and build it anew on a change of the language
    // tab. So the attributes are set on the element, and set again when the
    // input under the tips changes.
    useEffect(() => {
      const root = rootRef.current;
      if (!root || !refusedKey) return;
      const apply = () => {
        const el = findField(root, refusedKey);
        if (el) markField(el, showErrors);
      };
      apply();
      const observer = new MutationObserver(apply);
      observer.observe(root, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['aria-describedby', 'aria-invalid', 'id'],
      });
      return () => {
        observer.disconnect();
        unmarkFields(root);
      };
    }, [refusedKey, showErrors]);

    const emitOrClear = (nextHeader: MultilingualString, nextLinks: StationTipLink[]) => {
      const stillHasHeader = Object.values(nextHeader).some((v) => !isRichTextEmpty(v));
      if (!stillHasHeader && nextLinks.length === 0) {
        onChange(null);
        return;
      }
      onChange({ header: nextHeader, links: nextLinks });
    };

    const handleHeaderChange = (nextHeader: MultilingualString) => {
      emitOrClear(nextHeader, links);
    };

    const handleAddLink = () => {
      if (links.length >= MAX_LINKS) return;
      emitOrClear(header, [...links, { label: {}, url: '' }]);
    };

    const handleRemoveLink = (idx: number) => {
      emitOrClear(header, links.filter((_, i) => i !== idx));
    };

    const handleLabelChange = (idx: number, nextLabel: MultilingualString) => {
      const nextLinks = links.map((link, i) => (i === idx ? { ...link, label: nextLabel } : link));
      emitOrClear(header, nextLinks);
    };

    const handleUrlChange = (idx: number, nextUrl: string) => {
      const nextLinks = links.map((link, i) => (i === idx ? { ...link, url: nextUrl } : link));
      emitOrClear(header, nextLinks);
    };

    return (
      <Box ref={rootRef}>
        <Typography variant="subtitle2" component="div" sx={{ mb: 0.5 }}>
          {t('station.tips.sectionTitle')}
        </Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
          {t('station.tips.helper')}
        </Typography>
        {/* Says what a started popup still lacks. The form refuses to save until then. */}
        {problem && (
          <Typography id={PROBLEM_ID} variant="body2" color="error" role="status" sx={{ mb: 1.5 }}>
            {t(`station.tips.incomplete.${problem}`)}
          </Typography>
        )}

        <Stack spacing={2}>
          <MultilingualField
            value={header}
            onChange={handleHeaderChange}
            label={t('station.tips.headerLabel')}
            id="station-tips-header"
            richText
          />

          {links.map((link, idx) => {
            const urlInvalid = !!link.url.trim() && !isValidUrl(link.url);
            const labelRefused = showErrors && refused?.kind === 'label' && refused.index === idx;
            const urlRefused = showErrors && refused?.kind === 'url' && refused.index === idx;
            const removeLabel = t('station.tips.removeLink', {
              label: link.label.de || `${idx + 1}`,
            });
            return (
              <Stack
                key={idx}
                direction={{ xs: 'column', sm: 'row' }}
                spacing={1}
                // The label has language tabs above its field. Aligned at the
                // bottom, the two fields of one link stand on one line. Aligned
                // at the top, the address would sit beside the tabs.
                sx={{ alignItems: { xs: 'stretch', sm: 'flex-end' } }}
              >
                <Box
                  data-tips-field={`label-${idx}`}
                  sx={{
                    flex: 1,
                    minWidth: 0,
                    // The red frame of a refused field. MultilingualField draws
                    // it for a missing German text only.
                    ...(labelRefused && {
                      '& .MuiOutlinedInput-notchedOutline': { borderColor: 'error.main' },
                    }),
                  }}
                >
                  <MultilingualField
                    value={link.label}
                    onChange={(next) => handleLabelChange(idx, next)}
                    label={t('station.tips.linkLabel', { index: idx + 1 })}
                    id={`station-tips-link-label-${idx}`}
                  />
                </Box>
                <TextField
                  label={t('station.tips.urlLabel')}
                  value={link.url}
                  onChange={(e) => handleUrlChange(idx, e.target.value)}
                  id={`station-tips-link-url-${idx}`}
                  error={urlInvalid || urlRefused}
                  helperText={urlInvalid ? t('station.tips.urlInvalid') : undefined}
                  fullWidth
                  disabled={disabled}
                  sx={{ flex: 1 }}
                />
                <IconButton
                  aria-label={removeLabel}
                  onClick={() => handleRemoveLink(idx)}
                  disabled={disabled}
                  size="small"
                >
                  <DeleteIcon fontSize="small" />
                </IconButton>
              </Stack>
            );
          })}

          <Button
            ref={addRef}
            startIcon={<AddIcon />}
            onClick={handleAddLink}
            size="small"
            variant="outlined"
            disabled={disabled || links.length >= MAX_LINKS}
            sx={{ alignSelf: 'flex-start' }}
          >
            {t('station.tips.addLink')}
          </Button>
        </Stack>
      </Box>
    );
  },
);

StationTipsField.displayName = 'StationTipsField';

export default StationTipsField;
