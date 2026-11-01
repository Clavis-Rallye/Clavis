"""The small helpers that several modules share, each tested once."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.repositories.invitation import hash_token
from app.routers.files import release_stream
from app.routers.schemas import lift_created_by_display_name
from app.routers.stations import is_creator
from app.services.retention_tasks import is_old_enough, reference_or_now


@pytest.mark.parametrize(
    ("role", "expected"),
    [("creator", True), ("org_admin", True), ("coordinator", False), ("participant", False), ("guest", False)],
)
def test_is_creator_by_role(role: str, expected: bool) -> None:
    user: Any = SimpleNamespace(role=SimpleNamespace(value=role))
    assert is_creator(user) is expected


def test_lift_created_by_display_name_copies_the_name_of_the_author() -> None:
    data = SimpleNamespace(created_by=SimpleNamespace(display_name="Ada"), created_by_display_name=None)
    assert lift_created_by_display_name(data) is data
    assert data.created_by_display_name == "Ada"


def test_lift_created_by_display_name_keeps_a_name_already_set() -> None:
    data = SimpleNamespace(created_by=SimpleNamespace(display_name="Ada"), created_by_display_name="Grace")
    lift_created_by_display_name(data)
    assert data.created_by_display_name == "Grace"


def test_lift_created_by_display_name_leaves_a_dict_and_a_missing_author_alone() -> None:
    flat = {"created_by_display_name": "Ada"}
    assert lift_created_by_display_name(flat) is flat
    assert flat == {"created_by_display_name": "Ada"}
    orphan = SimpleNamespace(created_by=None, created_by_display_name=None)
    lift_created_by_display_name(orphan)
    assert orphan.created_by_display_name is None


def test_hash_token_is_the_hex_sha256_of_the_token() -> None:
    assert hash_token("secret") == hashlib.sha256(b"secret").hexdigest()
    assert hash_token("secret") != hash_token("Secret")


def test_release_stream_closes_the_object_and_returns_its_connection() -> None:
    obj = MagicMock()
    release_stream(obj)
    obj.close.assert_called_once_with()
    obj.release_conn.assert_called_once_with()


def test_is_old_enough_compares_with_the_cutoff() -> None:
    cutoff = datetime(2026, 1, 10, tzinfo=UTC)
    assert is_old_enough({"last_modified": cutoff}, cutoff)
    assert is_old_enough({"last_modified": cutoff - timedelta(seconds=1)}, cutoff)
    assert not is_old_enough({"last_modified": cutoff + timedelta(seconds=1)}, cutoff)


def test_is_old_enough_keeps_an_object_without_age_and_reads_naive_as_utc() -> None:
    cutoff = datetime(2026, 1, 10, tzinfo=UTC)
    assert not is_old_enough({"last_modified": None}, cutoff)
    assert is_old_enough({"last_modified": datetime(2026, 1, 9)}, cutoff)
    assert not is_old_enough({"last_modified": datetime(2026, 1, 11)}, cutoff)


def test_reference_or_now_prefers_the_reference_date() -> None:
    reference = datetime(2026, 5, 1, tzinfo=UTC)
    assert reference_or_now(reference) is reference
    before = datetime.now(UTC)
    assert before <= reference_or_now(None) <= datetime.now(UTC)
