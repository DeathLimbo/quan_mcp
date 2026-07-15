"""Append-only audit event + writer.

Storage in phase 0 is Postgres (`audit_events` table, alembic migration 0002).
Only inserts allowed; a nightly job re-hashes rows to verify chain integrity.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Literal, Protocol

from packages.common.time_utils import utcnow


ActorType = Literal["human", "service", "agent", "system"]


def canonical_hash(obj: Any) -> str:
    """Deterministic sha256 of a JSON-serializable payload."""
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class AuditEvent:
    actor_id: str
    actor_type: ActorType
    action: str                    # e.g. model.publish / risk.override / ingest.run
    resource_type: str             # e.g. model / dataset / order_intent
    resource_id: str
    before_hash: str | None
    after_hash: str | None
    request_id: str | None
    trace_id: str | None
    ip_or_service_identity: str
    approval_id: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["metadata"] = json.dumps(self.metadata, sort_keys=True, default=str)
        return row


class AuditSink(Protocol):
    def insert(self, event: AuditEvent) -> None: ...


class InMemoryAuditSink:
    """Fallback sink used in unit tests / bootstrap before DB is up."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def insert(self, event: AuditEvent) -> None:
        self.events.append(event)


class AuditLog:
    """Facade. Callers never touch the sink directly."""

    def __init__(self, sink: AuditSink) -> None:
        self._sink = sink

    def record(
        self,
        *,
        actor_id: str,
        actor_type: ActorType,
        action: str,
        resource_type: str,
        resource_id: str,
        before: Any = None,
        after: Any = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        ip_or_service_identity: str = "unknown",
        approval_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            actor_id=actor_id,
            actor_type=actor_type,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before_hash=canonical_hash(before) if before is not None else None,
            after_hash=canonical_hash(after) if after is not None else None,
            request_id=request_id,
            trace_id=trace_id,
            ip_or_service_identity=ip_or_service_identity,
            approval_id=approval_id,
            metadata=metadata or {},
        )
        self._sink.insert(event)
        return event

    def events(self) -> list[AuditEvent]:
        """Return recorded events if the underlying sink supports it.

        Callers should treat this as an *ops / test* utility, not a query
        API — the production sink is Postgres and query goes through SQL.
        """
        ev = getattr(self._sink, "events", None)
        if callable(ev):
            return list(ev())
        if isinstance(ev, list):
            return list(ev)
        return []
