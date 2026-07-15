"""Audit log: append-only, hash-chained, tamper-evident.

Every write / admin action MUST call ``AuditLog.record(...)``. Required fields
per spec §顶层约束:
    actor_id, actor_type, action, resource_type, resource_id,
    before_hash, after_hash, request_id, trace_id, created_at,
    ip_or_service_identity, approval_id
"""
from packages.audit.record import AuditEvent, AuditLog, canonical_hash

__all__ = ["AuditEvent", "AuditLog", "canonical_hash"]
