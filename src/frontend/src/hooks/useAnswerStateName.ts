import { useTranslation } from 'react-i18next';
import { ANSWER_STATE_NAME_KEY } from '../utils/answerState';
import type { AnswerStateSign } from '../utils/answerState';

export interface ScoreValues {
  /** Only the scored state names its points. */
  points?: number;
  maxPoints?: number;
}

/** The screen reader name of a state sign, in the language of the page. */
export const useAnswerStateName = () => {
  const { t } = useTranslation('runs');
  return (state: AnswerStateSign, { points = 0, maxPoints = 0 }: ScoreValues = {}): string =>
    t(ANSWER_STATE_NAME_KEY[state], { points, maxPoints });
};
