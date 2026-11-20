from pathlib import Path

import pytest

from bottom.config import load_config

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    return load_config(ROOT / "config.yaml")
