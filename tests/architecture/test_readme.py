"""The README covers what the assignment asks for, and its make targets exist."""

import re
from pathlib import Path

SECTIONS = (
    "## Quickstart",
    "## Commands",
    "## Configuration",
    "## How it works",
    "## Evaluation",
    "## Testing",
    "## Extending",
    "## Limitations and incomplete functionality",
    "## External dependencies",
    "## Deliverables",
)


def test_readme_has_every_section(repo_root: Path) -> None:
    text = (repo_root / "README.md").read_text(encoding="utf-8")
    for heading in SECTIONS:
        assert f"\n{heading}\n" in text, heading


def test_readme_make_targets_exist(repo_root: Path) -> None:
    text = (repo_root / "README.md").read_text(encoding="utf-8")
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    targets = set(re.findall(r"`make ([a-z-]+)", text))
    assert targets
    for target in sorted(targets):
        assert re.search(rf"^{target}:", makefile, re.MULTILINE), target
