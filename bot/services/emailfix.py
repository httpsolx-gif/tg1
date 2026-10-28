"""Автоисправление очевидных опечаток в домене получателя (п.51).

Исправляет домен к крупному провайдеру, если это явная опечатка:
- точное совпадение по `TYPO_DOMAIN_MAP` (приоритет), иначе
- расстояние ≤1 (Optimal String Alignment — замена/вставка/удаление/перестановка
  соседних) до РОВНО ОДНОГО домена из `CANONICAL_EMAIL_DOMAINS`.

Если домен уже валиден (в каноническом наборе) или неоднозначен — не трогаем.
Чистый модуль: зовут и send_core (бот), и API-эндпоинт.
"""
from __future__ import annotations

from common.constants import CANONICAL_EMAIL_DOMAINS, TYPO_DOMAIN_MAP


def _osa(a: str, b: str) -> int:
    """Optimal String Alignment distance (Дамерау-Левенштейн без повторных правок)."""
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return 2  # для нашего порога ≤1 этого достаточно
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


def fix_domain(domain: str) -> str:
    d = (domain or "").strip().lower().strip(".")
    if not d:
        return domain
    if d in CANONICAL_EMAIL_DOMAINS:
        return d
    if d in TYPO_DOMAIN_MAP:
        return TYPO_DOMAIN_MAP[d]
    best = None
    best_dist = 2
    tie = False
    for c in CANONICAL_EMAIL_DOMAINS:
        dist = _osa(d, c)
        if dist < best_dist:
            best_dist, best, tie = dist, c, False
        elif dist == best_dist:
            tie = True
    if best is not None and best_dist <= 1 and not tie:
        return best
    return d


def fix_email(email: str) -> str:
    """Вернуть адрес с исправленным доменом (или исходный, если править нечего)."""
    e = (email or "").strip()
    if "@" not in e:
        return e
    local, _, dom = e.rpartition("@")
    if not local or not dom:
        return e
    fixed = fix_domain(dom)
    return f"{local}@{fixed}"
