from packages.audit.record import AuditLog, InMemoryAuditSink, canonical_hash


def test_hash_stable_across_key_order():
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


def test_hash_changes_on_value_change():
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_audit_log_captures_before_after():
    sink = InMemoryAuditSink()
    log = AuditLog(sink)
    event = log.record(
        actor_id="alice",
        actor_type="human",
        action="model.publish",
        resource_type="model",
        resource_id="model-cn-v1",
        before={"state": "CANDIDATE"},
        after={"state": "PRODUCTION"},
        request_id="req-1",
        trace_id="tr-1",
        ip_or_service_identity="10.0.0.1",
        approval_id="apr-42",
    )
    assert len(sink.events) == 1
    assert event.before_hash != event.after_hash
    assert event.approval_id == "apr-42"
    row = event.to_row()
    assert row["actor_id"] == "alice"
    assert row["metadata"] == "{}"


def test_audit_log_no_before_after_optional():
    sink = InMemoryAuditSink()
    log = AuditLog(sink)
    e = log.record(
        actor_id="worker",
        actor_type="service",
        action="ingest.run",
        resource_type="dataset",
        resource_id="cn.eod.2026-01-01",
        ip_or_service_identity="worker-1",
    )
    assert e.before_hash is None
    assert e.after_hash is None
