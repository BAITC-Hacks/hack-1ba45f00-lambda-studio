"""Рекурсивная сверка формы JSON с моком: ключи, вложенность, типы (int/float — «число»; null допускается)."""
from __future__ import annotations


def _kind(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    return type(v).__name__


def diff_shape(mock, real, path: str = "$", loose_null: bool = True) -> list[str]:
    errs: list[str] = []
    km, kr = _kind(mock), _kind(real)
    if "null" in (km, kr) and loose_null:
        return errs
    if km != kr:
        return [f"{path}: тип {kr}, в моке {km}"]
    if isinstance(mock, dict):
        # словарь id → объект (cards) или N → объект (resilience): сверяем значения с первым значением мока
        dyn = mock and all(k.isdigit() for k in mock)
        if dyn:
            ref = next(iter(mock.values()))
            for k, v in real.items():
                errs += diff_shape(ref, v, f"{path}.{k}", loose_null)
            return errs
        for k in mock:
            if k not in real:
                errs.append(f"{path}: нет ключа «{k}»")
            else:
                errs += diff_shape(mock[k], real[k], f"{path}.{k}", loose_null)
        for k in real:
            if k not in mock:
                errs.append(f"{path}: лишний ключ «{k}»")
    elif isinstance(mock, list) and mock and real:
        for i, v in enumerate(real):
            ref = mock[min(i, len(mock) - 1)] if not isinstance(mock[0], dict) else _best(mock, v)
            errs += diff_shape(ref, v, f"{path}[{i}]", loose_null)
            if len(errs) > 20:
                break
    return errs


def _best(items: list, v):
    """Для списков объектов разной формы (rule_trace: matched / skipped) берём мок с тем же набором ключей."""
    if isinstance(v, dict):
        for m in items:
            if isinstance(m, dict) and set(m) == set(v):
                return m
    return items[0]
