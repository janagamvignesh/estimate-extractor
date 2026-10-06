import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
SAMPLE = ROOT / "data" / "sample"


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


@pytest.fixture
def ref_b():
    return load(FIX / "CLAIM_B.reference.json")


@pytest.fixture
def ref_c():
    return load(FIX / "CLAIM_C.reference.json")


@pytest.fixture
def sys_a():
    return load(SAMPLE / "CLAIM_A_data.json")


@pytest.fixture
def sys_b():
    return load(SAMPLE / "CLAIM_B_data.json")


@pytest.fixture
def sys_c():
    return load(SAMPLE / "CLAIM_C_data.json")
