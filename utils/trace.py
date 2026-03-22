"""
Trace entry dataclass and writer for trace.json.
"""

import json
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class TraceEntry:
    specialist_type: str
    started_at: str          # ISO 8601
    ended_at: str            # ISO 8601
    duration_s: float
    modules_produced: list[str] = field(default_factory=list)
    iterations: int = 0
    error: Optional[str] = None
    blocked_by: Optional[str] = None  # set if this specialist was skipped due to dep failure


def write_trace(entries: list[TraceEntry], path: str) -> None:
    """Serializes trace entries to JSON and writes to path."""
    data = [asdict(e) for e in entries]
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
