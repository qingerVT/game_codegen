"""
Reads the '## Game Design Constraints' section from DESIGN.md and returns it
as a string for injection into planner and specialist prompts.
"""
from __future__ import annotations
import re
from pathlib import Path

DESIGN_MD_PATH = Path(__file__).parent.parent / "DESIGN.md"


def load_design_constraints() -> str:
    """
    Extracts the '## Game Design Constraints' section from DESIGN.md.
    Returns the section text (without the heading), or empty string if not found.
    """
    text = DESIGN_MD_PATH.read_text()
    # Match from '## Game Design Constraints' up to the next '---' or '## ' heading
    match = re.search(
        r'## Game Design Constraints\n(.*?)(?=\n---\n|\n## |\Z)',
        text,
        re.DOTALL,
    )
    if not match:
        return ""
    # Strip the italic note line starting with '>'
    section = match.group(1)
    section = re.sub(r'^>.*\n?', '', section, flags=re.MULTILINE)
    return section.strip()
