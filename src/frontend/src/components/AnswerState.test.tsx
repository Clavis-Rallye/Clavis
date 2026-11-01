import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ThemeProvider } from '@mui/material/styles';
import { axe } from 'vitest-axe';
import theme from '../theme';
import '../i18n';
import { AnswerStateChip, AnswerStateIcon } from './AnswerState';
import {
  ANSWER_STATE_SIGNS,
  STATION_MARKER_COLOR,
  WAITING_TONAL_BG,
  deriveAnswerState,
} from '../utils/answerState';
import type { AnswerStateSign } from '../utils/answerState';

const withTheme = (ui: React.ReactElement) => render(<ThemeProvider theme={theme}>{ui}</ThemeProvider>);

// Icon, colour and name per sign.
const MODEL: Record<AnswerStateSign, { testId: string; color: string; name: string }> = {
  open: { testId: 'RadioButtonUncheckedIcon', color: theme.palette.text.secondary, name: 'Offen' },
  submitted: {
    testId: 'HourglassEmptyIcon',
    color: theme.palette.primary.main,
    name: 'Eingereicht, wartet auf Bewertung',
  },
  scored: {
    testId: 'CheckCircleIcon',
    color: theme.palette.success.main,
    name: 'Bewertet, 3 von 5 Punkten',
  },
  stationDone: {
    testId: 'DoneAllIcon',
    color: theme.palette.success.main,
    name: 'Station abgeschlossen',
  },
  mediaRemoved: {
    testId: 'HideImageIcon',
    color: theme.palette.text.secondary,
    name: 'Medium entfernt',
  },
};

describe('deriveAnswerState', () => {
  it('derives the state from what exists, because an answer has no status field', () => {
    expect(deriveAnswerState(undefined)).toBe('open');
    expect(deriveAnswerState(null)).toBe('open');
    expect(deriveAnswerState({})).toBe('submitted');
    expect(deriveAnswerState({ is_scored: false })).toBe('submitted');
    expect(deriveAnswerState({ is_scored: true })).toBe('scored');
  });
});

describe('the colours of the state model', () => {
  it('uses the approved values of the theme', () => {
    expect(theme.palette.text.secondary).toBe('#49454F');
    expect(theme.palette.primary.main).toBe('#1D47A4');
    expect(theme.palette.primary.dark).toBe('#002378');
    expect(theme.palette.success.main).toBe('#2e7d32');
    expect(WAITING_TONAL_BG).toBe('#E4EAF7');
  });

  it('gives the map marker the blue and the green of the theme, and its line the secondary text colour', () => {
    expect(STATION_MARKER_COLOR).toEqual({ open: '#1D47A4', done: '#2e7d32', leader: '#49454F' });
  });
});

describe('AnswerStateIcon', () => {
  it.each(ANSWER_STATE_SIGNS)('%s: carries its icon, its colour and its name', (state) => {
    withTheme(<AnswerStateIcon state={state} points={3} maxPoints={5} />);

    const icon = screen.getByRole('img', { name: MODEL[state].name });
    expect(icon).toHaveAttribute('data-testid', MODEL[state].testId);
    expect(icon).not.toHaveAttribute('aria-hidden');
    expect(icon).toHaveStyle({ color: MODEL[state].color });
  });

  it.each(ANSWER_STATE_SIGNS)('%s: a decorative icon is hidden and has no name', (state) => {
    withTheme(<AnswerStateIcon state={state} decorative />);

    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    const icon = screen.getByTestId(MODEL[state].testId);
    expect(icon).toHaveAttribute('aria-hidden', 'true');
    expect(icon.querySelector('title')).toBeNull();
    // Hidden from assistive technology, still drawn in the colour of the state.
    expect(icon).toHaveStyle({ color: MODEL[state].color });
  });

  it('never draws the trophy, which means ranking only', () => {
    withTheme(
      <>
        {ANSWER_STATE_SIGNS.map((state) => (
          <AnswerStateIcon key={state} state={state} />
        ))}
      </>,
    );
    expect(screen.queryByTestId('EmojiEventsIcon')).not.toBeInTheDocument();
  });
});

describe('AnswerStateChip', () => {
  it('submitted: the blue tonal chip with the hourglass', () => {
    withTheme(<AnswerStateChip state="submitted" label="Unbewertet" />);

    const chip = screen.getByText('Unbewertet').closest('.MuiChip-root');
    expect(chip).toHaveStyle({
      backgroundColor: WAITING_TONAL_BG,
      color: theme.palette.primary.dark,
    });
    const icon = screen.getByTestId('HourglassEmptyIcon');
    expect(chip).toContainElement(icon);
    // The label says the state, so the icon stays silent.
    expect(icon).toHaveAttribute('aria-hidden', 'true');
  });

  it('scored: the filled success chip with the tick', () => {
    withTheme(<AnswerStateChip state="scored" label="Bewertet · 3/5" />);

    const chip = screen.getByText('Bewertet · 3/5').closest('.MuiChip-root');
    expect(chip).toHaveClass('MuiChip-colorSuccess');
    expect(chip).toHaveStyle({
      backgroundColor: theme.palette.success.main,
      color: theme.palette.success.contrastText,
    });
    expect(screen.getByTestId('CheckCircleIcon')).toHaveAttribute('aria-hidden', 'true');
  });

  it('stationDone: the filled success chip with the double tick', () => {
    withTheme(<AnswerStateChip state="stationDone" label="3 von 3 Aufgaben" />);

    const chip = screen.getByText('3 von 3 Aufgaben').closest('.MuiChip-root');
    expect(chip).toHaveClass('MuiChip-colorSuccess');
    expect(screen.getByTestId('DoneAllIcon')).toHaveAttribute('aria-hidden', 'true');
  });

  it('nameOnIcon: a bare "3 / 5" is announced once, through the name of the icon', () => {
    withTheme(
      <AnswerStateChip state="scored" nameOnIcon points={3} maxPoints={5} label="3 / 5" />,
    );

    expect(screen.getByRole('img', { name: 'Bewertet, 3 von 5 Punkten' })).toHaveAttribute(
      'data-testid',
      'CheckCircleIcon',
    );
    expect(screen.getByText('3 / 5')).toHaveAttribute('aria-hidden', 'true');
  });

  it('has no axe violations in any state', async () => {
    const { container } = withTheme(
      <main>
        {ANSWER_STATE_SIGNS.map((state) => (
          <AnswerStateIcon key={state} state={state} points={3} maxPoints={5} />
        ))}
        <AnswerStateChip state="submitted" label="Eingereicht" />
        <AnswerStateChip state="scored" label="Bewertet · 3/5" />
        <AnswerStateChip state="scored" nameOnIcon points={3} maxPoints={5} label="3 / 5" />
        <AnswerStateChip state="stationDone" label="3 von 3 Aufgaben" />
      </main>,
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});
