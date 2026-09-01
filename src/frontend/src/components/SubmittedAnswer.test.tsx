import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import SubmittedAnswer from './SubmittedAnswer';
import '../i18n';

// Media elements (audio/video) carry no ARIA role jsdom recognizes, so those
// two assertions query the container directly — same pattern as the media
// challenge form tests.
describe('SubmittedAnswer', () => {
  it('renders nothing without content', () => {
    const { container } = render(<SubmittedAnswer content={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing for content it has no representation for', () => {
    const { container } = render(<SubmittedAnswer content={{ unrecognized_key: 'x' }} />);
    expect(container).toBeEmptyDOMElement();
  });

  // In the conflict view the text that was not sent must not carry "Eure Antwort",
  // the label of the team's valid answer.
  it('leaves the caption out when the context names the content', () => {
    render(<SubmittedAnswer content={{ text: 'Gedicht vom Laptop' }} caption={null} />);
    expect(screen.getByText('Gedicht vom Laptop')).toBeInTheDocument();
    expect(screen.queryByText('Eure Antwort')).not.toBeInTheDocument();
  });

  it('shows a text answer under the "Eure Antwort" caption', () => {
    render(<SubmittedAnswer content={{ text: 'Das Rathaus ist von 1615.' }} />);
    expect(screen.getByText('Eure Antwort')).toBeInTheDocument();
    expect(screen.getByText('Das Rathaus ist von 1615.')).toBeInTheDocument();
  });

  it('shows the chosen quiz option', () => {
    render(<SubmittedAnswer content={{ selected_option: 'Augsburg' }} />);
    expect(screen.getByText('Augsburg')).toBeInTheDocument();
  });

  // The backend stores QR answers under `scanned_value`, so the
  // component must read that key.
  it('shows the scanned QR value', () => {
    render(<SubmittedAnswer content={{ scanned_value: 'clavis-station-7' }} />);
    expect(screen.getByText('clavis-station-7')).toBeInTheDocument();
  });

  // The team's own answer loads the display variant, never the
  // full-resolution original.
  it('shows a submitted photo through its display variant', () => {
    render(<SubmittedAnswer content={{ photo_url: 'org-1/runs/run-1/photo.jpg' }} />);
    const img = screen.getByRole('img');
    expect(img).toHaveAttribute(
      'src',
      expect.stringContaining('/api/files/org-1/runs/run-1/photo.jpg?variant=display'),
    );
    expect(img).toHaveAttribute('alt', 'Eure Antwort');
  });

  it('shows a submitted audio recording', () => {
    const { container } = render(
      <SubmittedAnswer content={{ audio_url: 'org-1/runs/run-1/message.webm' }} />,
    );
    const audio = container.querySelector('audio');
    expect(audio).toBeInTheDocument();
    expect(audio).toHaveAttribute(
      'src',
      expect.stringContaining('/api/files/org-1/runs/run-1/message.webm'),
    );
  });

  // A player without a name is "audio" or "video" to a screen reader.
  it('names the audio and the video player', () => {
    const { container } = render(
      <SubmittedAnswer content={{ audio_url: 'org-1/a.webm', video_url: 'org-1/b.mp4' }} />,
    );
    expect(container.querySelector('audio')).toHaveAttribute('aria-label', 'Eure Audio-Antwort');
    expect(container.querySelector('video')).toHaveAttribute('aria-label', 'Eure Video-Antwort');
  });

  it('shows a submitted video', () => {
    const { container } = render(
      <SubmittedAnswer content={{ video_url: 'org-1/runs/run-1/clip.mp4' }} />,
    );
    const video = container.querySelector('video');
    expect(video).toBeInTheDocument();
    expect(video).toHaveAttribute(
      'src',
      expect.stringContaining('/api/files/org-1/runs/run-1/clip.mp4'),
    );
  });

  // After the org-admin media purge the medium is gone and the
  // content carries `media_removed` with the reference nulled. The playback
  // says so instead of loading a broken medium.
  it('says "Medium entfernt" for a purged answer and loads no medium', () => {
    const { container } = render(
      <SubmittedAnswer content={{ photo_url: null, media_removed: true }} />,
    );
    expect(screen.getByText('Eure Antwort')).toBeInTheDocument();
    // The text names the state, the icon in front is hidden.
    expect(
      screen.getByText('Medium entfernt').querySelector('[data-testid="HideImageIcon"]'),
    ).toHaveAttribute('aria-hidden', 'true');
    expect(container.querySelector('img')).not.toBeInTheDocument();
    expect(container.querySelector('audio')).not.toBeInTheDocument();
    expect(container.querySelector('video')).not.toBeInTheDocument();
  });

  it('keeps showing a purged answer next to surviving text', () => {
    render(
      <SubmittedAnswer content={{ text: 'Unser Song', audio_url: null, media_removed: true }} />,
    );
    expect(screen.getByText('Unser Song')).toBeInTheDocument();
    expect(screen.getByText('Medium entfernt')).toBeInTheDocument();
  });
});
