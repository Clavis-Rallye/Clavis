"""
The regional place index of the address search.

One row is a named place, a street or a house address that a creator can search for. The rows come
from an OpenStreetMap extract that the operator imports with ``python -m app.cli geo import``. Until
then the table is empty, and the search asks only the configured upstream service.

The search matches word beginnings with ``LIKE`` and typing errors with trigram similarity, both on the
``pg_trgm`` indexes below. ``pg_trgm`` ships with PostgreSQL and is a trusted extension, so the owner of
the database may create it.
"""

from sqlalchemy import DDL, BigInteger, Float, Index, SmallInteger, String, event
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GeoPlace(Base):
    """A searchable place of the regional index.

    Attributes:
        kind: ``poi`` (a named place), ``street`` or ``address`` (a house number)
        category: A coarse group for the result list, for example ``authority``
        weight: Prominence from 0 to 100. A higher value ranks first
        name: The name of a place, or the street name of a street. Null for an address
        name_norm: What the ranking compares the query with, normalised
        search: Every searchable word, normalised, with a leading blank
    """

    __tablename__ = "geo_places"
    __table_args__ = (
        Index("ix_geo_places_search_trgm", "search", postgresql_using="gin", postgresql_ops={"search": "gin_trgm_ops"}),
        Index(
            "ix_geo_places_name_norm_trgm",
            "name_norm",
            postgresql_using="gin",
            postgresql_ops={"name_norm": "gin_trgm_ops"},
        ),
        Index("ix_geo_places_lat_lng", "lat", "lng"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    weight: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    street: Mapped[str | None] = mapped_column(String(255), nullable=True)
    housenumber: Mapped[str | None] = mapped_column(String(30), nullable=True)
    postcode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    district: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    name_norm: Mapped[str] = mapped_column(String(300), nullable=False)
    search: Mapped[str] = mapped_column(String(700), nullable=False)

    def __repr__(self) -> str:
        return f"<GeoPlace(id={self.id}, kind={self.kind}, name_norm={self.name_norm!r})>"


# The test suite builds the schema with ``create_all`` and not with Alembic, so
# the extension is created here as well as in migration 039.
event.listen(GeoPlace.__table__, "before_create", DDL("CREATE EXTENSION IF NOT EXISTS pg_trgm"))  # type: ignore[no-untyped-call]
