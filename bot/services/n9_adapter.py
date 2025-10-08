import os
import httpx

# Можно переопределить: export N9_ENDPOINT="http://127.0.0.1:8000/shorten"
N9_ENDPOINT = os.getenv("N9_ENDPOINT", "http://127.0.0.1:8000/shorten")
N9_TIMEOUT  = float(os.getenv("N9_TIMEOUT", "50"))

def get_short_url(url: str, alias: str | None = None, password: str | None = None,
                  headless: bool = True, proxy: str | None = None, proxies_file: str | None = None) -> str:
    """
    Совместимый по сигнатуре адаптер.
    Игнорирует proxy/headless внутри — коротит через локальный сервис на 127.0.0.1.
    """
    payload = {"url": url}
    if alias:    payload["alias"] = alias
    if password: payload["password"] = password

    r = httpx.post(N9_ENDPOINT, json=payload, timeout=N9_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    short = (data or {}).get("short", "")
    if (data or {}).get("status") == "OK" and short.startswith("https://n9.cl/"):
        return short
    raise RuntimeError(f"n9_error: {data}")
