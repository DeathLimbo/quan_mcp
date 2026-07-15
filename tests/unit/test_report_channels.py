"""Unit tests for packages.reporting.channels."""
from __future__ import annotations

import json
import pathlib

import pytest

from packages.reporting.channels import (
    FileArchiveChannel,
    InMemoryChannel,
    MultiChannelPublisher,
    WebhookChannel,
)


def test_in_memory_channel_records_delivery() -> None:
    ch = InMemoryChannel(channel_id="mem")
    rec = ch.send("Daily", "# hi", meta={"portfolio_id": "PF-1"})
    assert rec.channel == "mem"
    assert rec.subject == "Daily"
    assert rec.markdown == "# hi"
    assert rec.meta == {"portfolio_id": "PF-1"}
    assert len(ch.sent) == 1


def test_file_archive_channel_writes_markdown_and_meta(tmp_path: pathlib.Path) -> None:
    ch = FileArchiveChannel(root=tmp_path)
    rec = ch.send("Daily Report", "# body\ntext",
                  meta={"portfolio_id": "PF-1", "horizon": "5d"})
    # Locate any written .md file (day partition under tmp_path).
    md_files = list(tmp_path.rglob("*.md"))
    meta_files = list(tmp_path.rglob("*.meta.json"))
    assert len(md_files) == 1 and len(meta_files) == 1
    assert "# body" in md_files[0].read_text(encoding="utf-8")
    meta_payload = json.loads(meta_files[0].read_text(encoding="utf-8"))
    assert meta_payload["subject"] == "Daily Report"
    assert meta_payload["meta"]["portfolio_id"] == "PF-1"
    assert rec.delivered_at_utc.tzinfo is not None


def test_webhook_channel_uses_injected_transport() -> None:
    calls: list[tuple[str, dict]] = []

    def fake(url: str, payload: dict) -> None:
        calls.append((url, payload))

    ch = WebhookChannel(channel_id="wecom", webhook_url="https://x/y", transport=fake)
    ch.send("Alert", "**RISK**", meta={"code": "RISK_REJECTED"})
    assert len(calls) == 1
    url, payload = calls[0]
    assert url == "https://x/y"
    assert payload["msgtype"] == "markdown"
    assert "Alert" in payload["markdown"]["content"]
    assert "RISK" in payload["markdown"]["content"]
    assert payload["meta"] == {"code": "RISK_REJECTED"}


def test_multi_channel_publisher_isolates_failures() -> None:
    ok = InMemoryChannel(channel_id="ok")

    class Boom:
        channel_id = "boom"

        def send(self, subject, markdown, *, meta):
            raise RuntimeError("network unavailable")

    pub = MultiChannelPublisher(channels=[ok, Boom(), InMemoryChannel(channel_id="ok2")])
    delivered, failures = pub.publish("S", "M", meta={"k": "v"})
    assert [d.channel for d in delivered] == ["ok", "ok2"]
    assert len(failures) == 1
    assert failures[0][0] == "boom"
    assert isinstance(failures[0][1], RuntimeError)


def test_multi_channel_publisher_empty_meta_default() -> None:
    ok = InMemoryChannel(channel_id="ok")
    pub = MultiChannelPublisher(channels=[ok])
    delivered, failures = pub.publish("S", "M")
    assert not failures
    assert delivered[0].meta == {}
