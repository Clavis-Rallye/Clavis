/**
 * Turning a failed submission call into what the form shows and does.
 *
 * The backend's `detail` is a single-language literal, so shown raw it reaches
 * every participant whatever language they picked. The six challenge forms
 * share this mapping, so they cannot drift apart.
 */
import i18n from '../../i18n';
import { ApiError } from '../../api/client';

/** What the form's error alert should say, and whether the view must re-fetch. */
interface SubmissionErrorView {
  message: string;
  /**
   * True when the server no longer matches the screen: a teammate submitted
   * first, or the run left the active state. The view then reloads.
   */
  reload: boolean;
  /**
   * True when a teammate's answer took the place of this one. The view then
   * says so and keeps what this member tried to send.
   */
  teammate: boolean;
}

// The coded answers of the submission and upload endpoints that this build
// understands. An upload refusal stored nothing, so the team tries again.
const CODES: Record<string, { key: string; reload: boolean; teammate?: boolean }> = {
  TEAM_ALREADY_SUBMITTED: {
    key: 'participant.challenge.errors.teamAlreadySubmitted',
    reload: true,
    teammate: true,
  },
  // 413: the pixel dimensions of the photo exceed the decode cap.
  IMAGE_TOO_LARGE: {
    key: 'participant.challenge.errors.imageTooLarge',
    reload: false,
  },
  // 415: the file is no picture the server can decode.
  IMAGE_NOT_DECODABLE: {
    key: 'participant.challenge.errors.imageNotDecodable',
    reload: false,
  },
  // 422: the server removes the metadata of a video or an audio file and
  // refuses a container it cannot read.
  MEDIA_NOT_READABLE: {
    key: 'participant.challenge.errors.mediaNotReadable',
    reload: false,
  },
  // The run left the active state while the form was open. Reloading lets
  // the view swap the form for the matching notice.
  RUN_ENDED: { key: 'participant.challenge.errors.runEnded', reload: true },
  RUN_PAUSED: { key: 'participant.challenge.errors.runPaused', reload: true },
  RUN_NOT_STARTED: { key: 'participant.challenge.errors.runNotStarted', reload: true },
};

// A code this build does not know still deserves a translated answer.
const GENERIC_KEY = 'participant.challenge.errors.generic';

export function mapSubmissionError(err: unknown): SubmissionErrorView {
  if (!(err instanceof ApiError)) {
    return { message: String(err), reload: false, teammate: false };
  }
  if (!err.errorCode) {
    // An uncoded 422 keeps its detail: the API client already localizes
    // validation details. Any other uncoded answer would be the API's English
    // text, so it gets the generic translated message.
    return {
      message: err.status === 422 ? err.detail : i18n.t(`runs:${GENERIC_KEY}`),
      reload: false,
      teammate: false,
    };
  }
  const coded = CODES[err.errorCode];
  if (!coded) {
    return { message: i18n.t(`runs:${GENERIC_KEY}`), reload: false, teammate: false };
  }
  return {
    message: i18n.t(`runs:${coded.key}`),
    reload: coded.reload,
    teammate: coded.teammate ?? false,
  };
}
