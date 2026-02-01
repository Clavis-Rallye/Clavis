# Data Layer

## Database Stack

- **PostgreSQL 18** (Alpine), primary datastore
- **SQLAlchemy 2.0** with async engine (`asyncpg` driver)
- **Alembic** for schema migrations
- **Garage** for media/file storage (S3-compatible)

## Declarative Base and Naming Convention

All models inherit from `Base` (defined in `app/database.py`), which uses a shared `MetaData` instance with a naming convention for constraints:

```
ix_{column_label}                          — indexes
uq_{table}_{column}                        — unique constraints
ck_{table}_{constraint}                    — check constraints
fk_{table}_{column}_{referred_table}       — foreign keys
pk_{table}                                 — primary keys
```

This ensures Alembic can generate deterministic migration names and that constraints are self-documenting.

## Model Mixins

Three mixins in `app/models/base.py` provide standard columns. Every entity model composes these:

### UUIDMixin

```python
id: UUID  # Primary key, default=uuid7
```

Uses `uuid7` (time-ordered) instead of `uuid4` (random) for better index performance. UUIDs sort chronologically, which means B-tree index pages fill sequentially rather than fragmenting.

### TimestampMixin

```python
created_at: datetime  # Server-side default: now(), timezone-aware UTC
updated_at: datetime  # Server-side default: now(), onupdate: now()
```

Both are server-managed — application code doesn't need to set them. `updated_at` auto-updates on every write via SQLAlchemy's `onupdate` hook.

### SoftDeleteMixin

```python
deleted_at: datetime | None  # Null if not deleted
is_deleted: bool             # Default: False
```

An ordinary delete sets `is_deleted` and `deleted_at` and keeps the row. This preserves referential integrity and lets an administrator restore the record. Three paths remove rows for good:

- the permanent delete of an archived station, challenge or rallye by an organization admin
- the retention sweep of guest accounts, see [Deletion Classes](security-and-compliance.md#deletion-classes)
- the scheduled erasure of an archived organization (`app/services/org_deletion.py`)

A query excludes deleted records itself with `is_deleted == False`. `TenantScopedRepository._base_query` does that for the repositories. It selects the model rows of one organization that are not soft-deleted.

## Models

### Stable Layer (Templates)

These represent reusable content templates that exist independently of any run:

| Model | Table | Key Fields | FK |
|-------|-------|------------|-----|
| `Organization` | organizations | name, slug, settings (JSONB), default_language | — |
| `User` | users | email, display_name, password_hash, role, totp_secret, webauthn_credentials (JSONB, no feature writes it) | organization_id |
| `Rallye` | rallyes | title, description, default_language | organization_id |
| `Station` | stations | title, description, address, location_lat, location_lng, title_image_key, tips | organization_id |
| `Challenge` | challenges | title, instructions, challenge_type (enum), scoring_rules (JSONB) | organization_id |
| `RallyeStation` | rallye_stations | sort_order, the place of the station in the rallye | organization_id, rallye_id, station_id |
| `RallyeStationChallenge` | rallye_station_challenges | sort_order, the place of the challenge at the station of the rallye | organization_id, rallye_station_id, challenge_id |

A station and a challenge belong to the library of the organization, not to one rallye. The two link tables place them in a rallye and hold the order, so one station or challenge can serve several rallyes.

### Dynamic Layer (Runtime)

These are created when a run starts and represent live game state:

| Model | Table | Key Fields | FK |
|-------|-------|------------|-----|
| `Run` | runs | state (enum), join_code, started_at, paused_at, ended_at | organization_id, rallye_id |
| `Team` | teams | name, join_code | organization_id, run_id |
| `TeamMember` | team_members | joined_at | organization_id, team_id, user_id |
| `RunStation` | run_stations | (snapshot fields from Station) | organization_id, run_id, source_station_id |
| `RunChallenge` | run_challenges | (snapshot fields from Challenge) | organization_id, run_id, run_station_id, source_challenge_id |
| `Submission` | submissions | content (JSONB), submitted_at | organization_id, run_id, team_id, run_challenge_id |
| `Score` | scores | points, scored_by, scored_at | organization_id, submission_id |
| `FeedEvent` | feed_events | event_type (enum), content (JSONB) | organization_id, run_id, author_id |
| `ChatMessage` | chat_messages | content, is_system_message | organization_id, team_id, author_id |
| `Feedback` | feedback | rating, comment | organization_id, run_id, user_id |

### Frozen Snapshots

When a run transitions to `active`, the snapshot service (`app/services/snapshot.py`) deep-copies all stations and challenges from the template rallye into `RunStation` and `RunChallenge` rows. This means:

- Runs operate on immutable copies of the content
- Editing a template after a run starts doesn't affect in-progress runs
- Historical runs preserve the exact content that was used

The snapshot is created within the same transaction as the state transition. If snapshotting fails, the run doesn't start.

## Repository Pattern

All database access goes through repository classes. There are two base classes:

### TenantScopedRepository[T]

For entities belonging to an organization. Constructor takes `(session, organization_id)`.

The `_base_query()` method always applies two filters:

```sql
WHERE organization_id = :org_id AND is_deleted = FALSE
```

This is the structural enforcement of multi-tenancy. Every `get_by_id`, `list`, `count`, `create`, `update`, and `soft_delete` method inherits this scoping. You cannot accidentally query across tenant boundaries because the base query makes it impossible.

Key safety features:
- `get_by_id()` returns `None` for IDs that exist in other orgs (prevents cross-tenant data leakage via UUID guessing)
- `create()` forces `organization_id` to the scoped org regardless of what's passed in kwargs
- `update()` strips `organization_id` from kwargs (can't move entities between orgs)

### SystemRepository[T]

For system-level entities like `Organization` itself. Constructor takes only `(session)`. Same CRUD operations but without `organization_id` filtering. Still respects soft-delete.

### Concrete Repositories

Each model has its own repository class that adds domain-specific queries:

```
app/repositories/
├── base.py              # TenantScopedRepository, SystemRepository
├── organization.py      # get_by_slug(), ...
├── user.py              # get_by_email(), get_by_id_unscoped(), ...
├── rallye.py
├── station.py
├── challenge.py
├── run.py               # get_by_join_code(), ...
├── team.py
├── team_member.py       # get_by_user_and_team(), ...
├── run_station.py
├── run_challenge.py
├── submission.py
├── score.py
├── feed_event.py
├── feedback.py
└── chat_message.py
```

## Session Management

The database session is provided via FastAPI dependency injection through `get_db()`:

```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()       # Auto-commit on success
        except Exception:
            await session.rollback()     # Auto-rollback on error
            raise
        finally:
            await session.close()
```

Key behaviors:
- `expire_on_commit=False` — prevents lazy loading issues after commit
- `autocommit=False`, `autoflush=False` — explicit control over when writes happen
- Repository methods use `flush()` (not `commit()`) to write within the transaction; the session dependency commits at the end of the request

## Row Locks

Two capacity rules are enforced as count-then-insert and must hold under
concurrent requests: a team's `max_members_per_team` and a run's `max_teams`.
Counting alone races: two requests that both see one free place both pass
the check and both insert, so each counting path first locks a row
with `SELECT ... FOR UPDATE`:

- **A join into an existing team** (`routers/guest.py`) locks the team's row
  (`TeamRepository.lock_row`) before it counts the team's members.
- **A join that creates a team** (`routers/guest.py`) and **a coordinator's
  team creation** (`routers/teams.py`) lock the run's row
  (`RunRepository.lock_row`) before they count the run's teams — only when
  the run has a `max_teams` at all.

Both `lock_row` helpers are column-only selects with `with_for_update()`, the
same pattern as `services/org_deletion.py`: they lock the row without loading
an ORM instance into the session. The lock is held for the rest of the
request's transaction (`get_db()` commits at request end), so the second of
two concurrent requests waits, re-counts after the winner's insert, and gets
the refusal (`TEAM_FULL` / `TEAM_LIMIT_REACHED`).

**Lock order.** The member move (`routers/teams.py`) locks both
team rows in ascending id order — regardless of which is source and which is
target — and then the member's own row. A join takes exactly one lock: one
team row *or* the run row, never both; a team creation takes only the run
row. No two paths take the same two locks in different orders, so none of
them can deadlock on another. Any new path that locks more than one of these
rows must fit that order. The endpoints behind these locks are documented in
[api-structure.md](api-structure.md).

## Connection Pool

The async engine is configured with:

| Setting | Default | Description |
|---------|---------|-------------|
| `pool_size` | 10 | Baseline connections kept in pool |
| `pool_max_overflow` | 20 | Extra connections allowed beyond pool_size |
| `pool_pre_ping` | True | Verify connections before use (handles stale connections) |

The PgNotifyListener uses a separate, dedicated `asyncpg` connection outside the SQLAlchemy pool. This prevents LISTEN operations from blocking pool connections.

## Migrations

Alembic manages schema changes with `alembic.ini` in `src/backend/`. The steps to create, apply and roll back a migration are in [Creating Database Migrations](getting-started.md#creating-database-migrations).

Migration files live in `src/backend/alembic/versions/`. The naming convention from `MetaData` ensures autogenerated migrations produce consistent constraint names.

### A hand-written constraint or index name must match the naming convention exactly

Alembic's `env.py` runs against `target_metadata` (`Base.metadata`), which
carries the naming convention from [Declarative Base and Naming
Convention](#declarative-base-and-naming-convention) above
(`ck_%(table_name)s_%(constraint_name)s` for a check constraint, and so on
for the others). `op.create_table`, `op.create_check_constraint` and the
rest apply that same convention to whatever name they are given — so a
migration must pass the short, logical constraint name, exactly as the model
does (e.g. `name="singleton"` on the model becomes
`ck_instance_settings_singleton` on both sides automatically), or wrap an
already-expanded name in `op.f(...)`, which marks it pre-formatted so
Alembic uses it exactly as written instead of expanding it again.

A finished name passed without `op.f()` gets double-prefixed —
`ck_teams_ck_teams_share_code_length`, the double prefix migration 013 shows.
Migrations 031 and 033 show the two correct
patterns side by side: migration 031 passes the bare logical name
(`"singleton"`) and lets the convention expand it, migration 033 passes the
already-expanded name wrapped in
`op.f("ck_organizations_deletion_requires_archived")`. Either is correct;
mixing them is what breaks.
