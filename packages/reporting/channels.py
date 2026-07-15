"""Reporting channels (§90) —企业微信 / 钉钉 / 邮件 / 文件归档.

Each channel implements ``ReportChannel``:
    def send(self, subject: str, markdown: str, *, meta: dict[str, str]) -> None

Real HTTP integrations are behind a ``Transport`` seam so tests never talk
to the network. The default transport for WeCom/DingTalk is a "dry run"
that appends the payload to an in-memory log; production overrides the
transport with an HTTP client.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Protocol


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    channel: str
    subject: str
    markdown: str
    meta: dict[str, str]
    delivered_at_utc: datetime


class ReportChannel(Protocol):
    channel_id: str

    def send(self, subject: str, markdown: str, *, meta: dict[str, str]) -> DeliveryRecord: ...


Transport = Callable[[str, dict], None]     # webhook_url, payload_dict


def _dry_run_transport(url: str, payload: dict) -> None:
    # Placeholder — real production wiring uses httpx.
    _ = url, payload


@dataclass
class InMemoryChannel:
    """Test double + fallback. Appends records to ``sent``."""

    channel_id: str = "memory"
    sent: list[DeliveryRecord] = field(default_factory=list)

    def send(self, subject: str, markdown: str, *, meta: dict[str, str]) -> DeliveryRecord:
        from packages.common.time_utils import utcnow
        rec = DeliveryRecord(channel=self.channel_id, subject=subject,
                             markdown=markdown, meta=dict(meta),
                             delivered_at_utc=utcnow())
        self.sent.append(rec)
        return rec


@dataclass
class FileArchiveChannel:
    """Persists rendered reports to disk under ``root/{yyyy}/{mm}/{dd}/...``."""

    channel_id: str = "file"
    root: pathlib.Path = field(default_factory=lambda: pathlib.Path("./reports"))

    def send(self, subject: str, markdown: str, *, meta: dict[str, str]) -> DeliveryRecord:
        from packages.common.time_utils import utcnow
        now = utcnow()
        day = self.root / f"{now:%Y}" / f"{now:%m}" / f"{now:%d}"
        day.mkdir(parents=True, exist_ok=True)
        safe_subject = subject.replace("/", "_").replace(" ", "_")[:80]
        base = day / f"{now:%Y%m%dT%H%M%S}_{safe_subject}"
        base.with_suffix(".md").write_text(markdown, encoding="utf-8")
        base.with_suffix(".meta.json").write_text(
            json.dumps({"subject": subject, "meta": meta,
                        "delivered_at_utc": now.isoformat()},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        return DeliveryRecord(channel=self.channel_id, subject=subject,
                              markdown=markdown, meta=dict(meta),
                              delivered_at_utc=now)


@dataclass
class WebhookChannel:
    """WeCom / DingTalk / email-webhook uniform surface.

    Real transport is injected. Payload shape mimics WeCom markdown message:
        {"msgtype": "markdown", "markdown": {"content": "..."}}
    """

    channel_id: str
    webhook_url: str
    transport: Transport = _dry_run_transport

    def send(self, subject: str, markdown: str, *, meta: dict[str, str]) -> DeliveryRecord:
        from packages.common.time_utils import utcnow
        body = "\n\n".join([f"# {subject}", markdown])
        payload = {"msgtype": "markdown", "markdown": {"content": body}, "meta": meta}
        self.transport(self.webhook_url, payload)
        return DeliveryRecord(channel=self.channel_id, subject=subject,
                              markdown=markdown, meta=dict(meta),
                              delivered_at_utc=utcnow())


@dataclass
class MultiChannelPublisher:
    """Fan-out publisher — publishes to N channels and collects DeliveryRecords.

    Failures are captured per-channel: an exception in one channel does not
    abort the others (spec §90: 报告渠道失败 must be surfaced but not fail-open).
    """

    channels: list[ReportChannel] = field(default_factory=list)

    def publish(self, subject: str, markdown: str, *,
                meta: dict[str, str] | None = None
                ) -> tuple[list[DeliveryRecord], list[tuple[str, Exception]]]:
        meta = meta or {}
        delivered: list[DeliveryRecord] = []
        failures: list[tuple[str, Exception]] = []
        for ch in self.channels:
            try:
                delivered.append(ch.send(subject, markdown, meta=meta))
            except Exception as e:                 # noqa: BLE001 — see docstring
                failures.append((ch.channel_id, e))
        return delivered, failures
