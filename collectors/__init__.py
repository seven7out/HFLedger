"""Read-only observation collectors for HFLedger automation."""

from .run import CollectorBusyError, collect

__all__ = ["CollectorBusyError", "collect"]
