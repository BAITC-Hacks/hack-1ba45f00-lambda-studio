from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session", autouse=True)
def pipeline_outputs():
    """Выходы пайплайна должны существовать; если нет — считаем их один раз."""
    if not (ROOT / "out" / "web" / "cards.json").exists():
        from mycelium import pipeline
        pipeline.run()
