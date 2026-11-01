"""
Unit tests for the metadata removal of video and audio uploads.

Every file is built in ``tests/media_samples.py`` with the tags a phone writes.
The checks are the same for each container: no marker text is left, the
picture and sound bytes are unchanged, and a second clean changes nothing.
"""

import struct

import pytest

from app.services.media_metadata import UnsupportedMediaError, strip_media_metadata
from tests import media_samples as samples


def _clean(data: bytes) -> bytes:
    cleaned = bytes(strip_media_metadata(data))
    for marker in samples.ALL_MARKERS:
        assert marker not in cleaned, f"{marker!r} survived the clean"
    assert bytes(strip_media_metadata(cleaned)) == cleaned, "a second clean changed the file"
    return cleaned


class TestMp4AndQuickTime:
    def test_the_sample_really_carries_what_a_phone_writes(self):
        data = samples.mp4_with_metadata()
        for marker in (
            samples.GPS_MARKER,
            samples.DEVICE_MARKER,
            samples.SOFTWARE_MARKER,
            samples.TRACK_SAMPLE_MARKER,
            samples.XMP_MARKER,
            samples.TITLE_MARKER,
        ):
            assert marker in data

    def test_place_device_title_and_xmp_are_gone(self):
        _clean(samples.mp4_with_metadata())

    def test_the_picture_data_stays_where_the_track_points(self):
        data = samples.mp4_with_metadata()
        cleaned = _clean(data)

        assert len(cleaned) == len(data)
        offset = samples.mp4_video_sample_offset(cleaned)
        assert offset == samples.mp4_video_sample_offset(data)
        assert cleaned[offset : offset + len(samples.VIDEO_SAMPLE)] == samples.VIDEO_SAMPLE

    def test_a_metadata_track_goes_together_with_its_samples(self):
        cleaned = _clean(samples.mp4_with_metadata())

        # One track is left, and the bytes of the other one in mdat are zero.
        assert cleaned.count(b"trak") == 1
        assert cleaned.count(b"vide") == 1
        assert b"mebx" not in cleaned

    def test_the_recording_time_is_zero(self):
        data = samples.mp4_with_metadata()
        stamp = struct.pack(">I", 3776248630)
        assert data.count(stamp) >= 6

        assert stamp not in _clean(data)

    def test_a_file_without_tags_keeps_its_picture(self):
        cleaned = _clean(samples.tiny_mp4())
        assert samples.VIDEO_SAMPLE in cleaned

    def test_padding_behind_the_last_box_is_zeroed(self):
        data = samples.tiny_mp4() + bytes(16) + samples.GPS_MARKER
        cleaned = _clean(data)
        assert len(cleaned) == len(data)

    def test_a_cut_off_file_is_refused(self):
        data = samples.mp4_with_metadata()
        with pytest.raises(UnsupportedMediaError):
            strip_media_metadata(data[: len(data) // 2])

    def test_a_file_without_movie_header_is_refused(self):
        with pytest.raises(UnsupportedMediaError):
            strip_media_metadata(samples.box(b"ftyp", b"isom" + bytes(8)) + samples.box(b"mdat", b"x" * 32))


class TestWebm:
    def test_title_date_track_name_and_tags_are_gone(self):
        data = samples.webm_with_metadata()
        assert samples.GPS_MARKER in data and samples.TITLE_MARKER in data

        cleaned = _clean(data)

        assert len(cleaned) == len(data)
        assert samples.VIDEO_SAMPLE in cleaned
        assert b"V_VP9" in cleaned


class TestOgg:
    def test_comments_are_gone_and_every_page_checksum_is_right(self):
        data = samples.ogg_opus((b"LOCATION=" + samples.GPS_MARKER, b"TITLE=" + samples.TITLE_MARKER))
        assert samples.ogg_pages_are_valid(data)

        cleaned = _clean(data)

        assert len(cleaned) == len(data)
        assert samples.ogg_pages_are_valid(cleaned)
        assert samples.AUDIO_SAMPLE in cleaned
        assert b"OpusTags" in cleaned

    def test_a_comment_packet_over_several_pages(self):
        # A cover picture makes the comment packet longer than one page.
        data = samples.ogg_opus((b"METADATA_BLOCK_PICTURE=" + samples.DEVICE_MARKER * 6000,))
        cleaned = _clean(data)
        assert samples.ogg_pages_are_valid(cleaned)

    def test_an_unknown_codec_is_refused(self):
        data = samples.ogg_opus().replace(b"OpusHead", b"NopeHead")
        with pytest.raises(UnsupportedMediaError):
            strip_media_metadata(data)


class TestTaggedAudio:
    def test_mp3_loses_both_id3_tags(self):
        assert bytes(strip_media_metadata(samples.mp3_with_tags())) == samples.MP3_FRAMES

    def test_aac_and_amr_pass_unchanged(self):
        assert bytes(strip_media_metadata(samples.AAC_FRAMES)) == samples.AAC_FRAMES
        assert bytes(strip_media_metadata(samples.AMR_FILE)) == samples.AMR_FILE

    def test_wav_keeps_format_and_sound_only(self):
        cleaned = _clean(samples.wav_with_tags())

        assert b"LIST" not in cleaned
        assert samples.AUDIO_SAMPLE in cleaned
        assert struct.unpack_from("<I", cleaned, 4)[0] == len(cleaned) - 8

    def test_flac_comment_and_picture_become_padding(self):
        data = samples.flac_with_tags()
        cleaned = _clean(data)

        assert len(cleaned) == len(data)
        assert cleaned.endswith(b"\xff\xf8" + samples.AUDIO_SAMPLE)


class TestUnknownFiles:
    @pytest.mark.parametrize(
        "data",
        [
            b"",
            b"fake video content",
            b"x" * 4096,
            b"RIFF\x00\x00\x00\x00AVI LIST" + bytes(64),
            b"%PDF-1.7 this is no recording",
        ],
    )
    def test_a_file_that_is_no_known_container_is_refused(self, data: bytes):
        with pytest.raises(UnsupportedMediaError):
            strip_media_metadata(data)


class TestCraftedFiles:
    """Anybody who joined a run can upload. A crafted file is refused fast."""

    def test_a_movie_header_full_of_empty_boxes_is_refused(self):
        flood = samples.box(b"junk", b"") * 60_000
        data = samples.box(b"ftyp", b"isom" + bytes(8)) + samples.box(b"moov", flood)
        with pytest.raises(UnsupportedMediaError):
            strip_media_metadata(data)

    def test_an_endless_comment_header_is_refused(self):
        data = samples.ogg_opus((b"X=" + bytes(9 * 1024 * 1024),))
        with pytest.raises(UnsupportedMediaError):
            strip_media_metadata(data)

    def test_a_flac_file_without_a_last_block_is_refused(self):
        data = b"fLaC" + (bytes([4]) + (0).to_bytes(3, "big")) * 60_000
        with pytest.raises(UnsupportedMediaError):
            strip_media_metadata(data)

    def test_a_track_that_points_behind_the_file_is_refused(self):
        data = bytearray(samples.mp4_with_metadata())
        # the chunk offset of the metadata track, the second stco box
        position = data.index(b"stco", data.index(b"stco") + 4)
        data[position + 12 : position + 16] = (len(data) + 100).to_bytes(4, "big")
        with pytest.raises(UnsupportedMediaError):
            strip_media_metadata(bytes(data))
