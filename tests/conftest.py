import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUNDLES = ROOT / "docs" / "synthetic"
MATCH_IDS = [m["id"] for m in json.loads((BUNDLES / "index.json").read_text())["matches"]]


def load_bundle(name):
    return json.loads((BUNDLES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def bundle():
    """The first published match bundle (read-only: tests must not mutate it)."""
    return load_bundle(f"{MATCH_IDS[0]}.json")
