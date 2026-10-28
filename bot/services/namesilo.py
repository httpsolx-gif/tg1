"""Минимальный клиент NameSilo API — авто-смена NS у регистратора.

Нужен ровно для «автоматом» в флоу short-доменов: прописать неймсерверы CF у
домена, зарегистрированного в NameSilo, без ручного захода в ЛК регистратора.
API: GET https://www.namesilo.com/api/<op>?version=1&type=xml&key=...&domain=...
"""
from __future__ import annotations

import re
from typing import Optional

import aiohttp

BASE = "https://www.namesilo.com/api"


class NameSiloError(RuntimeError):
    pass


def _field(xml: str, tag: str) -> Optional[str]:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.S)
    return m.group(1).strip() if m else None


async def _call(op: str, key: str, params: dict, timeout: int = 25) -> str:
    q = {"version": "1", "type": "xml", "key": key, **params}
    to = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=to) as s:
        async with s.get(f"{BASE}/{op}", params=q) as r:
            xml = await r.text()
    code = _field(xml, "code")
    if code != "300":  # 300 = success
        raise NameSiloError(f"{op}: code={code} detail={_field(xml, 'detail')}")
    return xml


async def change_nameservers(key: str, domain: str, nameservers: list[str], *,
                             retries: int = 3) -> str:
    """Сменить NS домена на переданные (нужно ≥2). → detail.

    NameSilo иногда отдаёт транзиентный code=201 (Internal error) — ретраим.
    """
    import asyncio
    ns = [n for n in nameservers if n]
    if len(ns) < 2:
        raise NameSiloError("нужно минимум 2 неймсервера")
    params = {"domain": domain}
    for i, n in enumerate(ns, start=1):
        params[f"ns{i}"] = n
    last = None
    for attempt in range(retries):
        try:
            xml = await _call("changeNameServers", key, params)
            return _field(xml, "detail") or "ok"
        except NameSiloError as e:
            last = e
            if "code=201" not in str(e):  # не транзиентная — не ретраим
                raise
            await asyncio.sleep(3)
    raise last or NameSiloError("changeNameServers: нет попыток (retries<=0)")


async def get_nameservers(key: str, domain: str) -> list[str]:
    """Текущие NS домена (getDomainInfo)."""
    xml = await _call("getDomainInfo", key, {"domain": domain})
    return re.findall(r"<nameserver\b[^>]*>(.*?)</nameserver>", xml, re.S)


async def verify_key(key: str) -> tuple[bool, str]:
    """Проверить ключ лёгким вызовом (listDomains)."""
    try:
        await _call("listDomains", key, {})
        return True, "ok"
    except Exception as e:
        return False, str(e)
