import React from 'react';
import Box from '@mui/material/Box';
import InputAdornment from '@mui/material/InputAdornment';
import MenuItem from '@mui/material/MenuItem';
import TextField from '@mui/material/TextField';

import type { SupportedLanguage } from '../i18n/resolveDefaultLanguage';
import languageIcon from '../assets/icons/language.svg';

interface LanguageOption {
  code: SupportedLanguage;
  label: string;
}

interface LanguageDropdownProps {
  value: SupportedLanguage;
  onChange: (lang: SupportedLanguage) => void;
  options: LanguageOption[];
  label: string;
  /** id of an element describing the field (e.g. helper text). Wires up aria-describedby. */
  describedById?: string;
  id?: string;
}

const LanguageDropdown: React.FC<LanguageDropdownProps> = ({
  value,
  onChange,
  options,
  label,
  describedById,
  id = 'language-dropdown',
}) => {
  // Stable id for the visible label. The select's underlying input carries
  // `aria-labelledby={labelId}` via slotProps.htmlInput so screen readers
  // announce exactly the visible label text — without relying on a
  // duplicated aria-label string that could drift from the visible copy.
  const labelId = `${id}-label`;

  const renderSelectedValue = (val: unknown) => {
    const code = val as SupportedLanguage;
    const opt = options.find((o) => o.code === code);
    return (
      // Noto Sans 16/400/24, letter spacing 0.50, #1D1B20. The visible
      // "Language" label is rendered as an external sibling (see below), so it
      // exists in the DOM independently of `renderValue` and survives the
      // open-menu state where the trigger is briefly covered.
      <Box
        component="span"
        sx={{
          fontFamily: "'Twemoji Country Flags', 'Noto Sans', sans-serif",
          fontSize: '16px',
          fontWeight: 400,
          lineHeight: '24px',
          letterSpacing: '0.50px',
          color: '#1D1B20',
          // Push the selected value below the absolutely-positioned label
          // overlay, so the stack matches the design (4 px top padding within
          // the 76 px box plus the label height of about 28 px).
          marginTop: '24px',
          display: 'block',
        }}
      >
        {opt?.label ?? code}
      </Box>
    );
  };

  return (
    <Box sx={{ position: 'relative' }}>
      {/* Real <label> bound by htmlFor. The SELECT's hidden input shares the
          same id, so this label is the accessible name source for the
          combobox. It is positioned inside the bordered box, stacked over the
          value (no overlap with the trailing chevron icon thanks to the 24 px
          right inset). */}
      <Box
        component="label"
        htmlFor={id}
        id={labelId}
        sx={{
          position: 'absolute',
          top: '13px',
          left: '60px',
          right: '40px',
          fontFamily: "'Twemoji Country Flags', 'Noto Sans', sans-serif",
          fontSize: '14px',
          fontWeight: 500,
          lineHeight: '20px',
          letterSpacing: '0.10px',
          color: '#605D64',
          pointerEvents: 'none',
          zIndex: 1,
        }}
      >
        {label}
      </Box>
      <TextField
        select
        fullWidth
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value as SupportedLanguage)}
        slotProps={{
          select: {
            // aria-label here mirrors the visible label rendered above.
            // We need it AS WELL AS the htmlFor <label> below because MUI
            // forwards slotProps.select to the MuiInputBase wrapper, whereas
            // the role="combobox" element is one level deeper — the deeper
            // node picks up aria-label inheritance but not aria-labelledby
            // to an external sibling.
            'aria-label': label,
            'aria-describedby': describedById,
            native: false,
            renderValue: renderSelectedValue,
            MenuProps: {
              slotProps: {
                paper: {
                  sx: {
                    '& .MuiMenuItem-root.Mui-selected': {
                      backgroundColor: 'var(--color-primary-99, #E9EBF2)',
                    },
                    '& .MuiMenuItem-root.Mui-selected:hover': {
                      backgroundColor: 'var(--color-primary-99, #E9EBF2)',
                    },
                  },
                },
              },
            },
          },
          input: {
            startAdornment: (
              <InputAdornment position="start" sx={{ marginRight: '12px' }}>
                <Box
                  component="img"
                  src={languageIcon}
                  alt=""
                  aria-hidden
                  sx={{ width: '24px', height: '24px', display: 'block' }}
                />
              </InputAdornment>
            ),
          },
        }}
        sx={{
          // 16 px radius, 76 px height, 24 px padding, double drop shadow
          // (shadow-lg). No outline border: background and shadow only.
          '& .MuiOutlinedInput-root': {
            borderRadius: '16px',
            minHeight: 76,
            paddingInline: '24px',
            backgroundColor: '#FFFFFF',
            boxShadow:
              'inset 0 0 0 1px #E9EBF2, 0px 4px 6px -4px rgba(0, 0, 0, 0.1), 0px 10px 15px -3px rgba(0, 0, 0, 0.1)',
          },
          '& .MuiOutlinedInput-notchedOutline': {
            border: 'none',
          },
          '& .MuiSelect-icon': {
            color: '#605D64',
          },
        }}
      >
        {options.map((opt) => (
          <MenuItem key={opt.code} value={opt.code}>
            {opt.label}
          </MenuItem>
        ))}
      </TextField>
    </Box>
  );
};

export default LanguageDropdown;
