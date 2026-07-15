"""Worker package. ``main`` is imported lazily so the RQ dependency (which
changes API between major versions) only matters when actually running the
worker, not when introspecting :mod:`apps.worker.tasks` from tests."""
from __future__ import annotations


def main(*args, **kwargs):
    from apps.worker.main import main as _main
    return _main(*args, **kwargs)


__all__ = ["main"]
