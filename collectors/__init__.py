"""Read-only observation collectors for Ledger automation."""

from .run import CollectorBusyError, collect

__all__ = ["CollectorBusyError", "collect"]
