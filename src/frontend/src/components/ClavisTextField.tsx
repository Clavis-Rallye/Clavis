import React, { forwardRef, useState } from 'react';
import TextField, { type TextFieldProps } from '@mui/material/TextField';
import IconButton from '@mui/material/IconButton';
import InputAdornment from '@mui/material/InputAdornment';
import Box from '@mui/material/Box';
import Visibility from '@mui/icons-material/Visibility';
import VisibilityOff from '@mui/icons-material/VisibilityOff';

import cancelIcon from '../assets/icons/cancel.svg';

type ClavisTextFieldProps = Omit<TextFieldProps, 'InputProps'> & {
  onClear?: () => void;
  clearAriaLabel?: string;
  /**
   * When true on a `type="password"` field, render an eye/eye-off toggle
   * that flips the visible characters between masked and plain. It replaces
   * the clear-X adornment, because only one trailing control fits.
   */
  passwordToggle?: boolean;
  showPasswordAriaLabel?: string;
  hidePasswordAriaLabel?: string;
};

const ClavisTextField = forwardRef<HTMLDivElement, ClavisTextFieldProps>(
  function ClavisTextField(
    {
      onClear,
      clearAriaLabel = 'Eingabe löschen',
      passwordToggle,
      showPasswordAriaLabel = 'Passwort anzeigen',
      hidePasswordAriaLabel = 'Passwort verbergen',
      value,
      onChange,
      slotProps,
      sx: callerSx,
      type,
      ...rest
    },
    ref,
  ) {
    const hasValue =
      value !== undefined && value !== null && String(value).length > 0;

    const [showPassword, setShowPassword] = useState(false);
    const isPasswordWithToggle = passwordToggle && type === 'password';
    const effectiveType = isPasswordWithToggle && showPassword ? 'text' : type;

    const handleClear = () => {
      if (onClear) {
        onClear();
        return;
      }
      if (onChange) {
        const synthetic = {
          target: { value: '' },
          currentTarget: { value: '' },
        } as unknown as React.ChangeEvent<HTMLInputElement>;
        onChange(synthetic);
      }
    };

    const endAdornment = isPasswordWithToggle ? (
      <InputAdornment position="end">
        <IconButton
          aria-label={showPassword ? hidePasswordAriaLabel : showPasswordAriaLabel}
          onClick={() => setShowPassword((s) => !s)}
          edge="end"
          size="small"
          // The toggle shows no focus ring by default (outline: none). This
          // rule gives a keyboard user a visible focus.
          sx={{
            '&.Mui-focusVisible': {
              outline: '2px solid',
              outlineColor: 'primary.main',
              outlineOffset: '2px',
            },
          }}
        >
          {showPassword ? <VisibilityOff fontSize="small" /> : <Visibility fontSize="small" />}
        </IconButton>
      </InputAdornment>
    ) : hasValue ? (
      <InputAdornment position="end">
        <IconButton
          aria-label={clearAriaLabel}
          tabIndex={-1}
          onClick={handleClear}
          edge="end"
          size="small"
        >
          <Box
            component="img"
            src={cancelIcon}
            alt=""
            aria-hidden
            sx={{ width: '24px', height: '24px', display: 'block' }}
          />
        </IconButton>
      </InputAdornment>
    ) : null;

    // WCAG 2.1 SC 4.1.3 (Status Messages, AA): a validation or submit error
    // must reach assistive technology without receiving focus. The helper text
    // carries the message and is associated via aria-describedby. On its own
    // it is only spoken when the field has focus, so a screen-reader user who
    // submits a form hears nothing about the failure. A live region on the
    // helper text announces the error the moment it appears.
    const mergedSlotProps = {
      ...(slotProps ?? {}),
      input: {
        ...((slotProps as { input?: Record<string, unknown> })?.input ?? {}),
        endAdornment,
      },
      formHelperText: {
        ...((slotProps as { formHelperText?: Record<string, unknown> })?.formHelperText ?? {}),
        ...(rest.error ? { role: 'alert' as const } : {}),
      },
    };

    const baseSx = {
      // Stroke #605D64 at 1px, not the MUI default rgba(0,0,0,0.23). The
      // selector adds .MuiOutlinedInput-root as a middle class, so its
      // specificity beats the theme default `.MuiOutlinedInput-root
      // .MuiOutlinedInput-notchedOutline` (#CAC4D0). borderWidth is set to 1px
      // for every state.
      '& .MuiOutlinedInput-root .MuiOutlinedInput-notchedOutline': {
        borderColor: '#605D64',
        borderWidth: '1px',
      },
      '& .MuiOutlinedInput-root:hover .MuiOutlinedInput-notchedOutline': {
        borderColor: '#605D64',
        borderWidth: '1px',
      },
      // Supporting text: Roboto 12/400/16, letter spacing 0.4, #605D64.
      // fontFamily is explicit because the MUI caption variant ('Twemoji
      // Country Flags', 'Noto Sans', sans-serif) would otherwise cascade over
      // Roboto.
      '& .MuiFormHelperText-root': {
        fontFamily: "'Roboto', sans-serif",
        color: '#605D64',
        letterSpacing: '0.4px',
      },
    };

    // Compose sx as an array so caller-supplied function / array sx values
    // survive intact (spreading them into an object would drop functions and
    // turn array entries into numeric keys).
    const composedSx = Array.isArray(callerSx)
      ? [baseSx, ...callerSx]
      : callerSx
        ? [baseSx, callerSx]
        : baseSx;

    return (
      <TextField
        ref={ref}
        value={value}
        onChange={onChange}
        type={effectiveType}
        slotProps={mergedSlotProps}
        sx={composedSx}
        {...rest}
      />
    );
  },
);

export default ClavisTextField;
