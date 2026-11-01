"""
Metadata removal for video and audio uploads.

A phone writes the place of recording, the device model, the software version
and the time into a recording. "No live tracking" is an invariant of the
platform, and a stored coordinate is personal data the platform never uses.
``image_variants`` cleans photos, this module cleans video and audio.

It rewrites the container in pure Python, without decoding or re-encoding. In
MP4 and QuickTime, WebM and Matroska, and Ogg, every metadata element is
overwritten in place with padding of the same length. No byte of picture or
sound moves, so every offset table stays valid and the file plays as before.
MP3, AAC, WAV and FLAC carry their tags as blocks around the sound data, and
these blocks are cut off or blanked.

The rule is an allow-list: inside a container the code keeps the elements a
player needs and blanks every other element, known or not. A file whose
container this module does not know, or cannot read to the end, raises
``UnsupportedMediaError``. The upload route refuses such a file, so no video
or audio is stored with metadata that nobody looked at.

A cleaned file keeps the codec settings, the container brand (for example
``qt`` for an iPhone recording), the sound and the picture. A message that an
encoder writes into the picture stream itself is not touched.
"""

from __future__ import annotations

import struct
import threading

__all__ = ["UnsupportedMediaError", "strip_media_metadata"]


class UnsupportedMediaError(Exception):
    """The bytes are no video or audio container this module can clean."""


# Upper bounds for the walk through a file. Anybody who joined a run can upload,
# so a crafted file must not keep a worker busy for long. A real recording of
# 100 MB stays far below every bound: a few thousand boxes or pages, a few
# hundred thousand blocks, a comment header of some kilobytes.
_MAX_CHILDREN = 50_000
_MAX_TRACKS = 64
_MAX_CHUNKS_OF_REMOVED_TRACK = 1_000_000
_MAX_MATROSKA_ELEMENTS = 2_000_000
_MAX_OGG_PAGES = 1_000_000
_MAX_OGG_COMMENT_BYTES = 8 * 1024 * 1024

# At most this many files are cleaned at once. A clean holds the upload and one
# working copy in memory, and the thread pool would let many run side by side.
_MAX_CONCURRENT_CLEANS = 2
_clean_slots = threading.BoundedSemaphore(_MAX_CONCURRENT_CLEANS)


def strip_media_metadata(data: bytes) -> bytes | bytearray:
    """Return ``data`` without container metadata.

    For MP4, QuickTime, WebM, Matroska, Ogg and FLAC the result has the length
    of the input. For MP3, AAC and WAV it is shorter by the removed tag blocks.
    The working copy itself is returned, so the peak memory is the upload plus
    one copy. An unknown or damaged container raises ``UnsupportedMediaError``.
    """
    with _clean_slots:
        try:
            return _dispatch(data)
        except (struct.error, IndexError) as short_read:
            raise UnsupportedMediaError("the file ends inside a header") from short_read


def _dispatch(data: bytes) -> bytes | bytearray:
    head = bytes(data[:12])
    if len(head) < 12:
        raise UnsupportedMediaError("the file is too short")
    if head[4:8] in _ISO_FIRST_BOXES:
        return _strip_isobmff(data)
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return _strip_matroska(data)
    if head[:4] == b"OggS":
        return _strip_ogg(data)
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return _strip_wav(data)
    if head[:5] == b"#!AMR":
        # AMR is a magic line followed by sound frames. It has no place for a tag.
        return data
    return _strip_tagged_stream(data)


# --------------------------------------------------------------------------
# MP4, QuickTime, M4A, 3GP (ISO base media file format)
# --------------------------------------------------------------------------

_ISO_FIRST_BOXES = frozenset({b"ftyp", b"styp", b"moov", b"mdat", b"free", b"skip", b"wide", b"pnot", b"moof"})
_ISO_PADDING = frozenset({b"free", b"skip"})
_ISO_TOP_KEEP = frozenset({b"ftyp", b"styp", b"moov", b"mdat", b"moof", b"mfra", b"sidx", b"ssix", b"wide"})
_ISO_MOOV_KEEP = frozenset({b"mvhd", b"trak", b"mvex", b"iods"})
_ISO_TRAK_KEEP = frozenset({b"tkhd", b"edts", b"mdia", b"tref", b"tapt"})
_ISO_MDIA_KEEP = frozenset({b"mdhd", b"hdlr", b"minf", b"elng"})
_ISO_MINF_KEEP = frozenset({b"vmhd", b"smhd", b"gmhd", b"nmhd", b"hmhd", b"sthd", b"dinf", b"stbl", b"hdlr"})
_ISO_MOOF_KEEP = frozenset({b"mfhd", b"traf", b"pssh"})
_ISO_TRAF_DROP = frozenset({b"udta", b"meta", b"uuid"})
# Picture and sound. Every other track kind (timed metadata, GPS telemetry,
# subtitles, timecode, hints) is removed together with its samples.
_ISO_MEDIA_HANDLERS = frozenset({b"vide", b"soun", b"auxv"})


def _iso_boxes(buf: bytearray, start: int, end: int) -> list[tuple[bytes, int, int, int]]:
    """The child boxes between ``start`` and ``end`` as (type, box start, payload start, box end)."""
    boxes: list[tuple[bytes, int, int, int]] = []
    pos = start
    while pos < end:
        if end - pos < 8:
            raise UnsupportedMediaError("a box header is cut off")
        size, box_type = struct.unpack_from(">I4s", buf, pos)
        header = 8
        if size == 1:
            (size,) = struct.unpack_from(">Q", buf, pos + 8)
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            raise UnsupportedMediaError(f"box {box_type!r} does not fit into its parent")
        boxes.append((bytes(box_type), pos, pos + header, pos + size))
        if len(boxes) > _MAX_CHILDREN:
            raise UnsupportedMediaError("a box has too many children")
        pos += size
    return boxes


def _zero(buf: bytearray, start: int, end: int) -> None:
    if end > start:
        buf[start:end] = bytes(end - start)


def _iso_blank(buf: bytearray, box: tuple[bytes, int, int, int]) -> None:
    """Turn a box into a ``free`` box of the same length with an empty payload."""
    _box_type, box_start, payload_start, box_end = box
    buf[box_start + 4 : box_start + 8] = b"free"
    _zero(buf, payload_start, box_end)


def _iso_filter(buf: bytearray, start: int, end: int, keep: frozenset[bytes]) -> list[tuple[bytes, int, int, int]]:
    """Blank every child that is not in ``keep`` and return the kept children."""
    kept = []
    for box in _iso_boxes(buf, start, end):
        if box[0] in keep:
            kept.append(box)
        elif box[0] in _ISO_PADDING:
            _zero(buf, box[2], box[3])
        else:
            _iso_blank(buf, box)
    return kept


def _iso_zero_times(buf: bytearray, payload_start: int) -> None:
    """Zero the creation and modification time of an mvhd, tkhd or mdhd box."""
    width = 16 if buf[payload_start] == 1 else 8
    _zero(buf, payload_start + 4, payload_start + 4 + width)


def _iso_child(buf: bytearray, start: int, end: int, wanted: bytes) -> tuple[bytes, int, int, int] | None:
    for box in _iso_boxes(buf, start, end):
        if box[0] == wanted:
            return box
    return None


def _iso_handler(buf: bytearray, trak: tuple[bytes, int, int, int]) -> bytes:
    mdia = _iso_child(buf, trak[2], trak[3], b"mdia")
    hdlr = _iso_child(buf, mdia[2], mdia[3], b"hdlr") if mdia else None
    if hdlr is None or hdlr[3] - hdlr[2] < 12:
        raise UnsupportedMediaError("a track has no handler")
    return bytes(buf[hdlr[2] + 8 : hdlr[2] + 12])


def _iso_track_id(buf: bytearray, trak: tuple[bytes, int, int, int]) -> int:
    tkhd = _iso_child(buf, trak[2], trak[3], b"tkhd")
    if tkhd is None:
        raise UnsupportedMediaError("a track has no header")
    offset = tkhd[2] + (20 if buf[tkhd[2]] == 1 else 12)
    (track_id,) = struct.unpack_from(">I", buf, offset)
    return int(track_id)


def _iso_zero_samples(buf: bytearray, trak: tuple[bytes, int, int, int]) -> None:
    """Zero the sample bytes a track points to. They sit in ``mdat``, outside the track box."""
    mdia = _iso_child(buf, trak[2], trak[3], b"mdia")
    minf = _iso_child(buf, mdia[2], mdia[3], b"minf") if mdia else None
    stbl = _iso_child(buf, minf[2], minf[3], b"stbl") if minf else None
    if stbl is None:
        return
    tables = {box[0]: box for box in _iso_boxes(buf, stbl[2], stbl[3])}

    offsets: tuple[int, ...] = ()
    for name, code in ((b"stco", "I"), (b"co64", "Q")):
        if name in tables:
            start = tables[name][2]
            (count,) = struct.unpack_from(">I", buf, start + 4)
            offsets = struct.unpack_from(f">{count}{code}", buf, start + 8)
    if not offsets:
        return
    if len(offsets) > _MAX_CHUNKS_OF_REMOVED_TRACK:
        raise UnsupportedMediaError("a track that is neither picture nor sound has too many chunks")

    sizes: tuple[int, ...] = ()
    uniform = 0
    if b"stsz" in tables:
        start = tables[b"stsz"][2]
        uniform, count = struct.unpack_from(">II", buf, start + 4)
        if uniform == 0:
            sizes = struct.unpack_from(f">{count}I", buf, start + 12)
    elif b"stz2" in tables:
        start = tables[b"stz2"][2]
        field, count = struct.unpack_from(">B", buf, start + 7)[0], struct.unpack_from(">I", buf, start + 8)[0]
        if field not in (8, 16):
            raise UnsupportedMediaError("a compact sample table uses 4-bit sizes")
        sizes = struct.unpack_from(f">{count}{'B' if field == 8 else 'H'}", buf, start + 12)
    else:
        raise UnsupportedMediaError("a track has no sample size table")

    if b"stsc" not in tables:
        raise UnsupportedMediaError("a track has no sample-to-chunk table")
    start = tables[b"stsc"][2]
    (count,) = struct.unpack_from(">I", buf, start + 4)
    runs = [struct.unpack_from(">III", buf, start + 8 + 12 * index) for index in range(count)]

    sample = 0
    for run_index, (first_chunk, per_chunk, _description) in enumerate(runs):
        last_chunk = runs[run_index + 1][0] - 1 if run_index + 1 < len(runs) else len(offsets)
        for chunk in range(first_chunk, last_chunk + 1):
            if chunk < 1 or chunk > len(offsets):
                raise UnsupportedMediaError("a sample-to-chunk table points outside the chunk table")
            length = uniform * per_chunk if uniform else sum(sizes[sample : sample + per_chunk])
            sample += per_chunk
            chunk_start = offsets[chunk - 1]
            if chunk_start + length > len(buf):
                raise UnsupportedMediaError("a track points behind the end of the file")
            _zero(buf, chunk_start, chunk_start + length)


def _iso_clean_handler(buf: bytearray, hdlr: tuple[bytes, int, int, int]) -> None:
    """Zero the vendor and the name of a handler box, for example "Core Media Video" of Apple."""
    _zero(buf, hdlr[2] + 12, min(hdlr[2] + 16, hdlr[3]))
    _zero(buf, hdlr[2] + 24, hdlr[3])


def _iso_clean_sample_entries(buf: bytearray, stbl: tuple[bytes, int, int, int]) -> None:
    """Zero the vendor code in the sample descriptions of a picture or sound track.

    QuickTime writes the maker of the encoder there, for example ``appl``.
    In an MP4 file the four bytes are reserved and already zero.
    """
    stsd = _iso_child(buf, stbl[2], stbl[3], b"stsd")
    if stsd is None:
        return
    for entry in _iso_boxes(buf, stsd[2] + 8, stsd[3]):
        if entry[3] - entry[1] >= 24:
            _zero(buf, entry[1] + 20, entry[1] + 24)


def _iso_clean_trak(buf: bytearray, trak: tuple[bytes, int, int, int], removed_ids: set[int]) -> None:
    for box in _iso_filter(buf, trak[2], trak[3], _ISO_TRAK_KEEP):
        if box[0] == b"tkhd":
            _iso_zero_times(buf, box[2])
        elif box[0] == b"tref":
            # A reference to a removed track would point at nothing.
            for reference in _iso_boxes(buf, box[2], box[3]):
                count = (reference[3] - reference[2]) // 4
                if removed_ids.intersection(struct.unpack_from(f">{count}I", buf, reference[2])):
                    _iso_blank(buf, reference)
        elif box[0] == b"mdia":
            for child in _iso_filter(buf, box[2], box[3], _ISO_MDIA_KEEP):
                if child[0] == b"mdhd":
                    _iso_zero_times(buf, child[2])
                elif child[0] == b"hdlr":
                    _iso_clean_handler(buf, child)
                elif child[0] == b"minf":
                    for leaf in _iso_filter(buf, child[2], child[3], _ISO_MINF_KEEP):
                        if leaf[0] == b"hdlr":
                            _iso_clean_handler(buf, leaf)
                        elif leaf[0] == b"stbl":
                            _iso_clean_sample_entries(buf, leaf)


def _strip_isobmff(data: bytes) -> bytearray:
    buf = bytearray(data)
    end = len(buf)

    # Bytes behind the last box that form no box: some recorders pad the file.
    # They are zeroed, and the walk below stops in front of them.
    top: list[tuple[bytes, int, int, int]] = []
    pos = 0
    while pos < end:
        size, box_type = struct.unpack_from(">I4s", buf, pos) if end - pos >= 8 else (0, b"")
        if end - pos < 8 or not all(32 <= char < 127 or char == 0xA9 for char in box_type):
            if not top:
                raise UnsupportedMediaError("the file does not start with a box")
            _zero(buf, pos, end)
            break
        box = _iso_boxes(buf, pos, pos + _iso_box_length(buf, pos, end))[0]
        top.append(box)
        if len(top) > _MAX_CHILDREN:
            raise UnsupportedMediaError("the file has too many boxes")
        pos = box[3]

    fragmented = any(box[0] == b"moof" for box in top)
    seen_moov = False
    for box in top:
        if box[0] in _ISO_PADDING:
            _zero(buf, box[2], box[3])
        elif box[0] not in _ISO_TOP_KEEP:
            _iso_blank(buf, box)
        elif box[0] == b"moov":
            seen_moov = True
            _iso_clean_moov(buf, box, fragmented)
        elif box[0] == b"moof":
            for child in _iso_filter(buf, box[2], box[3], _ISO_MOOF_KEEP):
                if child[0] == b"traf":
                    for leaf in _iso_boxes(buf, child[2], child[3]):
                        if leaf[0] in _ISO_TRAF_DROP:
                            _iso_blank(buf, leaf)
    if not seen_moov:
        raise UnsupportedMediaError("the file has no movie header")
    return buf


def _iso_box_length(buf: bytearray, pos: int, end: int) -> int:
    (size,) = struct.unpack_from(">I", buf, pos)
    if size == 1:
        (size,) = struct.unpack_from(">Q", buf, pos + 8)
    elif size == 0:
        size = end - pos
    if size < 8 or pos + size > end:
        raise UnsupportedMediaError("a box is longer than the file")
    return int(size)


def _iso_clean_moov(buf: bytearray, moov: tuple[bytes, int, int, int], fragmented: bool) -> None:
    if _iso_child(buf, moov[2], moov[3], b"cmov") is not None:
        raise UnsupportedMediaError("the movie header is compressed")
    kept = _iso_filter(buf, moov[2], moov[3], _ISO_MOOV_KEEP)
    traks = [box for box in kept if box[0] == b"trak"]
    if len(traks) > _MAX_TRACKS:
        raise UnsupportedMediaError("the file has too many tracks")
    removed = [trak for trak in traks if _iso_handler(buf, trak) not in _ISO_MEDIA_HANDLERS]
    if removed and fragmented:
        # The samples of a fragmented file are described per fragment. Phones
        # do not write such a file with an extra track, so it is refused.
        raise UnsupportedMediaError("a fragmented file carries a track that is neither picture nor sound")
    removed_ids = {_iso_track_id(buf, trak) for trak in removed}
    for box in kept:
        if box[0] == b"mvhd":
            _iso_zero_times(buf, box[2])
    for trak in traks:
        if trak in removed:
            _iso_zero_samples(buf, trak)
            _iso_blank(buf, trak)
        else:
            _iso_clean_trak(buf, trak, removed_ids)


# --------------------------------------------------------------------------
# WebM and Matroska (EBML)
# --------------------------------------------------------------------------

_MKV_SEGMENT = 0x18538067
_MKV_INFO = 0x1549A966
_MKV_TRACKS = 0x1654AE6B
_MKV_CLUSTER = 0x1F43B675
_MKV_TRACK_ENTRY = 0xAE
_MKV_VOID = 0xEC
_MKV_CRC = 0xBF
# SeekHead, Info, Tracks, Cluster, Cues
_MKV_SEGMENT_KEEP = frozenset({0x114D9B74, _MKV_INFO, _MKV_TRACKS, _MKV_CLUSTER, 0x1C53BB6B})
# Every top-level element of a segment. An open-ended cluster ends where one of them starts.
_MKV_LEVEL_ONE = _MKV_SEGMENT_KEEP | {0x1254C367, 0x1941A469, 0x1043A770, _MKV_SEGMENT}
# TimestampScale, Duration
_MKV_INFO_KEEP = frozenset({0x2AD7B1, 0x4489})
# MuxingApp and WritingApp are mandatory, so their text is zeroed and the element stays.
_MKV_INFO_ZERO = frozenset({0x4D80, 0x5741})
_MKV_TRACK_NAME = 0x536E


def _mkv_element(buf: bytearray, pos: int, end: int) -> tuple[int, int, int | None]:
    """Read one element header: (id, payload start, payload length or None when open-ended)."""
    first = buf[pos]
    id_length = 1
    while id_length <= 4 and not first & (0x80 >> (id_length - 1)):
        id_length += 1
    if id_length > 4 or pos + id_length >= end:
        raise UnsupportedMediaError("an element id is damaged")
    element_id = int.from_bytes(buf[pos : pos + id_length], "big")
    size_pos = pos + id_length
    first = buf[size_pos]
    size_length = 1
    while size_length <= 8 and not first & (0x80 >> (size_length - 1)):
        size_length += 1
    if size_length > 8 or size_pos + size_length > end:
        raise UnsupportedMediaError("an element size is damaged")
    raw = int.from_bytes(buf[size_pos : size_pos + size_length], "big")
    value = raw & ((1 << (7 * size_length)) - 1)
    payload = size_pos + size_length
    if value == (1 << (7 * size_length)) - 1:
        return element_id, payload, None
    if payload + value > end:
        raise UnsupportedMediaError("an element is longer than its parent")
    return element_id, payload, value


def _mkv_void(buf: bytearray, start: int, end: int) -> None:
    """Overwrite ``start:end`` with one Void element of the same length."""
    length = end - start
    if length < 2:
        raise UnsupportedMediaError("an element is too short to blank")
    _zero(buf, start, end)
    buf[start] = _MKV_VOID
    if length >= 9:
        buf[start + 1 : start + 9] = ((1 << 56) | (length - 9)).to_bytes(8, "big")
    else:
        buf[start + 1] = 0x80 | (length - 2)


def _mkv_children(buf: bytearray, start: int, end: int) -> list[tuple[int, int, int, int]]:
    """Children with a known length as (id, element start, payload start, element end)."""
    children: list[tuple[int, int, int, int]] = []
    pos = start
    while pos < end:
        element_id, payload, length = _mkv_element(buf, pos, end)
        if length is None:
            raise UnsupportedMediaError("an inner element has no length")
        if len(children) >= _MAX_CHILDREN:
            raise UnsupportedMediaError("an element has too many children")
        children.append((element_id, pos, payload, payload + length))
        pos = payload + length
    return children


def _mkv_clean_info(buf: bytearray, start: int, end: int) -> None:
    for element_id, element_start, payload, element_end in _mkv_children(buf, start, end):
        if element_id in _MKV_INFO_ZERO or element_id == _MKV_VOID:
            _zero(buf, payload, element_end)
        elif element_id not in _MKV_INFO_KEEP:
            _mkv_void(buf, element_start, element_end)


def _mkv_clean_tracks(buf: bytearray, start: int, end: int) -> None:
    for element_id, element_start, payload, element_end in _mkv_children(buf, start, end):
        if element_id == _MKV_CRC:
            _mkv_void(buf, element_start, element_end)
        elif element_id == _MKV_VOID:
            _zero(buf, payload, element_end)
        elif element_id == _MKV_TRACK_ENTRY:
            for child_id, child_start, _child_payload, child_end in _mkv_children(buf, payload, element_end):
                if child_id in (_MKV_TRACK_NAME, _MKV_CRC):
                    _mkv_void(buf, child_start, child_end)


def _mkv_skip_open_cluster(buf: bytearray, pos: int, end: int, budget: list[int]) -> int:
    """The end of a cluster without a length: the next top-level element, or the end of the file."""
    while pos < end:
        _mkv_spend(budget)
        element_id, payload, length = _mkv_element(buf, pos, end)
        if element_id in _MKV_LEVEL_ONE:
            return pos
        if length is None:
            raise UnsupportedMediaError("an element inside a cluster has no length")
        pos = payload + length
    return end


def _mkv_spend(budget: list[int]) -> None:
    budget[0] -= 1
    if budget[0] < 0:
        raise UnsupportedMediaError("the file has too many elements")


def _mkv_clean_segment(buf: bytearray, start: int, end: int) -> None:
    budget = [_MAX_MATROSKA_ELEMENTS]
    pos = start
    while pos < end:
        _mkv_spend(budget)
        element_id, payload, length = _mkv_element(buf, pos, end)
        if length is None:
            if element_id != _MKV_CLUSTER:
                raise UnsupportedMediaError("an element of the segment has no length")
            pos = _mkv_skip_open_cluster(buf, payload, end, budget)
            continue
        element_end = payload + length
        if element_id == _MKV_INFO:
            _mkv_clean_info(buf, payload, element_end)
        elif element_id == _MKV_TRACKS:
            _mkv_clean_tracks(buf, payload, element_end)
        elif element_id == _MKV_VOID:
            _zero(buf, payload, element_end)
        elif element_id not in _MKV_SEGMENT_KEEP:
            # Tags, Attachments, Chapters, a checksum and everything unknown
            _mkv_void(buf, pos, element_end)
        pos = element_end


def _strip_matroska(data: bytes) -> bytearray:
    buf = bytearray(data)
    end = len(buf)
    pos = 0
    seen_segment = False
    while pos < end:
        element_id, payload, length = _mkv_element(buf, pos, end)
        element_end = end if length is None else payload + length
        if element_id == _MKV_SEGMENT:
            seen_segment = True
            _mkv_clean_segment(buf, payload, element_end)
        elif element_id == _MKV_VOID:
            _zero(buf, payload, element_end)
        elif element_id != 0x1A45DFA3:
            raise UnsupportedMediaError("an unknown element stands beside the segment")
        pos = element_end
    if not seen_segment:
        raise UnsupportedMediaError("the file has no segment")
    return buf


# --------------------------------------------------------------------------
# Ogg (Opus, Vorbis, Theora, Speex)
# --------------------------------------------------------------------------

# codec magic at the start of the first packet -> (magic of the comment packet, trailing bytes)
_OGG_CODECS: tuple[tuple[bytes, bytes, bytes], ...] = (
    (b"OpusHead", b"OpusTags", b""),
    (b"\x01vorbis", b"\x03vorbis", b"\x01"),
    (b"\x80theora", b"\x81theora", b""),
    (b"Speex   ", b"", b""),
)


def _ogg_crc_table() -> tuple[int, ...]:
    table = []
    for index in range(256):
        value = index << 24
        for _ in range(8):
            value = ((value << 1) ^ 0x04C11DB7) if value & 0x80000000 else (value << 1)
        table.append(value & 0xFFFFFFFF)
    return tuple(table)


_OGG_CRC = _ogg_crc_table()


def _ogg_checksum(page: bytes | bytearray) -> int:
    crc = 0
    table = _OGG_CRC
    for byte in page:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ table[(crc >> 24) ^ byte]
    return crc


class _OggStream:
    """Where the first two packets of one logical stream sit in the file."""

    def __init__(self) -> None:
        self.packet = 0
        self.first = bytearray()
        self.comment_ranges: list[tuple[int, int]] = []
        self.comment_bytes = 0
        self.pages: list[tuple[int, int]] = []
        self.done = False


def _strip_ogg(data: bytes) -> bytearray:
    buf = bytearray(data)
    end = len(buf)
    streams: dict[int, _OggStream] = {}
    pos = 0
    pages = 0
    while pos < end:
        pages += 1
        if pages > _MAX_OGG_PAGES:
            raise UnsupportedMediaError("the file has too many pages")
        if buf[pos : pos + 4] != b"OggS" or pos + 27 > end:
            raise UnsupportedMediaError("the page sequence is broken")
        header_type = buf[pos + 5]
        (serial,) = struct.unpack_from("<I", buf, pos + 14)
        segment_count = buf[pos + 26]
        lacing = buf[pos + 27 : pos + 27 + segment_count]
        body = pos + 27 + segment_count
        page_end = body + sum(lacing)
        if len(lacing) != segment_count or page_end > end:
            raise UnsupportedMediaError("a page is cut off")
        if header_type & 0x02:
            # A new logical stream starts here, also in the middle of a chained file.
            streams[serial] = _OggStream()
        stream = streams.get(serial)
        if stream is None:
            raise UnsupportedMediaError("a page belongs to no stream")
        if not stream.done:
            cursor = body
            for value in lacing:
                if stream.packet == 0:
                    if len(stream.first) < 16:
                        stream.first += buf[cursor : cursor + value]
                elif stream.packet == 1:
                    stream.comment_bytes += value
                    if stream.comment_bytes > _MAX_OGG_COMMENT_BYTES:
                        raise UnsupportedMediaError("the comment header is too long")
                    stream.comment_ranges.append((cursor, cursor + value))
                    if not stream.pages or stream.pages[-1][0] != pos:
                        stream.pages.append((pos, page_end))
                cursor += value
                if value < 255:
                    stream.packet += 1
                    if stream.packet == 2:
                        stream.done = True
                        break
        pos = page_end

    if not streams:
        raise UnsupportedMediaError("the file has no stream")
    for stream in streams.values():
        if not stream.done:
            raise UnsupportedMediaError("a stream has no comment header")
        _ogg_rewrite_comment(buf, stream)
    return buf


def _ogg_rewrite_comment(buf: bytearray, stream: _OggStream) -> None:
    """Replace the comment packet by one of the same length with no comment in it."""
    known = [codec for codec in _OGG_CODECS if stream.first.startswith(codec[0])]
    if not known:
        raise UnsupportedMediaError("the stream holds a codec without a known comment header")
    _head_magic, comment_magic, tail = known[0]
    length = sum(stop - start for start, stop in stream.comment_ranges)
    vendor_length = length - len(comment_magic) - 8 - len(tail)
    if vendor_length < 0:
        raise UnsupportedMediaError("the comment header is too short")
    # The vendor text takes all the room, and the comment list is empty.
    packet = comment_magic + struct.pack("<I", vendor_length) + b" " * vendor_length + struct.pack("<I", 0) + tail
    offset = 0
    for start, stop in stream.comment_ranges:
        buf[start:stop] = packet[offset : offset + stop - start]
        offset += stop - start
    for page_start, page_end in stream.pages:
        buf[page_start + 22 : page_start + 26] = bytes(4)
        struct.pack_into("<I", buf, page_start + 22, _ogg_checksum(buf[page_start:page_end]))


# --------------------------------------------------------------------------
# WAV
# --------------------------------------------------------------------------

_WAV_KEEP = frozenset({b"fmt ", b"fact", b"data"})


def _strip_wav(data: bytes) -> bytes:
    end = len(data)
    chunks: list[bytes] = []
    pos = 12
    seen_data = False
    walked = 0
    while pos + 8 <= end:
        walked += 1
        if walked > _MAX_CHILDREN:
            raise UnsupportedMediaError("the WAV file has too many chunks")
        chunk_id = data[pos : pos + 4]
        (size,) = struct.unpack_from("<I", data, pos + 4)
        # A recorder that streams writes a wrong length into the last chunk.
        size = min(size, end - pos - 8)
        if chunk_id in _WAV_KEEP:
            seen_data = seen_data or chunk_id == b"data"
            body = data[pos + 8 : pos + 8 + size]
            chunks.append(chunk_id + struct.pack("<I", size) + body + (b"\x00" if size % 2 else b""))
        pos += 8 + size + (size % 2)
    if not seen_data:
        raise UnsupportedMediaError("the WAV file has no sound data")
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


# --------------------------------------------------------------------------
# MP3, AAC (ADTS) and FLAC: a sound stream with tag blocks around it
# --------------------------------------------------------------------------

_FLAC_KEEP = frozenset({0, 3})  # STREAMINFO and SEEKTABLE
_FLAC_PADDING = 1


def _strip_tagged_stream(data: bytes) -> bytes | bytearray:
    start = 0
    while data[start : start + 3] == b"ID3" and start + 10 <= len(data):
        size = 0
        for byte in data[start + 6 : start + 10]:
            size = (size << 7) | (byte & 0x7F)
        start += 10 + size + (10 if data[start + 5] & 0x10 else 0)
    if start > len(data):
        raise UnsupportedMediaError("an ID3 tag is longer than the file")

    end = len(data)
    changed = True
    while changed:
        changed = False
        if end - start >= 128 and data[end - 128 : end - 125] == b"TAG":
            end -= 128
            changed = True
        if end - start >= 227 and data[end - 227 : end - 223] == b"TAG+":
            end -= 227
            changed = True
        if end - start >= 32 and data[end - 32 : end - 24] == b"APETAGEX":
            size, _items, flags = struct.unpack_from("<III", data, end - 20)
            total = size + (32 if flags & 0x80000000 else 0)
            if total < 32 or total > end - start:
                raise UnsupportedMediaError("an APE tag is longer than the file")
            end -= total
            changed = True
        if end - start >= 15 and data[end - 9 : end] == b"LYRICS200":
            digits = data[end - 15 : end - 9]
            if not digits.isdigit() or int(digits) + 15 > end - start:
                raise UnsupportedMediaError("a lyrics tag is damaged")
            end -= int(digits) + 15
            changed = True

    stream = data[start:end]
    if stream[:4] == b"fLaC":
        return _strip_flac(stream)
    # MPEG audio and AAC in ADTS both start every frame with eleven set bits.
    if len(stream) >= 4 and stream[0] == 0xFF and stream[1] & 0xE0 == 0xE0:
        return stream
    raise UnsupportedMediaError("the file is no known video or audio container")


def _strip_flac(data: bytes) -> bytearray:
    buf = bytearray(data)
    pos = 4
    for _block in range(_MAX_CHILDREN):
        header = buf[pos]
        length = int.from_bytes(buf[pos + 1 : pos + 4], "big")
        if pos + 4 + length > len(buf):
            raise UnsupportedMediaError("a FLAC block is longer than the file")
        if header & 0x7F not in _FLAC_KEEP:
            # Comments, pictures and application blocks become padding of the same length.
            buf[pos] = (header & 0x80) | _FLAC_PADDING
            _zero(buf, pos + 4, pos + 4 + length)
        pos += 4 + length
        if header & 0x80:
            return buf
    raise UnsupportedMediaError("the FLAC file has too many blocks")
