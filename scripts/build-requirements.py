"""Build merged requirements.txt from root + all skills/*/requirements.txt.

Usage: python scripts/build-requirements.py > requirements-merged.txt
"""

from pathlib import Path

SEEN: set[str] = set()
ROOT = Path(__file__).resolve().parent.parent


def add(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().strip().split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            SEEN.add(line)


add(ROOT / "requirements.txt")

for skill_dir in sorted((ROOT / "skills").iterdir()):
    if skill_dir.is_dir():
        add(skill_dir / "requirements.txt")

for dep in sorted(SEEN):
    print(dep)
