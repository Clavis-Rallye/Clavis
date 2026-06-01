import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api/content', () => ({
  setStationTitleImage: vi.fn(),
  removeStationTitleImage: vi.fn(),
}));

import '../i18n';
import StationImageField from './StationImageField';
import { setStationTitleImage, removeStationTitleImage } from '../api/content';
import type { Station } from '../api/types';
import { ApiError } from '../api/client';
import i18n from '../i18n';

const mkStation = (over: Partial<Station> = {}): Station => ({
  id: 's1',
  organization_id: 'o',
  title: { de: 'Rathaus' },
  description: { de: '' },
  address: null,
  location_lat: null,
  location_lng: null,
  title_image_url: null,
  tips: null,
  created_at: '',
  updated_at: '',
  created_by_user_id: null,
  created_by_display_name: null,
  ...over,
});

beforeEach(() => {
  vi.mocked(setStationTitleImage).mockReset();
  vi.mocked(removeStationTitleImage).mockReset();
});

describe('StationImageField', () => {
  it('renders the empty state when no image is set', () => {
    render(<StationImageField orgSlug="o" stationId="s1" imageUrl={null} onChange={() => {}} />);
    expect(screen.getByText(/Kein Titelbild|No title image/i)).toBeInTheDocument();
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('renders the preview + a remove action when an image is set', () => {
    render(
      <StationImageField orgSlug="o" stationId="s1" imageUrl="https://img/x.png" onChange={() => {}} />,
    );
    expect(screen.getByRole('img')).toHaveAttribute('src', 'https://img/x.png');
    expect(screen.getByRole('button', { name: /Entfernen|Remove/i })).toBeInTheDocument();
  });

  it('uploads a selected file and reports the updated station', async () => {
    const updated = mkStation({ title_image_url: 'https://img/new.png' });
    vi.mocked(setStationTitleImage).mockResolvedValue(updated);
    const onChange = vi.fn();
    render(<StationImageField orgSlug="o" stationId="s1" imageUrl={null} onChange={onChange} />);

    const file = new File(['bytes'], 'cover.png', { type: 'image/png' });
    fireEvent.change(screen.getByTestId('station-image-input'), { target: { files: [file] } });

    await waitFor(() => expect(onChange).toHaveBeenCalledWith(updated));
    expect(setStationTitleImage).toHaveBeenCalledWith('o', 's1', file);
  });

  it('removes the image and reports the updated station', async () => {
    const updated = mkStation({ title_image_url: null });
    vi.mocked(removeStationTitleImage).mockResolvedValue(updated);
    const onChange = vi.fn();
    render(
      <StationImageField orgSlug="o" stationId="s1" imageUrl="https://img/x.png" onChange={onChange} />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Entfernen|Remove/i }));

    await waitFor(() => expect(onChange).toHaveBeenCalledWith(updated));
    expect(removeStationTitleImage).toHaveBeenCalledWith('o', 's1');
  });

  // The reason comes from the error code, in the language of
  // the page, and never from the server's English text.
  describe('a refused title image', () => {
    const pick = () => {
      const file = new File(['no picture'], 'cover.png', { type: 'image/png' });
      fireEvent.change(screen.getByTestId('station-image-input'), { target: { files: [file] } });
    };

    afterEach(async () => {
      await i18n.changeLanguage('de');
    });

    it.each([
      ['de', 'Diese Datei ist kein lesbares Bild. Bitte wähle ein anderes Bild.'],
      ['en', 'This file is not a readable picture. Please choose another image.'],
    ])('says in %s that the file is no readable picture', async (lng, text) => {
      await i18n.changeLanguage(lng);
      vi.mocked(setStationTitleImage).mockRejectedValue(
        new ApiError(415, 'The uploaded file claims to be an image but cannot be decoded as one', 'IMAGE_NOT_DECODABLE'),
      );
      const onChange = vi.fn();
      render(<StationImageField orgSlug="o" stationId="s1" imageUrl={null} onChange={onChange} />);
      pick();

      expect(await screen.findByRole('alert')).toHaveTextContent(text);
      expect(screen.queryByText(/cannot be decoded/)).toBeNull();
      expect(onChange).not.toHaveBeenCalled();
    });

    it.each([
      ['de', 'Das Bild ist zu groß. Bitte wähle ein Bild mit geringerer Auflösung.'],
      ['en', 'The image is too large. Please choose an image with a lower resolution.'],
    ])('says in %s that the image is too large', async (lng, text) => {
      await i18n.changeLanguage(lng);
      vi.mocked(setStationTitleImage).mockRejectedValue(
        new ApiError(413, 'Image exceeds the decode size limit: 99999x99999', 'IMAGE_TOO_LARGE'),
      );
      render(<StationImageField orgSlug="o" stationId="s1" imageUrl={null} onChange={() => {}} />);
      pick();

      expect(await screen.findByRole('alert')).toHaveTextContent(text);
      expect(screen.queryByText(/decode size limit/)).toBeNull();
    });

    it('keeps the server text of a failure without a known code', async () => {
      vi.mocked(setStationTitleImage).mockRejectedValue(new ApiError(404, 'Station not found'));
      render(<StationImageField orgSlug="o" stationId="s1" imageUrl={null} onChange={() => {}} />);
      pick();

      expect(await screen.findByRole('alert')).toHaveTextContent('Station not found');
    });
  });
});
