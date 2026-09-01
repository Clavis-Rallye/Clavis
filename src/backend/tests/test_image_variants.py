"""
Unit tests for app.services.image_variants.

Every image is generated in memory with Pillow: a JPEG carrying GPS EXIF, an
orientation-tagged one, a PNG, a WebP, a small one and broken bytes. The
assertions check: no EXIF and no GPS in any output,
the orientation applied to the stored pixels, the variants at 400/1600 px long
side without ever upscaling, and bytes Pillow cannot identify returned
unchanged with no variants. A second group covers blocks besides EXIF/GPS that
an encoder can carry forward on its own: an embedded ICC profile, a JPEG
comment and a PNG text chunk, plus the palette-transparency path the ICC/mode
handling must not break. A third group covers the decompression-bomb guard: the pixel cap
for non-JPEG formats, the JPEG draft scale, and the 4096 px cap on the stored original.
"""

import io
import struct
from unittest.mock import MagicMock

import pytest
from PIL import Image, ImageCms, ImageFile, ImageOps
from PIL.ExifTags import IFD
from PIL.ExifTags import Base as ExifTagsBase
from PIL.JpegImagePlugin import JpegImageFile
from PIL.PngImagePlugin import PngInfo

from app.exceptions import CodedHTTPException
from app.services import image_variants
from app.services.image_variants import (
    DECODE_FAILURES,
    DISPLAY_LONG_SIDE,
    MAX_ORIGINAL_LONG_SIDE,
    THUMB_LONG_SIDE,
    ImageNotDecodableError,
    ImageTooLargeError,
    clean_and_variant,
    clean_image_upload,
)

_GPS_IFD = IFD.GPSInfo
_ORIENTATION_TAG = ExifTagsBase.Orientation

# GPS coordinates as a phone would embed them (Augsburg Town Hall, roughly).
_GPS_TAGS = {
    1: "N",  # GPSLatitudeRef
    2: (48.37, 21.0, 0.0),  # GPSLatitude, degrees/minutes/seconds
    3: "E",  # GPSLongitudeRef
    4: (10.9, 52.0, 0.0),  # GPSLongitude
}


def jpeg_with_gps(size: tuple[int, int], orientation: int | None = None) -> bytes:
    """A solid-colour JPEG carrying a GPS EXIF block, optionally an orientation tag."""
    exif = Image.Exif()
    exif[_GPS_IFD] = _GPS_TAGS
    if orientation is not None:
        exif[_ORIENTATION_TAG] = orientation
    image = Image.new("RGB", size, (180, 40, 90))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def plain_image(size: tuple[int, int], image_format: str) -> bytes:
    """A solid-colour image in the given format, without any metadata."""
    image = Image.new("RGB", size, (30, 120, 200))
    buffer = io.BytesIO()
    image.save(buffer, format=image_format)
    return buffer.getvalue()


def xpm_image() -> bytes:
    """An X pixmap of 4 x 4 pixels in two colours.

    The image library reads it and has no writer for the format: the cleaning
    decodes it, and ``save`` raises ``KeyError`` when it writes the image back
    in its own format.
    """
    return (
        b"/* XPM */\n"
        b"static char * pic[] = {\n"
        b'"4 4 2 1",\n'
        b'"a c #ff0000",\n'
        b'"b c #0000ff",\n'
        b'"aabb",\n'
        b'"aabb",\n'
        b'"bbaa",\n'
        b'"bbaa"\n'
        b"};\n"
    )


# A whole JPEG of 8 x 8 grey pixels, byte for byte: start of image, the JFIF
# header, one quantisation table, the frame header, two Huffman tables, the
# scan and the end of image.
GREY_JPEG = bytes.fromhex(
    "ffd8"
    "ffe000104a46494600010100000100010000"
    "ffdb004300100b0c0e0c0a100e0d0e1211101318281a181616183123251d283a333d3c39"
    "33383740485c4e404457453738506d51575f626768673e4d71797064785c656763"
    "ffc0000b080008000801011100"
    "ffc40014000100000000000000000000000000000000"
    "ffc40014100100000000000000000000000000000000"
    "ffda0008010100003f003f"
    "ffd9"
)

EXIF_MAKE = 0x010F
EXIF_ORIENTATION = 0x0112
EXIF_TYPE_SHORT = 3
EXIF_TYPE_RATIONAL = 5


def jpeg_with_exif_tag_of_wrong_type() -> bytes:
    """A sound JPEG whose EXIF block says: rotate me, and holds a tag of the wrong type.

    The block has two entries. ``Make``, the name of the camera maker, is text
    by the standard and is stored here as a fraction, 1/2. ``Orientation`` is
    6, a quarter turn. The image library decodes the picture and turns it.
    Then it writes the EXIF block again without the orientation, meets the
    fraction where it expects text and raises ``AttributeError`` inside
    ``exif_transpose``.
    """
    entry_count = 2
    # The directory starts after the 8 bytes of the TIFF header. The value of
    # the fraction does not fit into its entry and follows the directory.
    directory_offset = 8
    value_offset = directory_offset + 2 + 12 * entry_count + 4
    directory = (
        struct.pack("<H", entry_count)
        + struct.pack("<HHII", EXIF_MAKE, EXIF_TYPE_RATIONAL, 1, value_offset)
        + struct.pack("<HHIHH", EXIF_ORIENTATION, EXIF_TYPE_SHORT, 1, 6, 0)
        + struct.pack("<I", 0)  # no further directory
    )
    fraction = struct.pack("<II", 1, 2)
    exif = b"Exif\x00\x00" + b"II*\x00" + struct.pack("<I", directory_offset) + directory + fraction
    segment = b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif
    start_of_image, rest = GREY_JPEG[:2], GREY_JPEG[2:]
    return start_of_image + segment + rest


def assert_no_metadata(data: bytes, image_format: str) -> Image.Image:
    """Assert ``data`` decodes as ``image_format`` and carries no EXIF and no GPS."""
    image = Image.open(io.BytesIO(data))
    assert image.format == image_format
    exif = image.getexif()
    assert not dict(exif), f"expected no EXIF tags, got {dict(exif)}"
    assert not dict(exif.get_ifd(_GPS_IFD)), f"expected no GPS data, got {dict(exif.get_ifd(_GPS_IFD))}"
    return image


def _icc_bytes(color_temp: float = -1) -> bytes:
    """Raw bytes of an sRGB ICC profile, at a given white point."""
    profile = ImageCms.createProfile("sRGB", color_temp)
    return ImageCms.ImageCmsProfile(profile).tobytes()


def jpeg_with_icc_and_comment(size: tuple[int, int]) -> bytes:
    """A JPEG carrying an ICC profile and a comment marker."""
    image = Image.new("RGB", size, (30, 120, 200))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", icc_profile=_icc_bytes(), comment=b"vacation photo")
    return buffer.getvalue()


def png_with_icc_and_text(size: tuple[int, int]) -> bytes:
    """A PNG carrying an ICC profile and a tEXt chunk."""
    pnginfo = PngInfo()
    pnginfo.add_text("Comment", "vacation photo")
    image = Image.new("RGB", size, (30, 120, 200))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", icc_profile=_icc_bytes(), pnginfo=pnginfo)
    return buffer.getvalue()


def palette_png_with_transparency(size: tuple[int, int]) -> bytes:
    """A palette-mode PNG, half painted at a transparent palette index."""
    image = Image.new("P", size)
    palette = [0, 0, 0] * 256
    palette[0:3] = [255, 0, 255]  # index 0: the transparent colour
    palette[3:6] = [30, 120, 200]  # index 1: the opaque fill
    image.putpalette(palette)
    image.paste(1, (0, 0, size[0], size[1]))
    image.paste(0, (0, 0, size[0] // 2, size[1] // 2))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", transparency=0)
    return buffer.getvalue()


def assert_no_icc_or_extra_chunks(data: bytes, image_format: str) -> Image.Image:
    """Assert ``data`` decodes as ``image_format`` and carries no ICC, comment or text chunk."""
    image = Image.open(io.BytesIO(data))
    image.load()
    assert image.format == image_format
    assert "icc_profile" not in image.info, "expected no ICC profile"
    assert "comment" not in image.info, "expected no JPEG comment"
    assert not getattr(image, "text", None), f"expected no PNG text chunks, got {image.text}"
    return image


class TestCleanAndVariant:
    """clean_and_variant on generated images."""

    def test_jpeg_with_gps_is_cleaned_and_gets_both_variants(self):
        source = jpeg_with_gps((2400, 1600))
        source_exif = Image.open(io.BytesIO(source)).getexif()
        assert dict(source_exif.get_ifd(_GPS_IFD)), "fixture must carry GPS, or it proves nothing"

        original, thumb, display = clean_and_variant(source)

        cleaned = assert_no_metadata(original, "JPEG")
        assert cleaned.size == (2400, 1600)

        thumb_image = assert_no_metadata(thumb, "WEBP")
        assert thumb_image.size == (THUMB_LONG_SIDE, 267)

        display_image = assert_no_metadata(display, "WEBP")
        assert display_image.size == (DISPLAY_LONG_SIDE, 1067)

    def test_exif_orientation_is_applied_to_the_pixels(self):
        # Orientation 6 ("rotate 90 CW to display"): a sensor-sized 1200x1600
        # frame must be stored already rotated, as 1600x1200 — the size swap
        # proves the transpose ran, and the tag itself is gone.
        source = jpeg_with_gps((1200, 1600), orientation=6)
        assert Image.open(io.BytesIO(source)).size == (1200, 1600)

        original, thumb, _display = clean_and_variant(source)

        cleaned = assert_no_metadata(original, "JPEG")
        assert cleaned.size == (1600, 1200)
        assert assert_no_metadata(thumb, "WEBP").size == (THUMB_LONG_SIDE, 300)

    def test_png_stays_png_and_gets_both_variants(self):
        source = plain_image((2000, 1000), "PNG")

        original, thumb, display = clean_and_variant(source)

        cleaned = assert_no_metadata(original, "PNG")
        assert cleaned.size == (2000, 1000)
        assert assert_no_metadata(thumb, "WEBP").size == (THUMB_LONG_SIDE, 200)
        assert assert_no_metadata(display, "WEBP").size == (DISPLAY_LONG_SIDE, 800)

    def test_webp_stays_webp(self):
        source = plain_image((1200, 900), "WEBP")

        original, thumb, display = clean_and_variant(source)

        cleaned = assert_no_metadata(original, "WEBP")
        assert cleaned.size == (1200, 900)
        assert assert_no_metadata(thumb, "WEBP").size == (400, 300)
        # 1200 px long side is already within the 1600 px cap: no upscale.
        assert assert_no_metadata(display, "WEBP").size == (1200, 900)

    def test_small_image_is_never_upscaled(self):
        # Already within both caps — every output keeps its original size.
        source = jpeg_with_gps((300, 200))

        original, thumb, display = clean_and_variant(source)

        assert assert_no_metadata(original, "JPEG").size == (300, 200)
        assert assert_no_metadata(thumb, "WEBP").size == (300, 200)
        assert assert_no_metadata(display, "WEBP").size == (300, 200)

    def test_png_with_icc_profile_and_text_chunk_is_cleaned(self):
        source = png_with_icc_and_text((800, 600))
        source_image = Image.open(io.BytesIO(source))
        source_image.load()
        assert "icc_profile" in source_image.info, "fixture must carry an ICC profile, or it proves nothing"
        assert source_image.text, "fixture must carry a text chunk, or it proves nothing"

        original, thumb, display = clean_and_variant(source)

        assert_no_icc_or_extra_chunks(original, "PNG")
        assert_no_icc_or_extra_chunks(thumb, "WEBP")
        assert_no_icc_or_extra_chunks(display, "WEBP")

    def test_jpeg_with_comment_and_icc_profile_is_cleaned(self):
        source = jpeg_with_icc_and_comment((800, 600))
        source_image = Image.open(io.BytesIO(source))
        assert "icc_profile" in source_image.info, "fixture must carry an ICC profile, or it proves nothing"
        assert "comment" in source_image.info, "fixture must carry a comment, or it proves nothing"

        original, thumb, display = clean_and_variant(source)

        assert_no_icc_or_extra_chunks(original, "JPEG")
        assert_no_icc_or_extra_chunks(thumb, "WEBP")
        assert_no_icc_or_extra_chunks(display, "WEBP")

    def test_palette_png_with_transparency_keeps_transparent_pixels(self):
        source = palette_png_with_transparency((40, 40))
        source_image = Image.open(io.BytesIO(source))
        assert source_image.mode == "P"
        assert "transparency" in source_image.info, "fixture must carry a transparency, or it proves nothing"

        original, _thumb, _display = clean_and_variant(source)

        cleaned = Image.open(io.BytesIO(original))
        assert cleaned.mode == "RGBA"
        assert cleaned.getpixel((0, 0))[3] == 0, "the transparent corner must stay transparent"
        assert cleaned.getpixel((39, 39))[3] == 255, "the opaque corner must stay opaque"

    def test_icc_profile_conversion_runs_with_the_embedded_profile(self, monkeypatch):
        # createProfile("sRGB", color_temp) ignores color_temp (Pillow builds
        # the same bytes regardless), so there is no cheap way to embed a
        # genuinely non-sRGB RGB profile and assert a colour shift. A spy on
        # profileToProfile instead proves the conversion actually runs, with
        # the image's own embedded profile as the source.
        icc_bytes = _icc_bytes()
        image = Image.new("RGB", (300, 200), (30, 120, 200))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", icc_profile=icc_bytes)
        source = buffer.getvalue()

        spy = MagicMock(wraps=ImageCms.profileToProfile)
        monkeypatch.setattr(image_variants.ImageCms, "profileToProfile", spy)

        original, thumb, display = clean_and_variant(source)

        assert spy.call_count == 1
        called_source_profile = spy.call_args.args[1]
        assert called_source_profile.tobytes() == icc_bytes
        assert_no_icc_or_extra_chunks(original, "JPEG")
        assert_no_icc_or_extra_chunks(thumb, "WEBP")
        assert_no_icc_or_extra_chunks(display, "WEBP")

    def test_unidentifiable_bytes_are_returned_unchanged_without_variants(self):
        # Pillow cannot even guess a format for these — the only case stored
        # as uploaded, with no variants.
        for broken in (b"", b"not an image at all"):
            original, thumb, display = clean_and_variant(broken)
            assert original == broken
            assert thumb is None
            assert display is None

    def test_identified_but_corrupt_bytes_raise_instead_of_being_stored(self):
        # A JPEG magic number with a truncated body: Pillow identifies the
        # format but fails at pixel load. Only bytes Pillow cannot identify at all
        # fall back to "store as uploaded". This is a real failure the caller must see.
        broken = b"\xff\xd8\xff\xe0\x00\x10JFIFtruncated"
        with pytest.raises(ImageNotDecodableError) as raised:
            clean_and_variant(broken)
        assert isinstance(raised.value.__cause__, OSError)

    def test_truncated_real_jpeg_raises_not_a_silent_fallback(self):
        # The service raises for an image it identified but could not decode. The upload
        # routes map that to 415 IMAGE_NOT_DECODABLE. One class covers every decode
        # failure, with the library's own error as cause.
        source = plain_image((64, 64), "JPEG")
        with pytest.raises(ImageNotDecodableError) as raised:
            clean_and_variant(source[: len(source) // 2])
        assert isinstance(raised.value.__cause__, OSError)

    def test_decode_failures_of_other_classes_raise_the_same_error(self):
        # The library raises SyntaxError for a PNG chunk with an
        # invalid identifier and ValueError for an oversized text chunk.
        # Neither is an OSError, and both are the same failure to the caller.
        from tests.test_image_refusal_table import png_with_invalid_chunk, png_with_oversized_text_chunk

        for broken, library_error in (
            (png_with_invalid_chunk(), SyntaxError),
            (png_with_oversized_text_chunk(), ValueError),
        ):
            with pytest.raises(ImageNotDecodableError) as raised:
                clean_and_variant(broken)
            assert type(raised.value.__cause__) is library_error

    def test_a_failure_outside_the_named_classes_is_not_a_decode_failure(self, monkeypatch):
        # Exception is deliberately not caught: a fault of the service stays one.
        def fault(_image):
            raise RuntimeError("fault of the service")

        monkeypatch.setattr(image_variants.ImageOps, "exif_transpose", fault)
        with pytest.raises(RuntimeError):
            clean_and_variant(plain_image((8, 8), "PNG"))


class TestDecompressionBombGuard:
    """The decode-size guards: the pixel cap and the JPEG draft scale."""

    def test_non_jpeg_above_the_pixel_cap_is_rejected_without_a_full_decode(self, monkeypatch):
        # The cap is patched down to a few pixels instead of building an
        # actually huge file, so the test stays cheap; a real 50x50 PNG is
        # already well above it.
        monkeypatch.setattr(image_variants, "MAX_DECODE_PIXELS", 1_000)
        source = plain_image((50, 50), "PNG")

        with pytest.raises(ImageTooLargeError):
            clean_and_variant(source)

    def test_jpeg_above_the_original_cap_is_drafted_and_stored_at_4096(self):
        # Both sides exceed MAX_ORIGINAL_LONG_SIDE, so the JPEG draft path
        # (not the non-JPEG pixel-count rejection) is exercised, and the
        # stored original still ends up capped at 4096 px on its long side.
        source = plain_image((6000, 3000), "JPEG")

        original, thumb, display = clean_and_variant(source)

        cleaned = Image.open(io.BytesIO(original))
        assert cleaned.format == "JPEG"
        assert cleaned.size == (MAX_ORIGINAL_LONG_SIDE, 2048)
        assert Image.open(io.BytesIO(thumb)).size == (THUMB_LONG_SIDE, 200)
        assert Image.open(io.BytesIO(display)).size == (DISPLAY_LONG_SIDE, 800)

    def test_panoramic_jpeg_draft_box_matches_this_images_own_aspect_ratio(self, monkeypatch):
        # A fixed (4096, 4096) box would leave a panorama's short side already under
        # the box, so draft() would pick no reduction at all and the decoder
        # would allocate the full resolution. The box follows this image's own
        # long-side ratio.
        box = image_variants._jpeg_draft_box(16000, 1000)
        assert box == (4096, 256)

        source = plain_image((16000, 1000), "JPEG")
        with Image.open(io.BytesIO(source)) as image:
            original_pixels = image.width * image.height
            image.draft("RGB", box)
            assert image.size == (8000, 500)
            assert image.width * image.height < original_pixels

    def test_panoramic_jpeg_is_drafted_and_stored_at_4096(self):
        # A fixed box would decode 16000x1000 at full size (16M px) because the
        # short side is already below the box. The stored original is capped at
        # 4096 on the long side either way, but this proves the decode itself
        # is bounded too.
        source = plain_image((16000, 1000), "JPEG")

        original, thumb, display = clean_and_variant(source)

        cleaned = Image.open(io.BytesIO(original))
        assert cleaned.format == "JPEG"
        assert cleaned.size == (MAX_ORIGINAL_LONG_SIDE, 256)
        assert Image.open(io.BytesIO(thumb)).size == (THUMB_LONG_SIDE, 25)
        assert Image.open(io.BytesIO(display)).size == (DISPLAY_LONG_SIDE, 100)

    def test_jpeg_whose_drafted_size_still_exceeds_the_patched_cap_is_rejected(self, monkeypatch):
        # The panorama drafts down to 8000x500 (4,000,000 px) — still above a
        # cap patched low enough, proving the pixel-count check runs on the
        # post-draft size, not the header's original claim.
        monkeypatch.setattr(image_variants, "MAX_DECODE_PIXELS", 1_000_000)
        source = plain_image((16000, 1000), "JPEG")

        with pytest.raises(ImageTooLargeError):
            clean_and_variant(source)


class TestCleanImageUpload:
    """The shared entry of the three image upload routes."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("keep_unidentified", [False, True])
    async def test_an_empty_body_is_refused_before_the_exception_for_unidentified_bytes(self, keep_unidentified):
        with pytest.raises(CodedHTTPException) as refusal:
            await clean_image_upload(b"", keep_unidentified=keep_unidentified)

        assert refusal.value.status_code == 415
        assert refusal.value.error_code == "IMAGE_NOT_DECODABLE"

    @pytest.mark.asyncio
    async def test_unidentified_bytes_are_kept_only_on_request(self):
        unidentified = b"no image the library knows"

        assert await clean_image_upload(unidentified, keep_unidentified=True) == (unidentified, None, None)
        with pytest.raises(CodedHTTPException) as refusal:
            await clean_image_upload(unidentified)
        assert refusal.value.status_code == 415
        assert refusal.value.error_code == "IMAGE_NOT_DECODABLE"

    @pytest.mark.asyncio
    async def test_an_image_the_library_identifies_is_cleaned_also_when_unidentified_bytes_are_kept(self):
        source = plain_image((8, 8), "PNG")

        original, thumb, display = await clean_image_upload(source, keep_unidentified=True)

        assert Image.open(io.BytesIO(original)).format == "PNG"
        assert thumb is not None
        assert display is not None


class TestFailuresOfOneLibraryCall:
    """Two failures of the image library that are caught at one call each.

    ``KeyError`` and ``AttributeError`` are not among ``DECODE_FAILURES``:
    around the whole cleaning they would hide faults of the service. Each is
    caught at the one call of the library that raises it for a file, and the
    same class from any other place stays a fault.
    """

    def test_an_image_the_library_cannot_write_back_is_not_decodable(self):
        with pytest.raises(ImageNotDecodableError):
            clean_and_variant(xpm_image())

    def test_a_jpeg_with_an_exif_tag_of_the_wrong_type_is_not_decodable(self):
        with pytest.raises(ImageNotDecodableError):
            clean_and_variant(jpeg_with_exif_tag_of_wrong_type())

    @pytest.mark.asyncio
    @pytest.mark.parametrize("make_bytes", [xpm_image, jpeg_with_exif_tag_of_wrong_type])
    @pytest.mark.parametrize("keep_unidentified", [False, True])
    async def test_the_shared_entry_refuses_both_with_415(self, make_bytes, keep_unidentified):
        with pytest.raises(CodedHTTPException) as refusal:
            await clean_image_upload(make_bytes(), keep_unidentified=keep_unidentified)

        assert refusal.value.status_code == 415
        assert refusal.value.error_code == "IMAGE_NOT_DECODABLE"

    @pytest.mark.parametrize("step", ["_to_srgb", "_normalize_mode", "_resize_to_long_side", "_webp_variant"])
    @pytest.mark.parametrize("fault", [KeyError, AttributeError])
    def test_the_same_class_from_another_step_stays_a_fault(self, monkeypatch, step, fault):
        def broken(*args, **kwargs):
            raise fault("a fault of the service")

        monkeypatch.setattr(image_variants, step, broken)

        with pytest.raises(fault, match="a fault of the service"):
            clean_and_variant(plain_image((8, 8), "GIF"))

    @pytest.mark.parametrize("image_format", ["JPEG", "PNG", "WEBP"])
    def test_a_key_error_at_a_save_with_settings_of_ours_stays_a_fault(self, monkeypatch, image_format):
        """JPEG, PNG and WebP always have a writer. A ``KeyError`` there is no missing writer."""

        def broken(self, *args, **kwargs):
            raise KeyError("a fault of the service")

        source = plain_image((8, 8), image_format)
        monkeypatch.setattr(Image.Image, "save", broken)

        with pytest.raises(KeyError, match="a fault of the service"):
            clean_and_variant(source)

    def test_a_format_with_a_writer_is_still_written_back_in_its_own_format(self):
        original, thumb, display = clean_and_variant(plain_image((8, 8), "GIF"))

        assert Image.open(io.BytesIO(original)).format == "GIF"
        assert thumb is not None
        assert display is not None


def one_bit_png() -> bytes:
    """A PNG in black and white, one bit per pixel: a mode the cleaning converts to RGB."""
    buffer = io.BytesIO()
    Image.new("1", (8, 8), 1).save(buffer, format="PNG")
    return buffer.getvalue()


def _every_call(*args, **kwargs) -> bool:
    return True


def _copy_of_the_cleaning(image, *args, **kwargs) -> bool:
    """The library copies the opened file itself while it reads the orientation.

    The copy the cleaning asks for is the later one, of an image that is no
    file any more.
    """
    return not isinstance(image, ImageFile.ImageFile)


def _save_of_a_variant(image, fp, format=None, **kwargs) -> bool:
    return format == "WEBP"


# source image, owner of the call, its name, which of its calls fail
LIBRARY_CALLS = [
    pytest.param(lambda: plain_image((8, 8), "PNG"), Image, "open", _every_call, id="open"),
    pytest.param(lambda: plain_image((5000, 8), "JPEG"), JpegImageFile, "draft", _every_call, id="draft"),
    pytest.param(lambda: plain_image((8, 8), "PNG"), ImageFile.ImageFile, "load", _every_call, id="load"),
    pytest.param(lambda: plain_image((8, 8), "PNG"), ImageOps, "exif_transpose", _every_call, id="exif_transpose"),
    pytest.param(lambda: plain_image((8, 8), "GIF"), Image.Image, "convert", _every_call, id="convert_of_a_palette"),
    pytest.param(one_bit_png, Image.Image, "convert", _every_call, id="convert_of_another_mode"),
    pytest.param(
        lambda: plain_image((4100, 8), "PNG"), Image.Image, "resize", _every_call, id="resize_of_the_original"
    ),
    pytest.param(lambda: plain_image((500, 8), "PNG"), Image.Image, "resize", _every_call, id="resize_of_a_variant"),
    pytest.param(lambda: plain_image((8, 8), "PNG"), Image.Image, "copy", _copy_of_the_cleaning, id="copy"),
    pytest.param(lambda: plain_image((8, 8), "JPEG"), Image.Image, "save", _every_call, id="save_as_jpeg"),
    pytest.param(lambda: plain_image((8, 8), "PNG"), Image.Image, "save", _every_call, id="save_as_png"),
    pytest.param(lambda: plain_image((8, 8), "WEBP"), Image.Image, "save", _every_call, id="save_as_webp"),
    pytest.param(lambda: plain_image((8, 8), "GIF"), Image.Image, "save", _every_call, id="save_in_another_format"),
    pytest.param(lambda: plain_image((8, 8), "PNG"), Image.Image, "save", _save_of_a_variant, id="save_of_a_variant"),
]

# The helpers of the service inside the cleaning. A JPEG passes through all of them.
OWN_HELPERS = [
    "_jpeg_draft_box",
    "_to_srgb",
    "_normalize_mode",
    "_resize_to_long_side",
    "_encode_original",
    "_webp_variant",
]


class TestTheMappingWrapsOnlyTheLibrary:
    """``DECODE_FAILURES`` become ``ImageNotDecodableError`` at the calls into the image library only.

    The classes of that list are common ones: ``ValueError`` and ``TypeError``
    are what a mistake in the service's own code raises too. A mapping around the whole
    cleaning would answer such a mistake with 415, as if the upload
    were a bad image.
    """

    def test_a_missing_source_format_is_a_fault_of_the_service(self, monkeypatch):
        """A ``ValueError`` that ``_encode_original`` raises by itself passes through as a fault."""
        monkeypatch.setattr(Image, "open", lambda fp: Image.new("RGB", (8, 8)))

        with pytest.raises(ValueError, match="Pillow did not report a source format"):
            clean_and_variant(plain_image((8, 8), "PNG"))

    @pytest.mark.parametrize("helper", OWN_HELPERS)
    @pytest.mark.parametrize("fault", DECODE_FAILURES, ids=lambda fault: fault.__name__)
    def test_a_fault_of_a_helper_of_the_service_passes_through(self, monkeypatch, helper, fault):
        def broken(*args, **kwargs):
            raise fault("a fault of the service")

        monkeypatch.setattr(image_variants, helper, broken)

        with pytest.raises(fault, match="a fault of the service") as raised:
            clean_and_variant(plain_image((8, 8), "JPEG"))
        assert type(raised.value) is fault

    @pytest.mark.parametrize("make_bytes,owner,name,fails", LIBRARY_CALLS)
    @pytest.mark.parametrize("failure", DECODE_FAILURES, ids=lambda failure: failure.__name__)
    def test_a_failure_of_a_library_call_is_still_not_decodable(
        self, monkeypatch, make_bytes, owner, name, fails, failure
    ):
        """Every call into the library keeps the mapping, so no bad image turns into a server error."""
        source = make_bytes()
        real = getattr(owner, name)
        failed = []

        def call(*args, **kwargs):
            if fails(*args, **kwargs):
                failed.append(name)
                raise failure("the library failed on the file")
            return real(*args, **kwargs)

        monkeypatch.setattr(owner, name, call)

        with pytest.raises(ImageNotDecodableError) as raised:
            clean_and_variant(source)
        assert type(raised.value.__cause__) is failure
        assert failed == [name]
