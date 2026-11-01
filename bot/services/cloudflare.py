"""Минимальный async-клиент Cloudflare API v4 для личных short-доменов.

Делает ровно то, что нужно флоу: создать зону, узнать NS/статус, включить
SSL=Full(strict), выпустить origin-сертификат по CSR, создать проксированную
A-запись. Токен — scoped API token (Bearer).
"""
from __future__ import annotations

from typing import Optional

import aiohttp

API = "https://api.cloudflare.com/client/v4"


class CFError(RuntimeError):
    pass


async def _api(token: str, method: str, path: str, *, json: Optional[dict] = None,
               timeout: int = 25) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = API + path
    to = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=to) as s:
        async with s.request(method, url, headers=headers, json=json) as r:
            try:
                data = await r.json()
            except Exception:
                text = await r.text()
                raise CFError(f"HTTP {r.status}: {text[:300]}")
    if not data.get("success", False):
        errs = data.get("errors") or []
        msg = "; ".join(f"{e.get('code')}:{e.get('message')}" for e in errs) or str(data)[:300]
        raise CFError(msg)
    return data.get("result") or {}


def _errcodes(e: CFError) -> set[int]:
    out = set()
    for part in str(e).split(";"):
        part = part.strip()
        if ":" in part and part.split(":", 1)[0].strip().isdigit():
            out.add(int(part.split(":", 1)[0].strip()))
    return out


async def get_zone(token: str, name: str) -> Optional[dict]:
    res = await _api(token, "GET", f"/zones?name={name}")
    if isinstance(res, list):
        return res[0] if res else None
    return res or None


async def create_zone(token: str, name: str, *, recreate: bool = False) -> tuple[str, list[str]]:
    """Создать зону. → (zone_id, [nameservers]).

    recreate=True: если зона уже есть в аккаунте — удалить и создать заново (чистая зона,
    без старых DNS-записей, которые могли светить IP). Иначе существующая подхватывается.
    """
    if recreate:
        z = await get_zone(token, name)
        if z:
            await _api(token, "DELETE", f"/zones/{z['id']}")
    try:
        res = await _api(token, "POST", "/zones", json={"name": name, "type": "full"})
    except CFError as e:
        # 1061 — зона уже существует: подхватываем её (при recreate сюда попадаем,
        # только если зона в ЧУЖОМ аккаунте CF — её не удалить)
        if 1061 in _errcodes(e):
            if recreate:
                raise CFError(f"зона {name} уже есть в другом аккаунте Cloudflare — удалите её там") from e
            z = await get_zone(token, name)
            if not z:
                raise
            return z["id"], z.get("name_servers") or []
        raise
    return res["id"], res.get("name_servers") or []


async def zone_status(token: str, zone_id: str) -> str:
    res = await _api(token, "GET", f"/zones/{zone_id}")
    return res.get("status", "unknown")


async def activation_check(token: str, zone_id: str) -> None:
    """Попросить CF немедленно перепроверить NS (ускоряет переход в active)."""
    try:
        await _api(token, "PUT", f"/zones/{zone_id}/activation_check")
    except CFError:
        pass  # не критично — CF и сам периодически проверяет


async def set_ssl(token: str, zone_id: str, mode: str = "strict") -> None:
    await _api(token, "PATCH", f"/zones/{zone_id}/settings/ssl", json={"value": mode})


async def always_use_https(token: str, zone_id: str, on: bool = True) -> None:
    await _api(token, "PATCH", f"/zones/{zone_id}/settings/always_use_https",
               json={"value": "on" if on else "off"})


RATE_LIMIT_DESC = "mailer: rate limit (short)"


async def set_rate_limit(token: str, zone_id: str, requests: int = 30) -> None:
    """Rate limit на зону: > requests запросов за 10 с с одного IP → блок на 10 с.

    Рамки Free-плана: 1 правило, период/таймаут 10 с, счёт по IP (+colo), в выражении
    только путь. PUT entrypoint фазы http_ratelimit заменяет набор целиком — зона наша.
    """
    rule = {
        "description": RATE_LIMIT_DESC,
        "expression": '(http.request.uri.path ne "/healthz")',
        "action": "block",
        "ratelimit": {
            "characteristics": ["cf.colo.id", "ip.src"],
            "period": 10,
            "requests_per_period": int(requests),
            "mitigation_timeout": 10,
        },
    }
    await _api(token, "PUT", f"/zones/{zone_id}/rulesets/phases/http_ratelimit/entrypoint",
               json={"rules": [rule]})


_RL_QUERY = """query($zone: String!, $since: Time!) { viewer { zones(filter: {zoneTag: $zone}) {
  firewallEventsAdaptiveGroups(limit: 5, orderBy: [count_DESC],
      filter: {datetime_geq: $since, source: "ratelimit"}) { count dimensions { clientIP } } } } }"""


async def ratelimit_hits(token: str, zone_id: str, since_iso: str, timeout: int = 25) -> tuple[int, list[tuple[str, int]]]:
    """Сколько запросов зарезал rate limit зоны с момента since (GraphQL Analytics).
    → (всего в топе, [(ip, count)…]). Нужно право токена Zone → Analytics → Read."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"query": _RL_QUERY, "variables": {"zone": zone_id, "since": since_iso}}
    to = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=to) as s:
        async with s.post(API + "/graphql", headers=headers, json=body) as r:
            try:
                data = await r.json()
            except Exception:
                raise CFError(f"HTTP {r.status}: {(await r.text())[:200]}")
    if data.get("errors"):
        raise CFError("; ".join(str(e.get("message")) for e in data["errors"])[:300])
    zones = ((data.get("data") or {}).get("viewer") or {}).get("zones") or []
    groups = zones[0].get("firewallEventsAdaptiveGroups") if zones else []
    top = [((g.get("dimensions") or {}).get("clientIP") or "?", int(g.get("count") or 0)) for g in groups or []]
    return sum(c for _, c in top), top


async def create_origin_cert(token: str, hostnames: list[str], csr: str,
                             validity_days: int = 5475) -> tuple[str, str]:
    """Выпустить origin-сертификат по CSR. → (cert_pem, cert_id).

    Требует у токена права SSL and Certificates. Приватный ключ не покидает ноду
    (CSR сгенерирован там же).
    """
    res = await _api(token, "POST", "/certificates", json={
        "hostnames": hostnames,
        "requested_validity": validity_days,
        "request_type": "origin-rsa",
        "csr": csr,
    })
    return res["certificate"], res.get("id") or ""


async def upsert_a_record(token: str, zone_id: str, name: str, ip: str,
                          proxied: bool = True) -> str:
    """Создать/обновить A-запись (ВСЕГДА проксированную). → record_id."""
    existing = await _api(token, "GET", f"/zones/{zone_id}/dns_records?type=A&name={name}")
    body = {"type": "A", "name": name, "content": ip, "proxied": proxied, "ttl": 1}
    if isinstance(existing, list) and existing:
        rid = existing[0]["id"]
        await _api(token, "PUT", f"/zones/{zone_id}/dns_records/{rid}", json=body)
        return rid
    res = await _api(token, "POST", f"/zones/{zone_id}/dns_records", json=body)
    return res["id"]


async def verify_token(token: str) -> tuple[bool, str]:
    """Проверить, что токен валиден (GET /user/tokens/verify)."""
    try:
        res = await _api(token, "GET", "/user/tokens/verify")
        return True, res.get("status", "ok")
    except CFError as e:
        return False, str(e)


async def hide_origin_ip(token: str, zone_id: str, ip: str) -> list[str]:
    """Утечка IP через DNS: все записи зоны, где встречается IP origin, должны быть
    проксированы. A/AAAA/CNAME без облака — включаем облако; непроксируемые (MX/TXT/SPF/SRV…)
    с этим IP — удаляем. → список того, что исправлено (для лога)."""
    fixed: list[str] = []
    page = 1
    while True:
        recs = await _api(token, "GET", f"/zones/{zone_id}/dns_records?per_page=100&page={page}")
        if not isinstance(recs, list) or not recs:
            break
        for r in recs:
            if ip not in str(r.get("content", "")):
                continue
            if r.get("proxied"):
                continue
            rid, name, typ = r["id"], r.get("name"), r.get("type")
            if typ in ("A", "AAAA", "CNAME"):
                body = {"type": typ, "name": name, "content": r["content"], "proxied": True, "ttl": 1}
                await _api(token, "PUT", f"/zones/{zone_id}/dns_records/{rid}", json=body)
                fixed.append(f"{typ} {name}: включено облако")
            else:
                await _api(token, "DELETE", f"/zones/{zone_id}/dns_records/{rid}")
                fixed.append(f"{typ} {name}: удалена (светила IP)")
        if len(recs) < 100:
            break
        page += 1
    return fixed


async def cf_ip_ranges(timeout: int = 20) -> tuple[list[str], list[str]]:
    """Диапазоны IP Cloudflare (v4, v6) — для фаервола origin. Без авторизации."""
    to = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=to) as s:
        async with s.get(f"{API}/ips") as r:
            data = await r.json()
    if not data.get("success"):
        raise CFError("ips endpoint failed")
    res = data["result"]
    return res.get("ipv4_cidrs", []), res.get("ipv6_cidrs", [])
