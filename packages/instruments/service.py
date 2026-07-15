"""Instrument service — the write side of the instrument master.

Reads and writes are separated so callers can inject an in-memory backend for
tests. The DB-backed backend uses SQLAlchemy Core against the ``instruments``
+ ``instrument_aliases`` tables from migration 0001.

Contracts (spec §主数据):
- Canonical `InstrumentId` is the ONLY uniqueness key. Ticker alone is not.
- Aliases live in ``instrument_aliases`` and are versioned by
  (valid_from_utc, valid_to_utc); an alias may map to different canonical ids
  over time (delisted/relisted symbols).
- ``upsert`` is idempotent per (instrument_id) and never rewrites history —
  new alias rows are appended.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, Sequence

from packages.common.errors import UnknownInstrumentError
from packages.common.instrument_id import InstrumentId
from packages.common.time_utils import utcnow
from packages.data_sources.contracts import InstrumentDescriptor


@dataclass(frozen=True, slots=True)
class InstrumentRecord:
    descriptor: InstrumentDescriptor
    aliases: tuple[str, ...] = ()
    ingested_at_utc: datetime | None = None
    calendar_version: str = "v0"
    rule_version: str = "v0"

    @property
    def instrument_id(self) -> InstrumentId:
        return self.descriptor.instrument_id


class InstrumentRepository(Protocol):
    """Storage-agnostic surface implemented by both InMemory and DB backends."""

    def upsert(self, rec: InstrumentRecord) -> InstrumentRecord: ...
    def get(self, iid: InstrumentId) -> InstrumentRecord | None: ...
    def resolve_alias(self, alias: str, *, source: str,
                      as_of: datetime | None = None) -> InstrumentRecord | None: ...
    def all(self) -> Sequence[InstrumentRecord]: ...


@dataclass
class InMemoryInstrumentRepository:
    _by_id: dict[InstrumentId, InstrumentRecord] = field(default_factory=dict)
    # alias_source -> alias -> [(valid_from, valid_to, InstrumentId)]
    _aliases: dict[str, dict[str, list[tuple[datetime, datetime | None, InstrumentId]]]] \
        = field(default_factory=dict)

    def upsert(self, rec: InstrumentRecord) -> InstrumentRecord:
        stored = rec if rec.ingested_at_utc else \
            InstrumentRecord(descriptor=rec.descriptor, aliases=rec.aliases,
                             ingested_at_utc=utcnow(),
                             calendar_version=rec.calendar_version,
                             rule_version=rec.rule_version)
        self._by_id[rec.instrument_id] = stored
        return stored

    def add_alias(self, alias: str, *, source: str, target: InstrumentId,
                  valid_from_utc: datetime, valid_to_utc: datetime | None = None) -> None:
        bucket = self._aliases.setdefault(source, {}).setdefault(alias, [])
        bucket.append((valid_from_utc, valid_to_utc, target))
        bucket.sort(key=lambda x: x[0])

    def get(self, iid: InstrumentId) -> InstrumentRecord | None:
        return self._by_id.get(iid)

    def resolve_alias(self, alias: str, *, source: str,
                      as_of: datetime | None = None) -> InstrumentRecord | None:
        as_of = as_of or utcnow()
        rows = self._aliases.get(source, {}).get(alias, [])
        for vfrom, vto, target in reversed(rows):   # newest-first
            if vfrom <= as_of and (vto is None or as_of < vto):
                return self._by_id.get(target)
        return None

    def all(self) -> Sequence[InstrumentRecord]:
        return list(self._by_id.values())


class InstrumentService:
    """High-level façade used by API routers and the admin MCP.

    Keeps the repository as an injected collaborator so tests never touch
    the real database. Business rules live here (e.g., can-add-alias checks)
    while the repository stays a dumb store.
    """

    def __init__(self, repo: InstrumentRepository) -> None:
        self._repo = repo

    def register(self, descriptor: InstrumentDescriptor, *,
                 aliases: Sequence[str] = (),
                 calendar_version: str = "v0",
                 rule_version: str = "v0") -> InstrumentRecord:
        rec = InstrumentRecord(descriptor=descriptor, aliases=tuple(aliases),
                               calendar_version=calendar_version,
                               rule_version=rule_version)
        return self._repo.upsert(rec)

    def resolve(self, query: str, *, source: str = "canonical",
                as_of: datetime | None = None) -> InstrumentRecord:
        # Try canonical parse first (source="canonical").
        try:
            from packages.common.instrument_id import parse_instrument_id
            iid = parse_instrument_id(query)
            rec = self._repo.get(iid)
            if rec is not None:
                return rec
        except ValueError:
            pass
        rec = self._repo.resolve_alias(query, source=source, as_of=as_of)
        if rec is None:
            raise UnknownInstrumentError(
                f"instrument not found: {query!r} (source={source})",
                details={"query": query, "source": source},
            )
        return rec

    def get(self, iid: InstrumentId) -> InstrumentRecord:
        rec = self._repo.get(iid)
        if rec is None:
            raise UnknownInstrumentError(iid.canonical(),
                                         details={"instrument_id": iid.canonical()})
        return rec

    def all(self) -> Sequence[InstrumentRecord]:
        return self._repo.all()
