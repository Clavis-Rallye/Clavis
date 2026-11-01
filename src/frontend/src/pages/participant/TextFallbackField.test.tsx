import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type React from 'react';
import PhotoChallengeForm from './PhotoChallengeForm';
import VideoChallengeForm from './VideoChallengeForm';
import AudioChallengeForm from './AudioChallengeForm';
import type { ChallengeFormProps } from './TextChallengeForm';
import { uploadFile } from '../../api/files';
import type { RunChallenge, Submission } from '../../api/types';

vi.mock('../../api/files', () => ({
  uploadFile: vi.fn(),
}));

// A text answer is always possible as a fallback when photo, video or audio do
// not work.

const challenge = (type: RunChallenge['challenge_type']): RunChallenge => ({
  id: 'ch-1',
  organization_id: 'org-1',
  run_id: 'run-1',
  run_station_id: 's-1',
  source_challenge_id: null,
  challenge_type: type,
  title: { de: 'Aufgabe' },
  instructions: { de: 'Mach etwas.' },
  scoring_rules: { max_points: 10 },
  sort_order: 0,
  time_estimate_minutes: null,
  suggested_solution: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
});

const result = { id: 'sub-1', run_challenge_id: 'ch-1', content: {} } as unknown as Submission;

const FORMS: [string, React.FC<ChallengeFormProps>, RunChallenge['challenge_type'], string, string, string][] = [
  ['photo', PhotoChallengeForm, 'photo', 'Foto klappt nicht? Dann antworte mit Text.', 'photo_url', 'image/png'],
  ['video', VideoChallengeForm, 'video', 'Video klappt nicht? Dann antworte mit Text.', 'video_url', 'video/mp4'],
  ['audio', AudioChallengeForm, 'audio', 'Aufnahme klappt nicht? Dann antworte mit Text.', 'audio_url', 'audio/webm'],
];

describe('the text fallback of a media task', () => {
  beforeAll(() => {
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock');
    globalThis.URL.revokeObjectURL = vi.fn();
  });
  beforeEach(() => vi.mocked(uploadFile).mockReset());

  it.each(FORMS)('%s: sends a text alone, with no upload', async (_name, Form, type, openLabel) => {
    const submitAction = vi.fn().mockResolvedValue(result);
    render(
      <Form
        orgSlug="test-org"
        runId="run-1"
        challenge={challenge(type)}
        alreadySubmitted={false}
        onSubmitted={vi.fn()}
        submitAction={submitAction}
      />,
    );
    const send = screen.getByRole('button', { name: 'Antwort senden' });
    expect(send).toBeDisabled();

    await userEvent.click(screen.getByRole('button', { name: openLabel }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Deine Antwort als Text' }), 'Die Kamera geht nicht.');
    expect(send).not.toBeDisabled();
    await userEvent.click(send);

    await waitFor(() => expect(submitAction).toHaveBeenCalledWith({ text: 'Die Kamera geht nicht.' }));
    expect(uploadFile).not.toHaveBeenCalled();
  });

  it.each(FORMS)('%s: sends the medium and the text together', async (_name, Form, type, openLabel, field, mime) => {
    vi.mocked(uploadFile).mockResolvedValue({
      object_key: 'org-1/run-1/team-1/abc_file',
      url: 'https://example.com/presigned',
      content_type: 'application/octet-stream',
      size: 1,
    });
    const submitAction = vi.fn().mockResolvedValue(result);
    const { container } = render(
      <Form
        orgSlug="test-org"
        runId="run-1"
        challenge={challenge(type)}
        alreadySubmitted={false}
        onSubmitted={vi.fn()}
        submitAction={submitAction}
      />,
    );
    const [, libraryInput] = container.querySelectorAll('input[type="file"]');
    await userEvent.upload(libraryInput as HTMLInputElement, new File(['x'], 'f.bin', { type: mime }));
    await userEvent.click(screen.getByRole('button', { name: openLabel }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Deine Antwort als Text' }), 'Dazu ein Satz.');
    await userEvent.click(screen.getByRole('button', { name: 'Antwort senden' }));

    await waitFor(() =>
      expect(submitAction).toHaveBeenCalledWith({ [field]: 'org-1/run-1/team-1/abc_file', text: 'Dazu ein Satz.' }),
    );
  });

  it('opens with the text of the answer being replaced', () => {
    render(
      <PhotoChallengeForm
        orgSlug="test-org"
        runId="run-1"
        challenge={challenge('photo')}
        alreadySubmitted={false}
        onSubmitted={vi.fn()}
        submitAction={vi.fn()}
        initialContent={{ text: 'Unsere alte Antwort' }}
      />,
    );
    expect(screen.getByRole('textbox', { name: 'Deine Antwort als Text' })).toHaveValue('Unsere alte Antwort');
    expect(screen.getByRole('button', { name: 'Antwort senden' })).not.toBeDisabled();
  });
});
