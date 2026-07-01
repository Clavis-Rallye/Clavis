"""The one place that resolves the effective retention period of a deletion class.

An org admin may configure the periods of LK_A, LK_B, LK_C and LK_H, the
run-scoped classes. A period
must not be shorter than the backup rotation window, so that a deleted record
does not outlive its period in a backup. The first source that is set decides:

1. the org override, an integer under ``organization.settings["retention"][LK_x]``
   (written by the admin endpoint in ``app/routers/admin.py``),
2. the instance setting ``settings.retention.lk_{a,b,c,h}_days`` (env),
3. the constant ``DeletionClass.retention_days``.

The other classes are not configurable: log rotation governs LK_E and LK_F,
and LK_D (immediate) and LK_G (grace) are no periods an org tunes.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from app.config import get_settings
from app.database import UNREADABLE_JSON_KEY, is_unreadable_json
from app.services.gdpr import DeletionClass

# The classes an org admin may tune. Everything else stays on its constant.
CONFIGURABLE_CLASSES: tuple[DeletionClass, ...] = (
    DeletionClass.LK_A,
    DeletionClass.LK_B,
    DeletionClass.LK_C,
    DeletionClass.LK_H,
)

# The JSONB sub-object on ``organization.settings`` that holds per-class overrides.
SETTINGS_KEY = "retention"

# Hard sanity ceiling; the floor is the instance backup-rotation window.
RETENTION_MAX_DAYS = 3650


# How deep an organization's settings may nest, the outermost object counted.
# The deepest value read is ``settings["retention"]["LK_A"]``, two levels.
# The readers of the stored value fail at unstated depths of their own: the
# JSON decoder raises a RecursionError at a depth that depends on the call
# stack (``database.tolerant_json_loads`` then returns the marker for
# unreadable settings), and the response serializer refuses a few hundred
# levels. Without a limit, settings nested 900 deep are stored and every later
# read of the organization ends in a 500. This limit is far below all of them.
MAX_SETTINGS_DEPTH = 32


class RetentionValidationError(ValueError):
    """Raised when a proposed retention period is out of bounds (→ HTTP 422)."""


class SettingsUnreadableError(ValueError):
    """The organization's stored settings cannot be read, or the given ones carry that marker.

    Not a ``RetentionValidationError``: no period is out of bounds. Unreadable
    settings have no effective period, and nothing may be merged into them.
    """


class SettingsNotStorableError(ValueError):
    """The given settings hold a value that cannot be stored and read back (→ HTTP 422).

    Nesting beyond ``MAX_SETTINGS_DEPTH``, a number JSON does not have (NaN,
    an infinity), or a string PostgreSQL does not keep in jsonb (a NUL
    character, half of a surrogate pair). Not a ``RetentionValidationError``:
    no period is out of bounds.
    """


def settings_unreadable(org_settings: Any) -> bool:
    """Whether these settings are the marker for a stored value the decoder refused.

    Nobody knows which period such an organization wanted, so no reader may
    answer with the default: the sweep leaves the organization out, and the
    retention page shows no period.
    """
    return is_unreadable_json(org_settings)


def instance_override_days(deletion_class: DeletionClass) -> int | None:
    """Instance-level (env) override for the class, or None to defer to the constant."""
    retention = get_settings().retention
    return {
        DeletionClass.LK_A: retention.lk_a_days,
        DeletionClass.LK_B: retention.lk_b_days,
        DeletionClass.LK_C: retention.lk_c_days,
        DeletionClass.LK_H: retention.lk_h_days,
    }.get(deletion_class)


def instance_default_days(deletion_class: DeletionClass) -> int:
    """The effective period with **no** org override: instance setting or constant."""
    override = instance_override_days(deletion_class)
    return override if override is not None else deletion_class.retention_days


def resolve_source(
    org_settings: dict[str, Any] | None, deletion_class: DeletionClass
) -> Literal["override", "instance", "default"]:
    """Which source decides the effective period."""
    if read_org_override(org_settings, deletion_class) is not None:
        return "override"
    if instance_override_days(deletion_class) is not None:
        return "instance"
    return "default"


def stored_override(org_settings: Any, deletion_class: DeletionClass) -> int | None:
    """The raw stored integer for the class, or None.

    Only reads: no bounds check, no logging. Settings or a ``retention`` value
    that is no object (JSONB also stores an array, a string, a number) hold no
    override. The sweep owns the warning.
    """
    if not isinstance(org_settings, dict):
        return None
    section = org_settings.get(SETTINGS_KEY)
    if not isinstance(section, dict):
        return None
    value = section.get(deletion_class.value)
    if isinstance(value, bool):  # bool is a subclass of int, and no period
        return None
    return value if isinstance(value, int) else None


def override_in_bounds(value: int) -> bool:
    """Whether a stored period is inside the same bounds the write enforces."""
    return retention_min_days() <= value <= RETENTION_MAX_DAYS


def read_org_override(org_settings: Any, deletion_class: DeletionClass) -> int | None:
    """The org's override for the class, or None if it is unset or invalid.

    A bad value never breaks the sweep. It falls through to the instance
    setting or the constant. A stored value outside the bounds of the write
    (zero and negatives included) counts as unset, so it cannot overflow
    ``timedelta`` in the sweep. The sweep logs it once per sweep.
    """
    value = stored_override(org_settings, deletion_class)
    if value is None or override_in_bounds(value):
        return value
    return None


def effective_retention_days(deletion_class: DeletionClass, org_settings: dict[str, Any] | None) -> int:
    """Resolve the effective period: org override → instance setting → constant."""
    override = read_org_override(org_settings, deletion_class)
    if override is not None:
        return override
    return instance_default_days(deletion_class)


def retention_min_days() -> int:
    """The lower bound of a configurable period: the backup rotation window.

    A shorter period would leave deleted data recoverable from backups after
    its period, a compliance gap.
    """
    return get_settings().retention.backup_rotation_days


def validate_period(days: int) -> None:
    """Validate a proposed retention period; raise RetentionValidationError if bad."""
    if not isinstance(days, int) or isinstance(days, bool):
        raise RetentionValidationError("Retention period must be an integer number of days.")
    floor = retention_min_days()
    if days < floor:
        raise RetentionValidationError(
            f"Retention period must be at least {floor} days (the backup-rotation window); "
            "a shorter period would leave deleted data recoverable from backups."
        )
    if days > RETENTION_MAX_DAYS:
        raise RetentionValidationError(f"Retention period must not exceed {RETENTION_MAX_DAYS} days.")


def apply_org_overrides(
    org_settings: dict[str, Any] | None,
    overrides: dict[DeletionClass, int | None],
) -> dict[str, Any]:
    """A new settings dict with the given overrides applied.

    ``None`` removes the override of a class. Classes outside
    ``CONFIGURABLE_CLASSES`` are ignored. ``org_settings`` is never changed in
    place, because JSONB change tracking needs a fresh object on the column.
    """
    if settings_unreadable(org_settings):
        # Merging would write the marker back as if it were the settings.
        raise SettingsUnreadableError("The organization's stored settings cannot be read.")
    new_settings: dict[str, Any] = dict(org_settings or {})
    section = dict(new_settings.get(SETTINGS_KEY) or {})
    for deletion_class, days in overrides.items():
        if deletion_class not in CONFIGURABLE_CLASSES:
            continue
        if days is None:
            section.pop(deletion_class.value, None)
        else:
            validate_period(days)
            section[deletion_class.value] = days
    if section:
        new_settings[SETTINGS_KEY] = section
    else:
        new_settings.pop(SETTINGS_KEY, None)
    return new_settings


def _refuse_unstorable_text(text: str) -> None:
    """Refuse a key or a string that PostgreSQL does not keep in jsonb."""
    if "\x00" in text:
        raise SettingsNotStorableError("Settings must not contain a NUL character.")
    if any("\ud800" <= character <= "\udfff" for character in text):
        raise SettingsNotStorableError("Settings must not contain half of a surrogate pair.")


def refuse_unstorable_settings(org_settings: Any) -> None:
    """Refuse settings that would not come back from the database as they were given.

    Each entry is a value the stored settings could not be read back from:

    - The key of the marker for unreadable settings, at any depth and whatever
      it holds (``SettingsUnreadableError``). The decoder returns
      ``{key: True}`` for a stored value it refuses. ``is_unreadable_json``
      checks the top level only, and no setting has that name.
    - Nesting beyond ``MAX_SETTINGS_DEPTH``. The decoder ends deep nesting in
      a RecursionError and returns the marker.
    - NaN and the infinities. The request body parser reads ``NaN``,
      ``Infinity`` and ``1e999``. JSON has no such number. PostgreSQL
      refuses the write, and the request ends in a 500.
    - A NUL character or half of a surrogate pair, in a string or a key.
      PostgreSQL refuses both in jsonb, and the request ends in a 500.

    Not checked here: an integer with more digits than the interpreter
    converts, and nesting the parser cannot read. The request body parser
    refuses both at the same limit as the decoder.

    The walk keeps its own stack, so the nesting it measures cannot end it.
    """
    pending: list[tuple[Any, int]] = [(org_settings, 1)]
    while pending:
        value, depth = pending.pop()
        if isinstance(value, dict | list):
            if depth > MAX_SETTINGS_DEPTH:
                raise SettingsNotStorableError(f"Settings must not nest deeper than {MAX_SETTINGS_DEPTH} levels.")
            if isinstance(value, dict):
                for key in value:
                    if key == UNREADABLE_JSON_KEY:
                        raise SettingsUnreadableError("Settings must not carry the marker for unreadable settings.")
                    if isinstance(key, str):
                        _refuse_unstorable_text(key)
                pending.extend((item, depth + 1) for item in value.values())
            else:
                pending.extend((item, depth + 1) for item in value)
        elif isinstance(value, str):
            _refuse_unstorable_text(value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise SettingsNotStorableError("Settings must not contain NaN or an infinite number.")


def validate_settings_overrides(org_settings: dict[str, Any] | None) -> None:
    """Validate a whole ``settings`` dict as ``PUT /api/orgs/{slug}`` writes it.

    A retention period set here must obey the bounds of the retention
    endpoint. Retention keys other than the configurable classes are not checked.
    Raises:

    - ``RetentionValidationError`` (HTTP 422) for a period that is no integer
      or out of bounds. ``None`` is allowed and clears the override,
    - ``SettingsUnreadableError`` for the marker of unreadable settings, at any
      depth: a client that read the marker must not save it,
    - ``SettingsNotStorableError`` for any other value that could not be read
      back (``refuse_unstorable_settings``).
    """
    if settings_unreadable(org_settings):
        raise SettingsUnreadableError("Settings must not carry the marker for unreadable settings.")
    refuse_unstorable_settings(org_settings)
    section = (org_settings or {}).get(SETTINGS_KEY)
    if not isinstance(section, dict):
        return
    for key, value in section.items():
        try:
            deletion_class = DeletionClass(key)
        except ValueError:
            continue  # not a deletion class, some other settings key
        if deletion_class not in CONFIGURABLE_CLASSES:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            if value is None:
                continue  # None clears an override, as on the retention endpoint
            raise RetentionValidationError(f"Retention override for {key} must be an integer number of days.")
        validate_period(value)
