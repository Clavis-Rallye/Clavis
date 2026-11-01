"""
Small video and audio files for tests, built byte by byte.

The upload route cleans video and audio and refuses a file it cannot read, so
a test needs a real container, not ``b"fake video"``. Each builder returns a
valid file of a few hundred bytes. The ``*_with_metadata`` builders add the
tags a phone writes, with the marker texts below, so a test can assert that no
marker is left.
"""

from __future__ import annotations

import struct

from app.services.media_metadata import _ogg_checksum

# Texts a test looks for after the clean. None of them may survive.
GPS_MARKER = b"+48.3705+010.8978+489.000/"
DEVICE_MARKER = b"iPhone 14 Pro"
SOFTWARE_MARKER = b"Core Media Video"
TRACK_SAMPLE_MARKER = b"GPS-TRACK-SAMPLE-48.3705-10.8978"
XMP_MARKER = b"<x:xmpmeta>secret-xmp</x:xmpmeta>"
TITLE_MARKER = b"Mein geheimer Titel"
ALL_MARKERS = (GPS_MARKER, DEVICE_MARKER, SOFTWARE_MARKER, TRACK_SAMPLE_MARKER, XMP_MARKER, TITLE_MARKER)

# The picture and sound bytes. They must come out untouched.
VIDEO_SAMPLE = b"VIDEO-SAMPLE-BYTES-0123456789"
AUDIO_SAMPLE = b"AUDIO-SAMPLE-BYTES-0123456789"

# 2023-08-30, in seconds since 1904 as QuickTime counts them
_RECORDING_TIME = 3776248630


def box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), box_type) + payload


def _hdlr(handler: bytes, name: bytes) -> bytes:
    return box(b"hdlr", bytes(4) + b"mhlr" + handler + b"appl" + bytes(8) + name + b"\x00")


def _trak(track_id: int, handler: bytes, sample_offset: int, sample_size: int, extra: bytes = b"") -> bytes:
    tkhd = box(b"tkhd", bytes(4) + struct.pack(">III", _RECORDING_TIME, _RECORDING_TIME, track_id) + bytes(72))
    mdhd = box(b"mdhd", bytes(4) + struct.pack(">IIII", _RECORDING_TIME, _RECORDING_TIME, 600, 600) + bytes(4))
    entry = box(b"avc1" if handler == b"vide" else b"mebx", bytes(6) + b"\x00\x01" + bytes(4) + b"appl" + bytes(8))
    stbl = box(
        b"stbl",
        box(b"stsd", bytes(4) + struct.pack(">I", 1) + entry)
        + box(b"stts", bytes(4) + struct.pack(">III", 1, 1, 600))
        + box(b"stsc", bytes(4) + struct.pack(">IIII", 1, 1, 1, 1))
        + box(b"stsz", bytes(4) + struct.pack(">III", 0, 1, sample_size))
        + box(b"stco", bytes(4) + struct.pack(">II", 1, sample_offset)),
    )
    minf = box(b"minf", box(b"vmhd", bytes(12)) + stbl)
    mdia = box(b"mdia", mdhd + _hdlr(handler, SOFTWARE_MARKER) + minf)
    return box(b"trak", tkhd + mdia + extra)


def _mp4(with_metadata: bool, video_payload: bytes = VIDEO_SAMPLE) -> bytes:
    ftyp = box(b"ftyp", b"qt  " + bytes(4) + b"qt  ")
    mvhd = box(b"mvhd", bytes(4) + struct.pack(">IIII", _RECORDING_TIME, _RECORDING_TIME, 600, 600) + bytes(80))

    def moov(video_offset: int, track_offset: int) -> bytes:
        parts = mvhd + _trak(1, b"vide", video_offset, len(video_payload))
        if with_metadata:
            keys = box(b"keys", bytes(4) + struct.pack(">I", 1) + box(b"mdta", b"com.apple.quicktime.location.ISO6709"))
            parts = (
                mvhd
                + _trak(1, b"vide", video_offset, len(video_payload), extra=box(b"udta", box(b"name", TITLE_MARKER)))
                # a timed metadata track, as an iPhone writes it beside picture and sound
                + _trak(2, b"meta", track_offset, len(TRACK_SAMPLE_MARKER))
                # where Android writes the place
                + box(b"udta", box(b"\xa9xyz", struct.pack(">HH", len(GPS_MARKER), 0x15C7) + GPS_MARKER))
                # where an iPhone writes place and model
                + box(b"meta", _hdlr(b"mdta", b"") + keys + box(b"ilst", GPS_MARKER + DEVICE_MARKER))
            )
        return box(b"moov", parts)

    # The movie header stands in front of the data, so an offset that moved would break the file.
    header_length = len(ftyp) + len(moov(0, 0)) + 8
    built = ftyp + moov(header_length, header_length + len(video_payload))
    built += box(b"mdat", video_payload + (TRACK_SAMPLE_MARKER if with_metadata else b""))
    if with_metadata:
        built += box(b"uuid", bytes(16) + XMP_MARKER)
    return built


def tiny_mp4(video_payload: bytes = VIDEO_SAMPLE) -> bytes:
    """A valid MP4 or QuickTime file with one picture track and no tag."""
    return _mp4(with_metadata=False, video_payload=video_payload)


def mp4_with_metadata() -> bytes:
    """A QuickTime file as a phone writes it: place, model, title, XMP and a metadata track."""
    return _mp4(with_metadata=True)


def mp4_video_sample_offset(data: bytes) -> int:
    """The chunk offset the picture track stores, read from the first ``stco`` box."""
    position = data.index(b"stco")
    return int(struct.unpack_from(">I", data, position + 12)[0])


def _ebml(element_id: bytes, payload: bytes) -> bytes:
    return element_id + (0x0100000000000000 | len(payload)).to_bytes(8, "big") + payload


def webm_with_metadata() -> bytes:
    """A WebM file with a title, a date, a track name, tags and an open-ended cluster."""
    info = _ebml(
        b"\x15\x49\xa9\x66",
        _ebml(b"\x2a\xd7\xb1", (1000000).to_bytes(3, "big"))
        + _ebml(b"\x4d\x80", DEVICE_MARKER)
        + _ebml(b"\x57\x41", SOFTWARE_MARKER)
        + _ebml(b"\x7b\xa9", TITLE_MARKER)
        + _ebml(b"\x44\x61", bytes(8)),
    )
    track = _ebml(b"\xae", _ebml(b"\xd7", b"\x01") + _ebml(b"\x53\x6e", TITLE_MARKER) + _ebml(b"\x86", b"V_VP9"))
    tracks = _ebml(b"\x16\x54\xae\x6b", track)
    tags = _ebml(b"\x12\x54\xc3\x67", GPS_MARKER)
    block = _ebml(b"\xa3", b"\x81\x00\x00\x80" + VIDEO_SAMPLE)
    cluster = b"\x1f\x43\xb6\x75" + b"\x01\xff\xff\xff\xff\xff\xff\xff" + _ebml(b"\xe7", b"\x00") + block
    segment = b"\x18\x53\x80\x67" + b"\x01\xff\xff\xff\xff\xff\xff\xff" + info + tracks + tags + cluster
    return _ebml(b"\x1a\x45\xdf\xa3", _ebml(b"\x42\x82", b"webm")) + segment


def _ogg_pages(serial: int, sequence: int, header_type: int, packet: bytes) -> bytes:
    """One packet as Ogg pages. A page holds 255 segments, so a long packet continues on the next page."""
    lacing = bytes([255] * (len(packet) // 255) + [len(packet) % 255])
    pages = b""
    consumed = 0
    for start in range(0, len(lacing), 255):
        part = lacing[start : start + 255]
        body = packet[consumed : consumed + sum(part)]
        consumed += sum(part)
        flags = header_type | (1 if start else 0)
        page = bytearray(
            b"OggS" + bytes([0, flags]) + bytes(8) + struct.pack("<II", serial, sequence + start // 255) + bytes(4)
            + bytes([len(part)]) + part + body
        )  # fmt: skip
        struct.pack_into("<I", page, 22, _ogg_checksum(page))
        pages += bytes(page)
    return pages


def ogg_opus(comments: tuple[bytes, ...] = ()) -> bytes:
    """An Ogg Opus file: the head packet, the comment packet and one sound packet."""
    head = b"OpusHead" + bytes([1, 1]) + bytes(9)
    vendor = b"libopus"
    tags = b"OpusTags" + struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(comments))
    for comment in comments:
        tags += struct.pack("<I", len(comment)) + comment
    return _ogg_pages(7, 0, 2, head) + _ogg_pages(7, 1, 0, tags) + _ogg_pages(7, 900, 4, AUDIO_SAMPLE)


def ogg_pages_are_valid(data: bytes) -> bool:
    """True when every page carries the checksum of its own bytes."""
    position = 0
    while position < len(data):
        segments = data[position + 26]
        length = 27 + segments + sum(data[position + 27 : position + 27 + segments])
        page = bytearray(data[position : position + length])
        stored = struct.unpack_from("<I", page, 22)[0]
        page[22:26] = bytes(4)
        if _ogg_checksum(page) != stored:
            return False
        position += length
    return True


# One MPEG audio frame header (MPEG-1 layer III, 128 kbit/s, 44.1 kHz) and its body
MP3_FRAMES = b"\xff\xfb\x90\x00" + AUDIO_SAMPLE * 4
# One AAC frame header in ADTS, as Signal exports a voice message
AAC_FRAMES = b"\xff\xf1\x50\x80\x04\x3f\xfc" + AUDIO_SAMPLE
AMR_FILE = b"#!AMR\n" + AUDIO_SAMPLE


def mp3_with_tags() -> bytes:
    """An MP3 file with an ID3v2 tag in front and an ID3v1 tag behind."""
    frame = b"TIT2" + struct.pack(">I", len(TITLE_MARKER) + 1) + bytes(2) + b"\x00" + TITLE_MARKER
    id3v2 = b"ID3\x03\x00\x00" + bytes([0, 0, 0, len(frame)]) + frame
    id3v1 = b"TAG" + TITLE_MARKER.ljust(125, b"\x00")
    return id3v2 + MP3_FRAMES + id3v1


def wav_with_tags() -> bytes:
    """A WAV file with a LIST chunk between the format and the sound data."""
    fmt = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16)
    info = b"INFOINAM" + struct.pack("<I", len(TITLE_MARKER) + 1) + TITLE_MARKER + b"\x00"
    listing = b"LIST" + struct.pack("<I", len(info)) + info
    sound = b"data" + struct.pack("<I", len(AUDIO_SAMPLE) + 1) + AUDIO_SAMPLE + b"\x00"
    body = b"WAVE" + fmt + listing + sound
    return b"RIFF" + struct.pack("<I", len(body)) + body


def flac_with_tags() -> bytes:
    """A FLAC file with a stream info block, a comment block and a picture block."""
    comment = struct.pack("<I", 4) + b"test" + struct.pack("<I", 1) + struct.pack("<I", len(GPS_MARKER)) + GPS_MARKER
    return (
        b"fLaC"
        + bytes([0]) + (34).to_bytes(3, "big") + bytes(34)
        + bytes([4]) + len(comment).to_bytes(3, "big") + comment
        + bytes([0x80 | 6]) + len(DEVICE_MARKER).to_bytes(3, "big") + DEVICE_MARKER
        + b"\xff\xf8" + AUDIO_SAMPLE
    )  # fmt: skip
