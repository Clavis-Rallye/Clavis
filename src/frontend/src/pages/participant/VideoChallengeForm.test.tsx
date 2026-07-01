import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import VideoChallengeForm from './VideoChallengeForm';
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
  challenge_type: 'video',
  title: { de: 'Videoaufgabe' },
  instructions: { de: 'Nimm ein Video auf.' },
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
  content: { video_url: 'org-1/run-1/team-1/abc_clip.mp4' },
  submitted_at: '2026-01-01T00:00:00Z',
  is_scored: false,
  points: null,
  max_points: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

describe('VideoChallengeForm', () => {
  beforeAll(() => {
    // jsdom doesn't implement these — the component revokes/creates object
    // URLs for the video preview.
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock-video-url');
    globalThis.URL.revokeObjectURL = vi.fn();
  });

  beforeEach(() => {
    vi.mocked(uploadFile).mockReset();
  });

  it('renders capture and gallery buttons', () => {
    render(
      <VideoChallengeForm
        orgSlug="test-org"
        runId="run-1"
        challenge={challenge}
        alreadySubmitted={false}
        onSubmitted={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Video aufnehmen' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Aus Galerie wählen' })).toBeInTheDocument();
    // Submit disabled until a file is chosen.
    expect(screen.getByRole('button', { name: 'Antwort senden' })).toBeDisabled();
  });

  it('shows a preview after choosing a file and submits video_url via uploadFile', async () => {
    vi.mocked(uploadFile).mockResolvedValue({
      object_key: 'org-1/run-1/team-1/abc_clip.mp4',
      url: 'https://example.com/presigned',
      content_type: 'video/mp4',
      size: 123,
    });
    const onSubmitted = vi.fn();
    const submitAction = vi.fn().mockResolvedValue(submissionResult);

    const { container } = render(
      <VideoChallengeForm
        orgSlug="test-org"
        runId="run-1"
        challenge={challenge}
        alreadySubmitted={false}
        onSubmitted={onSubmitted}
        submitAction={submitAction}
      />,
    );

    const file = new File(['clip-bytes'], 'clip.mp4', { type: 'video/mp4' });
    // Gallery input has no accessible name (hidden file input) — select by type=file.
    const [, galleryInput] = container.querySelectorAll('input[type="file"]');
    await userEvent.upload(galleryInput as HTMLInputElement, file);

    // Preview video element appears.
    expect(container.querySelector('video')).toBeInTheDocument();

    const submitButton = screen.getByRole('button', { name: 'Antwort senden' });
    expect(submitButton).not.toBeDisabled();
    await userEvent.click(submitButton);

    await waitFor(() => {
      expect(uploadFile).toHaveBeenCalledWith('test-org', 'run-1', file, expect.any(Function));
    });
    await waitFor(() => {
      expect(submitAction).toHaveBeenCalledWith({ video_url: 'org-1/run-1/team-1/abc_clip.mp4' });
    });
    await waitFor(() => {
      expect(onSubmitted).toHaveBeenCalledWith(submissionResult);
    });
  });

  // The coded duplicate refusal becomes a translated message, and the form asks
  // the view to reload so the teammate's answer appears.
  it('shows the translated duplicate message and reloads the view on TEAM_ALREADY_SUBMITTED', async () => {
    vi.mocked(uploadFile).mockResolvedValue({
      object_key: 'org-1/run-1/team-1/abc_clip.mp4',
      url: 'https://example.com/presigned',
      content_type: 'video/mp4',
      size: 123,
    });
    const onSubmitted = vi.fn();
    const onReload = vi.fn();
    const submitAction = vi.fn().mockRejectedValue(
      new ApiError(
        422,
        'Dein Team hat diese Challenge bereits beantwortet.',
        'TEAM_ALREADY_SUBMITTED',
      ),
    );

    const { container } = render(
      <VideoChallengeForm
        orgSlug="test-org"
        runId="run-1"
        challenge={challenge}
        alreadySubmitted={false}
        onSubmitted={onSubmitted}
        submitAction={submitAction}
        onReload={onReload}
      />,
    );

    const file = new File(['clip-bytes'], 'clip.mp4', { type: 'video/mp4' });
    const [, galleryInput] = container.querySelectorAll('input[type="file"]');
    await userEvent.upload(galleryInput as HTMLInputElement, file);
    await userEvent.click(screen.getByRole('button', { name: 'Antwort senden' }));

    expect(
      await screen.findByText(
        'Ein anderes Teammitglied hat diese Aufgabe bereits beantwortet. Die Antwort eures Teams wird angezeigt.',
      ),
    ).toBeInTheDocument();
    // The server's single-language detail never reaches the participant.
    expect(
      screen.queryByText('Dein Team hat diese Challenge bereits beantwortet.'),
    ).not.toBeInTheDocument();
    expect(onReload).toHaveBeenCalledTimes(1);
    expect(onSubmitted).not.toHaveBeenCalled();
    // The failed submit re-enables the form.
    expect(screen.getByRole('button', { name: 'Antwort senden' })).toBeEnabled();
  });

  // The real video form: a failed send is spoken once, the focus is not on BODY,
  // and the picked file stays, so a retry needs no new input. A unit test cannot hear,
  // so these tests check the structure that would make a screen reader speak twice.
  // Only a failed send heard with a screen reader (NVDA) proves it.
  it('no answer from the server (status 0): spoken once, focus not on BODY, the file stays', async () => {
    vi.mocked(uploadFile).mockResolvedValue({
      object_key: 'org-1/run-1/team-1/abc_clip.mp4',
      url: 'https://example.com/presigned',
      content_type: 'video/mp4',
      size: 123,
    });
    const submitAction = vi.fn().mockRejectedValueOnce(new ApiError(0, 'Network Error'));
    const text = 'Die Antwort konnte nicht gesendet werden. Bitte versuche es erneut.';

    const { container } = render(
      <VideoChallengeForm
        orgSlug="test-org"
        runId="run-1"
        challenge={challenge}
        alreadySubmitted={false}
        onSubmitted={vi.fn()}
        submitAction={submitAction}
      />,
    );

    const file = new File(['bytes'], 'clip.mp4', { type: 'video/mp4' });
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
    expect(container.querySelector('video')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Antwort senden' }));
    await waitFor(() => expect(submitAction).toHaveBeenCalledTimes(2));
    expect(uploadFile).toHaveBeenLastCalledWith('test-org', 'run-1', file, expect.any(Function));
  });
});
