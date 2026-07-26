"""Closed metadata vocabularies shared by projection and private UI state."""


PRIORITIES = ("P0", "P1", "P2")
WORK_TYPES = (
    "security",
    "feature",
    "bug-fix",
    "improvement",
    "maintenance",
    "documentation",
    "research",
)


def normalize_priority(value):
    """Return one canonical priority or ``None`` without guessing aliases."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper().replace("PRIORITY ", "")
    return normalized if normalized in PRIORITIES else None


def normalize_work_type(value):
    """Return one canonical work type or ``None`` without fuzzy matching."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in WORK_TYPES else None
