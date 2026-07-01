import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AudioChallengeForm from './AudioChallengeForm';
import { uploadFile } from '../../api/files';
import { ApiError } from '../../api/client';
import type { RunChallenge, Submission } from '../../api/types';

vi.mock('../../api/files', () => ({
  uploadFile: vi.fn(),
}));

const challenge: RunChallenge = {
  id: 'ch-1',
  organization_id: 'org-1',
  run_id: 'run-1',
  run_station_id: 's-1',
  source_challenge_id: null,
  challenge_type: 'audio',
  title: { de: 'Sprachaufgabe' },
  instructions: { de: 'Nimm eine Sprachnachricht auf.' },
  scoring_rules: { max_points: 10 },
  sort_order: 0,
  time_estimate_minutes: null,
  suggested_solution: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const submissionResult: Submission = {
  id: 'sub-1',
  organization_id: 'org-1',
  run_id: 'run-1',
  team_id: 'team-1',
  run_challenge_id: 'ch-1',
  content: { audio_url: 'org-1/run-1/team-1/abc_voice.webm' },
  submitted_at: '2026-01-01T00:00:00Z',
  is_scored: false,
  points: null,
  max_points: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

describe('AudioChallengeForm', () => {
  beforeAll(() => {
    // jsdom doesn't implement these — the component revokes/creates object
    // URLs for the audio preview.
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock-audio-url');
    globalThis.URL.revokeObjectURL = vi.fn();
  });

  beforeEach(() => {
    vi.mocked(uploadFile).mockReset();
  });

  it('renders record and library buttons', () => {
    render(
      <AudioChallengeForm
        orgSlug="test-org"
        runId="run-1"
        challenge={challenge}
        alreadySubmitted={false}
        onSubmitted={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Sprachnachricht aufnehmen' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Audiodatei wählen' })).toBeInTheDocument();
    // Submit disabled until a recording is chosen.
    expect(screen.getByRole('button', { name: 'Antwort senden' })).toBeDisabled();
  });

  it('hands the phone recorder the job on the record input', () => {
    const { container } = render(
      <AudioChallengeForm
        orgSlug="test-org"
        runId="run-1"
        challenge={challenge}
        alreadySubmitted={false}
        onSubmitted={vi.fn()}
      />,
    );

    const [recordInput, libraryInput] = container.querySelectorAll('input[type="file"]');
    expect(recordInput).toHaveAttribute('accept', 'audio/*');
    expect(recordInput).toHaveAttribute('capture');
    // Picking an existing file must not open the recorder.
    expect(libraryInput).toHaveAttribute('accept', 'audio/*');
    expect(libraryInput).not.toHaveAttribute('capture');
  });

  it('shows a preview after choosing a file and submits audio_url via uploadFile', async () => {
    vi.mocked(uploadFile).mockResolvedValue({
      object_key: 'org-1/run-1/team-1/abc_voice.webm',
      url: 'https://example.com/presigned',
      content_type: 'audio/webm',
      size: 123,
    });
    const onSubmitted = vi.fn();
    const submitAction = vi.fn().mockResolvedValue(submissionResult);

    const { container } = render(
      <AudioChallengeForm
        orgSlug="test-org"
        runId="run-1"
        challenge={challenge}
        alreadySubmitted={false}
        onSubmitted={onSubmitted}
        submitAction={submitAction}
      />,
    );

    const file = new File(['voice-bytes'], 'voice.webm', { type: 'audio/webm' });
    // Library input has no accessible name (hidden file input) — select by type=file.
    const [, libraryInput] = container.querySelectorAll('input[type="file"]');
    await userEvent.upload(libraryInput as HTMLInputElement, file);

    expect(container.querySelector('audio')).toBeInTheDocument();

    const submitButton = screen.getByRole('button', { name: 'Antwort senden' });
    expect(submitButton).not.toBeDisabled();
    await userEvent.click(submitButton);

    await waitFor(() => {
      expect(uploadFile).toHaveBeenCalledWith('test-org', 'run-1', file, expect.any(Function));
    });
    await waitFor(() => {
      expect(submitAction).toHaveBeenCalledWith({ audio_url: 'org-1/run-1/team-1/abc_voice.webm' });
    });
    await waitFor(() => {
      expect(onSubmitted).toHaveBeenCalledWith(submissionResult);
    });
  });

  // The real audio form: a failed send is spoken once, the focus is not on BODY,
  // and the picked file stays, so a retry needs no new input. A unit test cannot hear,
  // so these tests check the structure that would make a screen reader speak twice.
  // Only a failed send heard with a screen reader (NVDA) proves it.
  it('no answer from the server (status 0): spoken once, focus not on BODY, the file stays', async () => {
    vi.mocked(uploadFile).mockResolvedValue({
      object_key: 'org-1/run-1/team-1/abc_voice.webm',
      url: 'https://example.com/presigned',
      content_type: 'audio/webm',
      size: 123,
    });
    const submitAction = vi.fn().mockRejectedValueOnce(new ApiError(0, 'Network Error'));
    const text = 'Die Antwort konnte nicht gesendet werden. Bitte versuche es erneut.';

    const { container } = render(
      <AudioChallengeForm
        orgSlug="test-org"
        runId="run-1"
        challenge={challenge}
        alreadySubmitted={false}
        onSubmitted={vi.fn()}
        submitAction={submitAction}
      />,
    );

    const file = new File(['bytes'], 'voice.webm', { type: 'audio/webm' });
    const [, libraryInput] = container.querySelectorAll('input[type="file"]');
    await userEvent.upload(libraryInput as HTMLInputElement, file);
    await userEvent.click(screen.getByRole('button', { name: 'Antwort senden' }));

    await screen.findAllByText(text);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Antwort senden' })).toBeEnabled());
    // (a) one announcing path: the form's alert; nothing else is live or focused with it.
    const live = Array.from(
      document.querySelectorAll('[role="alert"], [role="status"], [role="log"], [aria-live]'),
    ).filter((el) => el.getAttribute('aria-live') !== 'off' && (el.textContent ?? '').includes(text));
    expect(live).toHaveLength(1);
    const focused = document.activeElement as HTMLElement;
    // (b) the focus is not on BODY, and not in or around the alert.
    expect(focused).not.toBe(document.body);
    expect(focused.contains(live[0])).toBe(false);
    expect(live[0].contains(focused)).toBe(false);
    expect(focused).toHaveTextContent('Antwort senden');
    // (c) the picked file stays: the preview is still there and a retry needs no new input.
    expect(container.querySelector('audio')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Antwort senden' }));
    await waitFor(() => expect(submitAction).toHaveBeenCalledTimes(2));
    expect(uploadFile).toHaveBeenLastCalledWith('test-org', 'run-1', file, expect.any(Function));
  });
});
