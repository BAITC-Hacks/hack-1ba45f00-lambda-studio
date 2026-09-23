"""Обёртка над mycelium.validate: все проверки §16 должны пройти."""
from __future__ import annotations

from mycelium.validate import validate


def test_outputs_valid():
    rep = validate()
    assert not rep.errors, "\n".join(rep.errors)
