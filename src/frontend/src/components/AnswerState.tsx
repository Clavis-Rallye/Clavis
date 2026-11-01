import React from 'react';
import { Chip } from '@mui/material';
import type { ChipProps, SvgIconProps } from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import DoneAllIcon from '@mui/icons-material/DoneAll';
import HideImageIcon from '@mui/icons-material/HideImage';
import HourglassEmptyIcon from '@mui/icons-material/HourglassEmpty';
import RadioButtonUncheckedIcon from '@mui/icons-material/RadioButtonUnchecked';
import {
  ANSWER_STATE_COLOR,
  WAITING_TONAL_BG,
  WAITING_TONAL_TEXT,
} from '../utils/answerState';
import type { AnswerStateSign } from '../utils/answerState';
import { useAnswerStateName } from '../hooks/useAnswerStateName';
import type { ScoreValues } from '../hooks/useAnswerStateName';

/**
 * The one place that owns icon, colour and accessible name of an answer state.
 * No screen keeps rules of its own.
 *
 * Every sign has its own shape, so colour never carries the state alone. The
 * trophy is not part of this set: it means ranking and results only.
 */
const ICON: Record<AnswerStateSign, typeof CheckCircleIcon> = {
  open: RadioButtonUncheckedIcon,
  submitted: HourglassEmptyIcon,
  scored: CheckCircleIcon,
  stationDone: DoneAllIcon,
  mediaRemoved: HideImageIcon,
};

interface AnswerStateIconProps extends ScoreValues, Omit<SvgIconProps, 'color' | 'points'> {
  state: AnswerStateSign;
  /**
   * True where the visible text beside the icon already says the state. The
   * icon is then hidden from assistive technology, so the state is announced
   * once. Without it the icon carries the name of the state.
   */
  decorative?: boolean;
  /** True inside a chip or an alert, where the surface sets the colour. */
  inheritColor?: boolean;
}

export const AnswerStateIcon: React.FC<AnswerStateIconProps> = ({
  state,
  decorative = false,
  inheritColor = false,
  points,
  maxPoints,
  sx,
  ...rest
}) => {
  const nameOf = useAnswerStateName();
  const Icon = ICON[state];
  return (
    <Icon
      {...rest}
      titleAccess={decorative ? undefined : nameOf(state, { points, maxPoints })}
      sx={[
        { color: inheritColor ? 'inherit' : ANSWER_STATE_COLOR[state] },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
    />
  );
};

interface AnswerStateChipProps extends ScoreValues, Omit<ChipProps, 'color' | 'icon' | 'variant'> {
  /** `open` has no chip of its own: an open task shows its points. */
  state: 'submitted' | 'scored' | 'stationDone';
  /**
   * True where the label does not say the state, such as a bare "3 / 5". The
   * icon then carries the full name and the label is hidden from assistive
   * technology, so the state and the points are announced once.
   */
  nameOnIcon?: boolean;
}

/**
 * A state as a chip with its icon: blue tonal with the hourglass for
 * everything that waits for scoring, filled success for scored and for a
 * finished station.
 */
export const AnswerStateChip: React.FC<AnswerStateChipProps> = ({
  state,
  nameOnIcon = false,
  points,
  maxPoints,
  label,
  sx,
  ...rest
}) => {
  const waiting = state === 'submitted';
  return (
    <Chip
      size="small"
      {...rest}
      color={waiting ? 'default' : 'success'}
      icon={
        <AnswerStateIcon
          state={state}
          decorative={!nameOnIcon}
          inheritColor
          points={points}
          maxPoints={maxPoints}
        />
      }
      label={nameOnIcon ? <span aria-hidden="true">{label}</span> : label}
      sx={
        waiting
          ? [
              {
                bgcolor: WAITING_TONAL_BG,
                color: WAITING_TONAL_TEXT,
                // MUI gives the icon of a default chip its own grey.
                '& .MuiChip-icon': { color: 'inherit' },
              },
              ...(Array.isArray(sx) ? sx : [sx]),
            ]
          : sx
      }
    />
  );
};
