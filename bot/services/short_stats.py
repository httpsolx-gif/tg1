"""Чтение статистики переходов личного short-домена с origin-ноды по SSH.

Мини-сервис на ноде пишет каждый переход в sqlite с дедупом по slug
(`INSERT OR IGNORE`), поэтому 1 slug = максимум 1 переход. Здесь — читаем счётчики.
"""
from __future__ import annotations

import json
from typing import Optional

from bot.services.node_runner import _run_on_node

_READER = r'''
import sqlite3, json, os
DB = "/var/lib/shortcf/clicks.db"
DOMAIN = "__DOMAIN__"
if not os.path.exists(DB):
    print(json.dumps({"unique": 0, "recent": []})); raise SystemExit(0)
con = sqlite3.connect(DB, timeout=5)
try:
    total = con.execute("SELECT count(*) FROM clicks WHERE domain=?", (DOMAIN,)).fetchone()[0]
    rows = con.execute("SELECT ts, target FROM clicks WHERE domain=? ORDER BY ts DESC LIMIT 8",
                       (DOMAIN,)).fetchall()
except Exception:
    total, rows = 0, []
print(json.dumps({"unique": total, "recent": rows}))
'''


_TRACKS_READER = r'''
import sqlite3, json, os
DB = "/var/lib/shortcf/clicks.db"
if not os.path.exists(DB):
    print("[]"); raise SystemExit(0)
con = sqlite3.connect(DB, timeout=5)
try:
    rows = con.execute("SELECT DISTINCT track FROM clicks WHERE track IS NOT NULL").fetchall()
    print(json.dumps([r[0] for r in rows]))
except Exception:
    print("[]")
'''


async def clicked_tracks(node, *, timeout: int = 30) -> set:
    """Множество трек-id переходов с ноды (дедуп на уровне slug уже сделан)."""
    for interp in ("python3 -", "python -"):
        try:
            r = await _run_on_node(node, interp, input=_TRACKS_READER, timeout=timeout)
        except Exception:
            return set()
        if r.exit_status == 127:
            continue
        try:
            return set(json.loads((r.stdout or "[]").strip().splitlines()[-1]))
        except Exception:
            return set()
    return set()


async def reset_clicks(node, *, timeout: int = 30) -> bool:
    """Очистить лог переходов на ноде (DELETE FROM clicks)."""
    src = ('import sqlite3,os\n'
           'DB="/var/lib/shortcf/clicks.db"\n'
           'if os.path.exists(DB):\n'
           '    c=sqlite3.connect(DB,timeout=5); c.execute("DELETE FROM clicks"); c.commit(); c.close()\n'
           'print("ok")\n')
    for interp in ("python3 -", "python -"):
        try:
            r = await _run_on_node(node, interp, input=src, timeout=timeout)
        except Exception:
            return False
        if r.exit_status == 127:
            continue
        return (r.stdout or "").strip().endswith("ok")
    return False


async def click_stats(node, domain: str, *, timeout: int = 30) -> dict:
    """{'unique': N, 'recent': [[ts, target], ...]} — переходы по домену (дедуп по slug)."""
    src = _READER.replace("__DOMAIN__", domain)
    for interp in ("python3 -", "python -"):
        try:
            r = await _run_on_node(node, interp, input=src, timeout=timeout)
        except Exception as e:
            return {"error": f"SSH: {type(e).__name__}: {e}"}
        if r.exit_status == 127:
            continue
        out = (r.stdout or "").strip()
        try:
            return json.loads(out.splitlines()[-1])
        except Exception:
            return {"error": (r.stderr or out or f"exit {r.exit_status}")[:200]}
    return {"error": "python3/python не найден на ноде"}
