"""
Participant photo hygiene.

An uploaded photo is stored re-encoded, with the EXIF orientation applied to
the pixels and every metadata block dropped. GPS above all: the platform does
no live tracking, and a stored coordinate is personal data it never uses. Two
WebP variants go beside the cleaned original, 400 px long side for lists and
1600 px for viewing, so the grading list does not load full-size originals.

- The re-encode keeps the source format: JPEG at quality 85, PNG and WebP with
  lossless-safe defaults. Bytes Pillow cannot identify, such as HEIC without a
  plugin or plain garbage, come back unchanged and without variants.
- An embedded ICC profile, often wide-gamut on phones (for example Display
  P3), is converted to sRGB so colours do not shift. If that fails the pixels
  stay as they are. Every encode runs on a copy with an empty ``info`` dict, so
  no encoder carries forward an ICC profile, EXIF, XMP, a JPEG comment or a PNG
  text chunk.
- Decompression bombs: a small file can claim an enormous pixel count, and the
  thread pool decodes several uploads at once. A JPEG always decodes at a
  reduced scale through Pillow's ``draft``. Any other format above
  ``MAX_DECODE_PIXELS`` raises ``ImageTooLargeError`` before a pixel is
  decoded, and so does Pillow's own decompression-bomb warning. At most two
  images decode at once.

The three image upload routes (photo answers, rich-text images, station title
images) share ``clean_image_upload``. It cleans off the event loop and answers
415 ``IMAGE_NOT_DECODABLE`` or 413 ``IMAGE_TOO_LARGE``. The 415 also refuses an
image the library cannot write back in its own format, and a JPEG whose EXIF
block it cannot rewrite after the rotation. Only a failing call into the image
library is refused this way. An exception from the service's own code passes
through as a server error.
"""

from __future__ import annotations

import io
import math
import struct
import threading
import warnings
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import status
from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError
from PIL.Image import DecompressionBombError, DecompressionBombWarning
from starlette.concurrency import run_in_threadpool

from app.exceptions import CodedHTTPException

# Long-side caps for the two variants. An image already within a cap is never
# upscaled to reach it.
THUMB_LONG_SIDE = 400
DISPLAY_LONG_SIDE = 1600

# The stored original itself is capped here. A 12-megapixel phone photo (4032 px
# long side) is well within it and stays untouched.
MAX_ORIGINAL_LONG_SIDE = 4096

# A non-JPEG image claiming more pixels than this is rejected before a pixel is
# decoded. These formats decode in one shot, unlike JPEG, so the size the header
# claims is the only guard before the decode.
MAX_DECODE_PIXELS = 50_000_000

# At most this many images decode at once, so a burst of uploads through the
# thread pool cannot multiply one decode's peak memory by the pool's width.
_MAX_CONCURRENT_DECODES = 2
_decode_slots = threading.BoundedSemaphore(_MAX_CONCURRENT_DECODES)

# JPEG re-encode quality, also used for the lossy WebP variants.
JPEG_QUALITY = 85


class ImageTooLargeError(Exception):
    """The image's pixel dimensions exceed what this service will decode."""


class ImageNotDecodableError(Exception):
    """The image library identified the bytes as an image and then failed on them."""


# What the image library raises for a file it identified and cannot decode.
# The classes share no base class, so each is named:
#
# - OSError: the decoder ran out of data or met a broken stream, for example a
#   cut-off JPEG or a PNG with damaged pixel data.
# - SyntaxError: a plugin's word for "not a valid file of my format". The PNG
#   plugin raises it for an invalid chunk identifier, also while the pixels load.
# - ValueError: a value in the file is out of range for the decoder, for example
#   a compressed PNG text chunk above the library's limit, a header number that
#   is no number, or less pixel data than the header announces.
# - struct.error, IndexError, TypeError: a binary field is shorter than its
#   format, or a parsed value has the wrong shape. ``Image.open`` treats exactly
#   these three, with SyntaxError, as "this plugin cannot read the file". They
#   mean the same at load and while the EXIF block is read for the orientation.
#
# Deliberately not ``Exception``: any other failure is a fault of the service
# and must stay visible as one. The service's own code can raise ``ValueError``
# or ``TypeError`` too, so ``_library_call`` applies the list around each call
# into the image library and nowhere else.
#
# Two more classes come from the library for a bad file. At every library call
# each would hide faults of the service, so each is caught at the one call that
# raises it: ``AttributeError`` at ``exif_transpose`` in ``clean_and_variant``,
# ``KeyError`` at the ``save`` in a format of the library's choice in
# ``_encode_original``.
DECODE_FAILURES: tuple[type[Exception], ...] = (
    OSError,
    SyntaxError,
    ValueError,
    struct.error,
    IndexError,
    TypeError,
)


@contextmanager
def _library_call() -> Iterator[None]:
    """Turn a ``DECODE_FAILURES`` of the one library call inside into ``ImageNotDecodableError``.

    Wrap one call into the image library and nothing else: open, draft, load,
    the rotation, the mode conversion, resize, copy and the encoders. The checks
    and arithmetic of the service stay outside, so their exceptions are not taken
    for a bad image.

    ``UnidentifiedImageError``, an ``OSError``, passes unchanged, and
    ``clean_and_variant`` hands the bytes back for it. The library's size guard
    is not among ``DECODE_FAILURES`` and passes unchanged too.
    """
    try:
        yield
    except UnidentifiedImageError:
        raise
    except DECODE_FAILURES as failure:
        # Identified, then not decodable: a truncated read, a corrupt stream,
        # a broken chunk. One class for the caller, whatever the plugin raised.
        raise ImageNotDecodableError(str(failure)) from failure


def clean_and_variant(data: bytes) -> tuple[bytes, bytes | None, bytes | None]:
    """Re-encode an uploaded image without metadata and build its WebP variants.

    Return ``(original, thumb, display)``: the cleaned original in the source
    format, capped at ``MAX_ORIGINAL_LONG_SIDE`` on its long side, and WebP
    variants at ``THUMB_LONG_SIDE`` and ``DISPLAY_LONG_SIDE``. Bytes Pillow
    cannot identify as an image come back unchanged, with both variants ``None``.

    Raises:
        ImageTooLargeError: the image is above the decode cap, or trips
            Pillow's own decompression-bomb guard.
        ImageNotDecodableError: Pillow identified the format and then failed
            in one of its calls with one of ``DECODE_FAILURES``, failed on the
            EXIF block while applying the orientation, or has no writer for the
            source format.

    An exception from the service's own code passes through unchanged.
    """
    with _decode_slots:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", DecompressionBombWarning)
                with _library_call():
                    opened = Image.open(io.BytesIO(data))
                with opened as image:
                    source_format = image.format
                    if source_format == "JPEG":
                        # Decode at a reduced scale whatever size the header
                        # claims, so the decoder never allocates the full size.
                        draft_box = _jpeg_draft_box(image.width, image.height)
                        if draft_box is not None:
                            with _library_call():
                                image.draft("RGB", draft_box)
                    if image.width * image.height > MAX_DECODE_PIXELS:
                        raise ImageTooLargeError(
                            f"{image.width}x{image.height} px exceeds the {MAX_DECODE_PIXELS}-pixel decode cap"
                        )
                    with _library_call():
                        image.load()
                    try:
                        with _library_call():
                            oriented = ImageOps.exif_transpose(image)
                    except AttributeError as failure:
                        # After the rotation the library rewrites the EXIF block
                        # without the orientation. A tag stored with another
                        # type than the standard's, for example a camera maker
                        # as a fraction, fails there with AttributeError. The
                        # other failures of that rewrite are DECODE_FAILURES,
                        # so this one refuses the file too, rather than store a
                        # photo lying on its side. Caught at this call only: an
                        # AttributeError anywhere else stays a fault.
                        raise ImageNotDecodableError(str(failure)) from failure
                    if oriented is None:  # pragma: no cover - only with in_place=True
                        with _library_call():
                            oriented = image.copy()
                    converted = _to_srgb(oriented)
                    normalized = _normalize_mode(converted)
                    capped = _resize_to_long_side(normalized, MAX_ORIGINAL_LONG_SIDE)
                    with _library_call():
                        clean = capped.copy()
                    clean.info = {}
                    original = _encode_original(clean, source_format)
                    thumb = _webp_variant(clean, THUMB_LONG_SIDE)
                    display = _webp_variant(clean, DISPLAY_LONG_SIDE)
        except ImageTooLargeError:
            raise
        except (DecompressionBombError, DecompressionBombWarning) as bomb:
            raise ImageTooLargeError(str(bomb)) from bomb
        except UnidentifiedImageError:
            # The only failure taken as "not an image we can process". The bytes
            # go back as uploaded, without variants, and the caller decides.
            return data, None, None
        # No clause for DECODE_FAILURES: each library call maps them through
        # ``_library_call``. Around this whole block the clause would take a
        # fault of the service's own code for a bad image.
        # ImageNotDecodableError passes every clause above.
    return original, thumb, display


def bare_content_type(content_type: str | None) -> str:
    """The claimed type without its parameters, lower case: "image/png; charset=binary" is still "image/png"."""
    return (content_type or "").split(";", 1)[0].strip().lower()


def image_not_decodable(
    detail: str = "The uploaded file claims to be an image but cannot be decoded as one",
) -> CodedHTTPException:
    """The one refusal of an upload that is no image this service stores: 415 ``IMAGE_NOT_DECODABLE``."""
    return CodedHTTPException(
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail=detail,
        error_code="IMAGE_NOT_DECODABLE",
    )


async def clean_image_upload(
    data: bytes, *, keep_unidentified: bool = False
) -> tuple[bytes, bytes | None, bytes | None]:
    """Clean an uploaded image off the event loop, or refuse it with a coded answer.

    The three image upload routes share it, so they cannot answer the same bad
    image differently. It returns the triple of ``clean_and_variant``.

    ``keep_unidentified`` hands bytes the library cannot identify back
    unchanged, without variants, instead of refusing them. Only the answer route
    sets it, for the HEIC photos phones send. It never covers an empty body.

    Raises:
        CodedHTTPException: 415 ``IMAGE_NOT_DECODABLE`` for bytes the library
            cannot decode, an empty upload included. 413 ``IMAGE_TOO_LARGE``
            for an image above the decode cap. Nothing is stored in either case.
    """
    if not data:
        # Zero bytes are no image under any claim. They are refused before the
        # cleaning, where ``keep_unidentified`` would apply: otherwise the answer
        # route would store an empty object for a HEIC or HEIF claim while the two
        # content routes refuse it.
        raise image_not_decodable("The uploaded file is empty")
    try:
        original, thumb, display = await run_in_threadpool(clean_and_variant, data)
    except ImageNotDecodableError:
        raise image_not_decodable() from None
    except ImageTooLargeError as too_large:
        raise CodedHTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Image exceeds the decode size limit: {too_large}",
            error_code="IMAGE_TOO_LARGE",
        ) from None
    if thumb is None and not keep_unidentified:
        # clean_and_variant hands back unidentifiable bytes without variants. An
        # image claim the library cannot back is refused: an SVG with an onload
        # handler must never be stored under an image type.
        raise image_not_decodable()
    return original, thumb, display


def _jpeg_draft_box(width: int, height: int) -> tuple[int, int] | None:
    """The ``draft()`` target box for a JPEG of this size, or ``None`` when the long side is within the cap.

    ``draft()`` picks the coarsest power-of-two scale that keeps *both*
    dimensions at least as large as asked. A fixed square box is larger than the
    short side of a far-from-square image, a panorama for example, so ``draft()``
    would not reduce at all and the decoder would allocate the full resolution.
    A box scaled by the image's own long-side ratio lets it pick the coarsest
    safe scale.
    """
    long_side = max(width, height)
    if long_side <= MAX_ORIGINAL_LONG_SIDE:
        return None
    scale = MAX_ORIGINAL_LONG_SIDE / long_side
    return (math.ceil(width * scale), math.ceil(height * scale))


def _to_srgb(image: Image.Image) -> Image.Image:
    """Convert an embedded ICC profile to sRGB, or keep the pixels as they are on failure.

    Dropping the profile instead would shift the colours of a wide-gamut photo.
    """
    profile_bytes = image.info.get("icc_profile")
    if not profile_bytes:
        return image
    try:
        source_profile = ImageCms.ImageCmsProfile(io.BytesIO(profile_bytes))
        srgb_profile = ImageCms.createProfile("sRGB")
        converted = ImageCms.profileToProfile(image, source_profile, srgb_profile)
    except Exception:
        return image
    # Without ``inPlace`` the call always returns an Image. The stub allows None
    # for inPlace=True only.
    return converted if converted is not None else image


def _normalize_mode(image: Image.Image) -> Image.Image:
    """Normalise the mode before encoding, so clearing ``info`` cannot lose transparency."""
    if image.mode in ("P", "PA", "LA") or "transparency" in image.info:
        with _library_call():
            return image.convert("RGBA")
    if image.mode in ("RGB", "RGBA", "L"):
        return image
    with _library_call():
        return image.convert("RGB")


def _resize_to_long_side(image: Image.Image, long_side: int) -> Image.Image:
    """Return ``image`` scaled down so its long side is at most ``long_side``; never upscaled."""
    scale = long_side / max(image.width, image.height)
    if scale >= 1.0:
        return image
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    with _library_call():
        return image.resize(size, Image.Resampling.LANCZOS)


def _encode_original(image: Image.Image, source_format: str | None) -> bytes:
    """Re-encode in the source format, without any metadata keyword.

    Raises:
        ImageNotDecodableError: the library has no writer for the source
            format, or its encoder fails on the image.
        ValueError: no source format was handed in. A fault of the service,
            not of the image, so it stands outside ``_library_call``.
    """
    buffer = io.BytesIO()
    if source_format == "JPEG":
        with _library_call():
            image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    elif source_format == "WEBP":
        # A lossless WebP (screenshot-style content) stays lossless.
        with _library_call():
            image.save(buffer, format="WEBP", lossless=True)
    elif source_format == "PNG":
        with _library_call():
            image.save(buffer, format="PNG")
    elif source_format is not None:
        # Any other format Pillow decodes (GIF, BMP, TIFF, ...) keeps its own
        # format with the plugin's defaults. There are no settings of ours.
        try:
            with _library_call():
                image.save(buffer, format=source_format)
        except KeyError as no_writer:
            # The library reads more formats than it writes, XPM for example,
            # and ``save`` looks the writer up by format name. An image that
            # cannot be written back cannot be stored cleaned, so it is refused.
            # Caught at this call only, where the library chose the format: the
            # three saves above name formats that always have a writer, and a
            # KeyError there or anywhere else stays a fault.
            raise ImageNotDecodableError(f"no writer for the image format {source_format}") from no_writer
    else:
        # Image.open always reports a format, so this is a fault of the service.
        raise ValueError("Pillow did not report a source format")
    return buffer.getvalue()


def _webp_variant(image: Image.Image, long_side: int) -> bytes:
    """A WebP re-encode whose long side is capped at ``long_side``, never upscaled.

    ``image`` is already mode-normalized by the caller (RGB, RGBA or L), which
    the WebP encoder accepts natively.
    """
    variant = _resize_to_long_side(image, long_side)
    buffer = io.BytesIO()
    with _library_call():
        variant.save(buffer, format="WEBP", quality=JPEG_QUALITY)
    return buffer.getvalue()
