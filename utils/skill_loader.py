"""
Loads all *.md files from the skills/ directory and returns their combined
content for injection into planner and specialist prompts.

Each skill file is a self-contained technical reference (e.g. colyseus.md).
They are included verbatim so agents can use the documented APIs correctly.
"""
from __future__ import annotations
from pathlib import Path

SKILLS_DIR = Path(__file__).parent.parent / "skills"


def load_skills() -> str:
    """
    Reads all *.md files from skills/ (sorted by name) and concatenates them
    into a single string, each prefixed with a heading showing the filename.
    Returns an empty string if the directory is missing or empty.
    """
    if not SKILLS_DIR.is_dir():
        return ""

    parts: list[str] = []
    for skill_file in sorted(SKILLS_DIR.glob("*.md")):
        content = skill_file.read_text().strip()
        if content:
            parts.append(f"### Skill: {skill_file.stem}\n\n{content}")

    return "\n\n---\n\n".join(parts)
