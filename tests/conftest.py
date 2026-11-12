import json
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture():
    def load(name):
        d = json.loads((FIX / name).read_text())
        return d.get("body", d) if isinstance(d, dict) and "body" in d and "url" in d else d
    return load
