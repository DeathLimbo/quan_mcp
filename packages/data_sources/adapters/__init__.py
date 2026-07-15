"""Concrete data-source adapters.

Each adapter implements the Protocols in packages.data_sources.contracts.
No downstream module may import a concrete adapter directly; use the
``get_adapter(name)`` registry instead.
"""
