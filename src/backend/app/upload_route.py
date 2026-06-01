"""Route classes of the upload routes: what a request may hold, checked before and while it is read.

FastAPI parses a multipart body before any dependency runs (in ``fastapi.routing.get_request_handler``,
``request.form()`` comes before ``solve_dependencies``), and it calls ``request.form()`` without arguments.
A route class is the one place before that call, so it does three things.

**The session comes first.** For a route that takes a form, its own dependencies (the session, the
role, the membership in the organisation) are solved once before the form is parsed. A refusal ends
the request before a byte of the body is read. They come from the route's signature, so the check
cannot drift from the route. A GET of the same router (for example the image of a legal text, which
needs no session) and the JSON routes are left as they are.

**The request has a ceiling that follows the route.** A Content-Length above the ceiling is refused
with 413 before a byte is read. A request without a true length (chunked) is counted while it is read
and cut off at the same bound. The ceiling is the largest file of the route plus the allowance for the
multipart frame: the video cap for ``LargeUploadRoute``, the image cap for ``ImageUploadRoute``.

**A part has the cap of its declared type.** The parser counts a part against the cap of the type in
its headers and stops the read there with 413. On the answer route an image stops at the base cap, not
at the video cap. Starlette's ``max_part_size`` cannot do this: it bounds only a part without a file
name (``starlette.formparsers.MultiPartParser.on_part_data``). A file part goes to its spooled file
with no bound. ``max_part_size`` is still raised to
``MAX_MULTIPART_PART_SIZE``, because its default of 1 MB is below the uploads.

The caps are defined in one place, ``app/storage.py``.

Usage: ``router = APIRouter(route_class=LargeUploadRoute)``, or ``ImageUploadRoute`` for a router
whose uploads are images.
"""

from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import AsyncExitStack
from typing import Any, ClassVar

from fastapi import HTTPException, Request, Response, params, status
from fastapi.dependencies.models import Dependant
from fastapi.dependencies.utils import solve_dependencies
from fastapi.routing import APIRoute, _effective_route_context_var
from python_multipart.multipart import parse_options_header
from starlette.datastructures import FormData, Headers
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.types import Message, Receive, Scope

from app.storage import (
    MAX_FILE_SIZE,
    MAX_MULTIPART_PART_SIZE,
    MAX_VIDEO_FILE_SIZE,
    MULTIPART_FRAME_ALLOWANCE,
    max_upload_size,
)

# The size cap in bytes of a part, by its declared type (None: no type declared).
PartSizeCap = Callable[[str | None], int]


def _too_large() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail="Uploaded file is too large.",
    )


def _file_too_large(cap: int) -> HTTPException:
    """The 413 of the routes' size check, for a part cut off while it is read.

    Unlike the routes' detail, it leaves out the file size, which is not known yet.
    """
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=f"File size exceeds maximum allowed size {cap} bytes ({cap // (1024 * 1024)}MB)",
    )


def _bounded(receive: Receive, ceiling: int) -> Receive:
    """Wrap ``receive`` so the request body fails with 413 once it passes ``ceiling``."""
    received = 0

    async def bounded_receive() -> Message:
        nonlocal received
        message = await receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > ceiling:
                raise _too_large()
        return message

    return bounded_receive


async def _no_body() -> Message:
    """The ``receive`` of the checks before the body: they must not read it."""
    raise RuntimeError("A dependency of an upload route read the request body before the form was parsed.")


class _CappedMultiPartParser(MultiPartParser):
    """Multipart parser that stops reading a part at the size cap of its declared type."""

    def __init__(
        self,
        headers: Headers,
        stream: AsyncGenerator[bytes, None],
        *,
        part_size_cap: PartSizeCap,
        max_files: int | float,
        max_fields: int | float,
        max_part_size: int,
    ) -> None:
        super().__init__(headers, stream, max_files=max_files, max_fields=max_fields, max_part_size=max_part_size)
        self._part_size_cap = part_size_cap
        self._current_cap = 0
        self._current_received = 0

    def on_headers_finished(self) -> None:
        super().on_headers_finished()
        declared = Headers(raw=self._current_part.item_headers).get("content-type")
        self._current_cap = self._part_size_cap(declared)
        self._current_received = 0

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        # Raised out of the feed of the parser, before the bytes are kept:
        # MultiPartParser.parse() closes the spooled files and the request ends.
        self._current_received += end - start
        if self._current_received > self._current_cap:
            raise _file_too_large(self._current_cap)
        super().on_part_data(data, start, end)


class _UploadRequest(Request):
    """Request whose multipart ``form()`` holds every part to the cap of its declared type."""

    def __init__(self, scope: Scope, receive: Receive, part_size_cap: PartSizeCap) -> None:
        super().__init__(scope, receive)
        self._part_size_cap = part_size_cap

    async def form(  # type: ignore[override]  # narrower (always-await) form of the base signature
        self,
        *,
        max_files: int | float = 1000,
        max_fields: int | float = 1000,
        max_part_size: int = MAX_MULTIPART_PART_SIZE,
    ) -> FormData:
        # Request._get_form names its parser class in place, so the multipart
        # branch is repeated here with ours. Every other body goes to the base.
        if self._form is None:
            content_type, _ = parse_options_header(self.headers.get("Content-Type"))
            if content_type == b"multipart/form-data":
                parser = _CappedMultiPartParser(
                    self.headers,
                    self.stream(),
                    part_size_cap=self._part_size_cap,
                    max_files=max_files,
                    max_fields=max_fields,
                    max_part_size=max_part_size,
                )
                try:
                    self._form = await parser.parse()
                except MultiPartException as exc:
                    # As the base answers a malformed multipart body.
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
        return await super().form(max_files=max_files, max_fields=max_fields, max_part_size=max_part_size)


class LargeUploadRoute(APIRoute):
    """APIRoute of the answer upload: a part is held to the cap of its declared type.

    ``video/*`` may reach the video cap, everything else the base cap, and the
    request as a whole the larger of the two plus the frame.
    """

    max_body_size: ClassVar[int] = max(MAX_FILE_SIZE, MAX_VIDEO_FILE_SIZE) + MULTIPART_FRAME_ALLOWANCE

    @staticmethod
    def part_size_cap(content_type: str | None) -> int:
        return max_upload_size(content_type)

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()
        # Read the route as the base class does in this call. A route of an included router is served
        # through its effective context, which holds the dependant of the full path and the provider of
        # the dependency overrides. The route object holds neither (APIRoute.get_route_handler, .handle).
        route: Any = self
        context = _effective_route_context_var.get()
        if context is not None and context.original_route is self:
            route = context
        dependant: Dependant = route.dependant
        overrides_provider: Any = route.dependency_overrides_provider
        body_field = route.body_field
        takes_form = body_field is not None and isinstance(body_field.field_info, params.Form)

        async def check_before_body(request: Request) -> None:
            """Solve the route's dependencies once, before its form is parsed.

            A refusal (401 without a session, 403, 404) is raised from here. The values are dropped and
            the route solves its dependencies again with the form, so an upload costs a second round of
            session queries. The checks' own exit stack closes before the body is read, so their database
            session holds no pooled connection while a slow upload arrives.
            """
            checks = Dependant(dependencies=dependant.dependencies, path=dependant.path)
            async with AsyncExitStack() as stack:
                scope = {**request.scope, "fastapi_inner_astack": stack, "fastapi_function_astack": stack}
                await solve_dependencies(
                    request=Request(scope, _no_body),
                    dependant=checks,
                    dependency_overrides_provider=overrides_provider,
                    async_exit_stack=stack,
                    embed_body_fields=False,
                )

        async def handler(request: Request) -> Response:
            declared = request.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > self.max_body_size:
                raise _too_large()
            if takes_form:
                await check_before_body(request)
            return await original(
                _UploadRequest(request.scope, _bounded(request.receive, self.max_body_size), self.part_size_cap)
            )

        return handler


class ImageUploadRoute(LargeUploadRoute):
    """APIRoute of a router whose uploads are images only: the base cap, whatever type a part declares."""

    max_body_size: ClassVar[int] = MAX_FILE_SIZE + MULTIPART_FRAME_ALLOWANCE

    @staticmethod
    def part_size_cap(_content_type: str | None) -> int:
        return MAX_FILE_SIZE
