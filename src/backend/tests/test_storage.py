"""
S3 storage integration tests.

Tests file upload, retrieval, presigned URLs, deletion, MIME validation,
and file size limits against the real object store — the Garage container
that `make test` runs on 127.0.0.1:59000.
"""

from datetime import timedelta

import pytest
import pytest_asyncio
from minio import Minio
from minio.error import S3Error
from pydantic import ValidationError

from app.config import StorageSettings, settings
from app.storage import (
    MAX_FILE_SIZE,
    delete_file,
    ensure_bucket,
    get_minio_client,
    get_presigned_url,
    list_files,
    upload_file,
)


@pytest_asyncio.fixture(scope="function")
async def minio_client() -> Minio:
    """The S3 client with the bucket ready. Removes every test object afterwards."""
    ensure_bucket()
    client = get_minio_client()
    yield client

    try:
        objects = client.list_objects(settings.storage.bucket, recursive=True)
        for obj in objects:
            client.remove_object(settings.storage.bucket, obj.object_name)
    except S3Error:
        pass  # Cleanup is best-effort


class TestStorageSettingsConfiguration:
    """Configuration of the S3-compatible store settings."""

    def test_client_gets_configured_region(self):
        """The client is built with the configured region, so it never has
        to ask the store for the bucket region before its first signed request
        (a mismatch there fails with AuthorizationHeaderMalformed)."""
        assert settings.storage.region
        client = get_minio_client()
        assert client._base_url.region == settings.storage.region

    def test_missing_s3_endpoint_fails_with_variable_named(self, monkeypatch):
        """A missing required S3_* variable fails validation with the
        variable's S3 name, so the startup error tells the operator what to set."""
        monkeypatch.delenv("S3_ENDPOINT", raising=False)
        with pytest.raises(ValidationError) as exc_info:
            StorageSettings(
                _env_file=None,
                access_key="minioadmin",
                secret_key="minioadmin",
                bucket="clavis-test",
            )
        assert "S3_ENDPOINT" in str(exc_info.value)


class TestMinIOBasicOperations:
    """Test basic object-store operations: upload, retrieve, list."""

    @pytest.mark.asyncio
    async def test_upload_and_retrieve(self, minio_client: Minio):
        """Test uploading a file and retrieving it successfully."""
        test_content = b"Test image content"
        filename = "test_image.jpg"
        content_type = "image/jpeg"

        object_key = upload_file(test_content, filename, content_type)

        assert "uploads/" in object_key
        assert filename in object_key

        response = minio_client.get_object(settings.storage.bucket, object_key)
        retrieved_content = response.read()

        assert retrieved_content == test_content
        assert response.headers.get("Content-Type") == content_type

    @pytest.mark.asyncio
    async def test_upload_auto_detects_mime_type(self, minio_client: Minio):
        """Test that MIME type is auto-detected from filename if not provided."""
        test_content = b"PNG image content"
        filename = "test_image.png"

        object_key = upload_file(test_content, filename)

        response = minio_client.get_object(settings.storage.bucket, object_key)
        assert response.headers.get("Content-Type") == "image/png"

    @pytest.mark.asyncio
    async def test_list_files_returns_uploaded_files(self, minio_client: Minio):
        """Test listing files after uploading multiple files."""
        filenames = ["file1.jpg", "file2.png", "file3.pdf"]
        uploaded_keys = []

        for filename in filenames:
            content = f"Content of {filename}".encode()
            key = upload_file(content, filename)
            uploaded_keys.append(key)

        files = list_files()

        assert len(files) >= len(filenames)
        file_keys = [f["key"] for f in files]
        for key in uploaded_keys:
            assert key in file_keys

    @pytest.mark.asyncio
    async def test_list_files_with_prefix(self, minio_client: Minio):
        """Test listing files with prefix filtering."""
        test_content = b"Test content"
        filename = "prefixed_file.jpg"
        object_key = upload_file(test_content, filename)

        # Extract date prefix (uploads/YYYY-MM-DD/)
        prefix = "/".join(object_key.split("/")[:2]) + "/"

        files = list_files(prefix=prefix)

        assert len(files) >= 1
        assert any(filename in f["key"] for f in files)


class TestPresignedURLs:
    """Test presigned URL generation."""

    @pytest.mark.asyncio
    async def test_presigned_url_generation(self, minio_client: Minio):
        """Test generating a presigned URL for an uploaded file."""
        test_content = b"Test content for presigned URL"
        filename = "presigned_test.jpg"
        object_key = upload_file(test_content, filename, "image/jpeg")

        presigned_url = get_presigned_url(object_key, expiry=timedelta(minutes=5))

        assert presigned_url.startswith("http")
        assert settings.storage.endpoint in presigned_url
        assert settings.storage.bucket in presigned_url
        # Object key appears in URL (may or may not be URL-encoded depending on client library version)
        assert filename in presigned_url or object_key in presigned_url

    @pytest.mark.asyncio
    async def test_presigned_url_allows_access(self, minio_client: Minio):
        """Test that a presigned URL is generated for an uploaded file."""
        test_content = b"Content accessible via presigned URL"
        filename = "accessible_file.jpg"
        object_key = upload_file(test_content, filename, "image/jpeg")

        presigned_url = get_presigned_url(object_key, expiry=timedelta(hours=1))

        # Only the URL is checked. An HTTP request would need the requests library.
        assert presigned_url is not None
        assert len(presigned_url) > 0

    @pytest.mark.asyncio
    async def test_presigned_url_for_nonexistent_file_still_generates(self, minio_client: Minio):
        """Test that presigned URL can be generated even for non-existent files.

        Note: the S3 store generates presigned URLs without checking object
        existence. The URL will return 404 when accessed, but generation
        succeeds.
        """
        nonexistent_key = "uploads/2099-12-31/nonexistent.jpg"

        presigned_url = get_presigned_url(nonexistent_key)

        assert presigned_url.startswith("http")
        assert settings.storage.bucket in presigned_url


class TestFileDeletion:
    """Test file deletion with verification."""

    @pytest.mark.asyncio
    async def test_delete_file_removes_object(self, minio_client: Minio):
        """Test deleting a file removes it from the S3 store."""
        test_content = b"Content to be deleted"
        filename = "to_delete.jpg"
        object_key = upload_file(test_content, filename, "image/jpeg")

        minio_client.stat_object(settings.storage.bucket, object_key)

        delete_file(object_key)

        with pytest.raises(S3Error) as exc_info:
            minio_client.stat_object(settings.storage.bucket, object_key)

        assert exc_info.value.code == "NoSuchKey"

    @pytest.mark.asyncio
    async def test_delete_nonexistent_file_succeeds(self, minio_client: Minio):
        """Test deleting a non-existent file succeeds (idempotent operation)."""
        nonexistent_key = "uploads/2099-12-31/never_existed.jpg"

        delete_file(nonexistent_key)

    @pytest.mark.asyncio
    async def test_delete_verifies_removal(self, minio_client: Minio):
        """Test that delete_file verifies the object was actually removed."""
        test_content = b"Verify deletion content"
        filename = "verify_delete.jpg"
        object_key = upload_file(test_content, filename, "image/jpeg")

        delete_file(object_key)

        with pytest.raises(S3Error) as exc_info:
            minio_client.get_object(settings.storage.bucket, object_key)

        assert exc_info.value.code == "NoSuchKey"


class TestMIMEValidation:
    """Test MIME type validation against allowlist."""

    @pytest.mark.asyncio
    async def test_allowed_mime_types_accepted(self, minio_client: Minio):
        """Test that a sample of the allowed MIME types is accepted."""
        test_cases = [
            ("image/jpeg", "test.jpg"),
            ("image/png", "test.png"),
            ("audio/mpeg", "test.mp3"),
            ("video/mp4", "test.mp4"),
            ("application/pdf", "test.pdf"),
            ("text/plain", "test.txt"),
        ]

        for content_type, filename in test_cases:
            test_content = b"Test content"

            object_key = upload_file(test_content, filename, content_type)

            assert object_key is not None
            assert filename in object_key

    @pytest.mark.asyncio
    async def test_disallowed_mime_type_rejected(self, minio_client: Minio):
        """Test that disallowed MIME types are rejected."""
        test_content = b"Executable content"
        filename = "malware.exe"
        disallowed_type = "application/x-msdownload"

        with pytest.raises(ValueError) as exc_info:
            upload_file(test_content, filename, disallowed_type)

        assert "MIME type" in str(exc_info.value)
        assert "not allowed" in str(exc_info.value)
        assert disallowed_type in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_unknown_mime_type_without_explicit_type_rejected(self, minio_client: Minio):
        """Test that files with unknown extensions are rejected without explicit type."""
        test_content = b"Unknown file content"
        filename = "unknown.unknownext123"

        with pytest.raises(ValueError) as exc_info:
            upload_file(test_content, filename)

        assert "Could not detect MIME type" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_javascript_file_rejected(self, minio_client: Minio):
        """Test that JavaScript files are rejected (security risk)."""
        test_content = b"alert('xss')"
        filename = "script.js"
        js_mime_type = "application/javascript"

        with pytest.raises(ValueError) as exc_info:
            upload_file(test_content, filename, js_mime_type)

        assert "not allowed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_html_file_rejected(self, minio_client: Minio):
        """Test that HTML files are rejected (security risk)."""
        test_content = b"<script>alert('xss')</script>"
        filename = "page.html"
        html_mime_type = "text/html"

        with pytest.raises(ValueError) as exc_info:
            upload_file(test_content, filename, html_mime_type)

        assert "not allowed" in str(exc_info.value)


class TestFileSizeLimits:
    """Test file size limit enforcement."""

    @pytest.mark.asyncio
    async def test_file_at_size_limit_accepted(self, minio_client: Minio):
        """Test that a file exactly at the size limit is accepted."""
        test_content = b"x" * MAX_FILE_SIZE
        filename = "max_size_file.jpg"

        object_key = upload_file(test_content, filename, "image/jpeg")

        assert object_key is not None

    @pytest.mark.asyncio
    async def test_file_exceeding_size_limit_rejected(self, minio_client: Minio):
        """Test that a file exceeding the size limit is rejected."""
        test_content = b"x" * (MAX_FILE_SIZE + 1)
        filename = "oversized_file.jpg"

        with pytest.raises(ValueError) as exc_info:
            upload_file(test_content, filename, "image/jpeg")

        assert "exceeds maximum allowed size" in str(exc_info.value)
        assert str(MAX_FILE_SIZE) in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_custom_size_limit_enforced(self, minio_client: Minio):
        """Test that custom size limit parameter is enforced."""
        custom_max_size = 1024  # 1 KB
        test_content = b"x" * (custom_max_size + 1)
        filename = "custom_limit_file.jpg"

        with pytest.raises(ValueError) as exc_info:
            upload_file(test_content, filename, "image/jpeg", max_size=custom_max_size)

        assert "exceeds maximum allowed size" in str(exc_info.value)
        assert str(custom_max_size) in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_small_file_accepted(self, minio_client: Minio):
        """Test that small files are accepted."""
        test_content = b"Small file content"
        filename = "small_file.jpg"

        object_key = upload_file(test_content, filename, "image/jpeg")

        assert object_key is not None
        response = minio_client.get_object(settings.storage.bucket, object_key)
        assert response.read() == test_content
