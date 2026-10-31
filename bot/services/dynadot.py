"""Минимальный клиент Dynadot API3 — авто-смена NS у регистратора (как namesilo.py).

API: GET https://api.dynadot.com/api3.json?key=...&command=<cmd>&...
Успех — ResponseCode 0 (в объекте <Command>Response / Response). Ключ может требовать
белый список IP в ЛК Dynadot (Tools → API) — тогда ошибка придёт в ответе.
"""
from __future__ import annotations

import aiohttp

BASE = "https://api.dynadot.com/api3.json"


class DynadotError(RuntimeError):
    pass


async def _call(command: str, key: str, params: dict, timeout: int = 25) -> dict:
    q = {"key": key, "command": command, **params}
    to = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=to) as s:
        async with s.get(BASE, params=q) as r:
            data = await r.json(content_type=None)
    # ответ: {"SetNsResponse": {...}} или {"Response": {...}} при ошибке
    body = next(iter(data.values()), {}) if isinstance(data, dict) and data else {}
    code = str(body.get("ResponseCode", body.get("ResponseHeader", {}).get("ResponseCode", "")))
    if code != "0":
        err = body.get("Error") or body.get("ResponseHeader", {}).get("Error") or data
        raise DynadotError(f"{command}: code={code} error={err}")
    return body


async def change_nameservers(key: str, domain: str, nameservers: list[str]) -> str:
    ns = [n for n in nameservers if n]
    if len(ns) < 2:
        raise DynadotError("нужно минимум 2 неймсервера")
    params = {"domain": domain}
    for i, n in enumerate(ns):
        params[f"ns{i}"] = n
    body = await _call("set_ns", key, params)
    return str(body.get("Status") or "ok")


async def verify_key(key: str) -> tuple[bool, str]:
    """Проверить ключ лёгким вызовом (account_info)."""
    try:
        await _call("account_info", key, {})
        return True, "ok"
    except Exception as e:
        return False, str(e)
