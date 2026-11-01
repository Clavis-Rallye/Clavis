"""
The protective headers of an answer that streams a stored object to a browser.

Every endpoint that hands stored bytes to a browser builds them here, so no two
endpoints drift apart. The general policy of ``app/main.py`` allows inline
script, so under it an object stored as ``image/svg+xml`` would run script on
direct navigation.

The function chooses inline or attachment by the stored content type, never by
the file name in the key. It sets three headers:

- ``X-Content-Type-Options: nosniff``, so the browser does not reinterpret
  the body as another type.
- A content-security policy that allows no script.
- ``Content-Disposition``: inline for the types a browser shows without
  running anything, attachment for every other stored type.

The file name comes from the stored key, which can hold any script. A header
value must be Latin-1, and a raw Cyrillic, Arabic or CJK name would raise
``UnicodeEncodeError`` and answer 500. So such a name goes out as ``filename*``
(RFC 5987, UTF-8 with percent encoding) beside an ASCII ``filename``.

Other headers, for example ``Cache-Control`` and ``Content-Length``, stay with
the endpoint.
"""

from urllib.parse import quote

# Stored types that may be served inline. A type a browser renders as a document
# can run script, so the inline set is only the raster formats the photo
# pipeline re-encodes to, plus PDF, audio and video. Every other stored type, for
# example an SVG object stored before the upload refused SVG, a HEIC pass-through
# or whatever an older deployment wrote, is served as an attachment and never
# runs in the application origin.
INLINE_MEDIA_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/bmp",
        "application/pdf",
    }
)

# Audio and video are allowed as families, like the upload allowlist
# (app/storage.py): a player renders them, no document does.
INLINE_MEDIA_FAMILIES = ("audio/", "video/")

# Every file answer carries this: no script may run from a file response, and
# with nosniff the browser must not reinterpret the body either. img-src and
# media-src 'self' are what inline photo, audio and video playback needs.
FILE_SECURITY_POLICY = "default-src 'none'; img-src 'self'; media-src 'self'"


# What the ASCII fallback of a file name must not hold, beside everything
# outside printable ASCII: the quote would end the quoted string, the
# backslash would escape the character after it, and some browsers read a
# percent sign with two hex digits in the plain parameter as an escape
# (RFC 6266, appendix D).
_UNSAFE_IN_FALLBACK = frozenset('"\\%')


def _filename_parameters(filename: str) -> str:
    """The ``filename`` parameters of ``Content-Disposition`` for a stored name.

    A name of printable ASCII goes out as it is, in ``filename`` alone. Any
    other name gets two parameters, in the order RFC 6266 advises: ``filename``
    with each unsafe character replaced by an underscore, for an old client, and
    ``filename*`` with the whole name in UTF-8 percent encoding (RFC 5987), which
    current browsers prefer. The result is always ASCII, so no name fails the
    header encoding and no control character or quote reaches the header.
    """
    fallback = "".join(
        character if " " <= character <= "~" and character not in _UNSAFE_IN_FALLBACK else "_" for character in filename
    )
    parameters = f'filename="{fallback}"'
    if fallback != filename:
        # quote() leaves only letters, digits and "-._~" unencoded, which RFC 5987
        # allows. A lone surrogate, which UTF-8 cannot encode, becomes a question
        # mark, so no name makes this line raise.
        encoded = quote(filename, safe="", encoding="utf-8", errors="replace")
        parameters += f"; filename*=UTF-8''{encoded}"
    return parameters


def file_response_headers(stored_content_type: str | None, *, filename: str | None = None) -> dict[str, str]:
    """The protective headers for an answer that streams a stored object.

    ``stored_content_type`` is the type the object store holds. ``None`` or
    empty counts as unknown and is served as an attachment. ``filename`` is the
    download name, without a path, in any script. Without it the browser names
    the download after the URL. The caller adds its own headers to the result.
    """
    stored_type = (stored_content_type or "application/octet-stream").lower()
    renders_inline = stored_type in INLINE_MEDIA_TYPES or stored_type.startswith(INLINE_MEDIA_FAMILIES)
    disposition = "inline" if renders_inline else "attachment"
    if filename:
        disposition = f"{disposition}; {_filename_parameters(filename)}"
    return {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": FILE_SECURITY_POLICY,
        "Content-Disposition": disposition,
    }
