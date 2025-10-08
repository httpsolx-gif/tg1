import os, re, asyncio, datetime, pathlib
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
from playwright.async_api import async_playwright

try:
    from playwright_stealth import stealth_async
except Exception:
    stealth_async = None  # опционально

BROWSER = os.getenv("BROWSER", "chromium")      # chromium | firefox | webkit
CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "8"))
DEBUG_DIR = pathlib.Path(os.path.expanduser("~/n9/debug")); DEBUG_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI()
_sem = asyncio.Semaphore(CONCURRENCY)
_pl = _browser = _ctx = None

BLOCKLIST = {"", "en","es","pt","de","fr","blog","pricing","price","terms","privacy",
             "contact","login","register","signup","api","about","support","n"}

def nowstamp():
    import datetime as _dt
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")

def pick_short(candidates:list[str]) -> str | None:
    cleaned = []
    for url in candidates:
        if not url.startswith("https://n9.cl/"):
            continue
        slug = url.split("https://n9.cl/",1)[1]
        slug = slug.split("#",1)[0].split("?",1)[0]
        if "/" in slug:
            continue
        if slug in BLOCKLIST:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,}", slug):
            continue
        cleaned.append(url)
    if not cleaned:
        return None
    cleaned.sort(key=lambda u:(len(u), candidates.index(u)))
    return cleaned[-1]

async def dump_debug(page, label):
    ts = nowstamp()
    html_path = DEBUG_DIR / f"{ts}-{label}.html"
    png_path  = DEBUG_DIR / f"{ts}-{label}.png"
    try:
        html = await page.content()
        html_path.write_text(html, encoding="utf-8")
        try:
            await page.screenshot(path=str(png_path), full_page=True)
        except Exception:
            pass
        print(f"[DEBUG] Saved {html_path.name} {png_path.name}")
    except Exception as e:
        print(f"[DEBUG] dump_debug failed: {e}")

class Payload(BaseModel):
    url: HttpUrl
    alias: str | None = None
    password: str | None = None

@app.on_event("startup")
async def startup():
    global _pl, _browser, _ctx
    _pl = await async_playwright().start()
    launcher = getattr(_pl, BROWSER)
    _browser = await launcher.launch(
        headless=True,
        args=[
            "--no-sandbox","--disable-gpu","--disable-dev-shm-usage",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
    )
    _ctx = await _browser.new_context(
        locale="en-US",
        user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"),
        viewport={"width": 1200, "height": 800},
    )
    # права на буфер обмена — некоторые шортнеры копируют ссылку туда
    try:
        await _ctx.grant_permissions(["clipboard-read","clipboard-write"], origin="https://n9.cl")
    except Exception:
        pass
    await _ctx.route("**/*", lambda r: asyncio.create_task(_filter(r)))
    print("[BOOT] Context ready")

async def _filter(route):
    t = route.request.resource_type
    if t in {"image","font","media","stylesheet"}:
        await route.abort()
    else:
        await route.continue_()

@app.on_event("shutdown")
async def shutdown():
    for obj in (_ctx, _browser, _pl):
        if obj:
            try:
                await obj.close()
            except Exception:
                pass

@app.get("/health")
async def health():
    return {"status": "ok"}

async def harvest_candidates(page):
    # 1) <a href="https://n9.cl/...">
    links = await page.eval_on_selector_all(
        'a[href^="https://n9.cl/"]', "els => els.map(e=>e.href)"
    )
    # 2) input value=...
    inputs = await page.eval_on_selector_all(
        "input", "els => els.map(e=>e.value).filter(Boolean)"
    )
    inputs = [v for v in inputs if isinstance(v,str) and v.startswith("https://n9.cl/")]
    # 3) data-clipboard-text
    data_clip = await page.eval_on_selector_all(
        "[data-clipboard-text]", "els => els.map(e=>e.getAttribute('data-clipboard-text')).filter(Boolean)"
    )
    data_clip = [v for v in data_clip if v.startswith("https://n9.cl/")]
    # 4) кнопки/элементы с onclick='copy("https://n9.cl/...")'
    onclicks = await page.eval_on_selector_all(
        "[onclick]", "els => els.map(e=>e.getAttribute('onclick')).filter(Boolean)"
    )
    for s in onclicks:
        for m in re.findall(r'https://n9\\.cl/[A-Za-z0-9_-]{4,}', s):
            data_clip.append(m)
    # Совокупность
    return links + inputs + data_clip

async def do_shorten(page, url, alias, password):
    print("[STEP] goto")
    await page.goto("https://n9.cl/en", wait_until="domcontentloaded", timeout=25000)

    print("[STEP] fill")
    url_input = page.locator('input[type="url"]')
    if await url_input.count() == 0:
        url_input = page.get_by_placeholder(re.compile("url", re.I))
    if await url_input.count() == 0:
        url_input = page.locator("input").first
    await url_input.fill(str(url))

    if alias or password:
        adv = page.get_by_text(re.compile("Advanced Options", re.I))
        if await adv.count():
            await adv.first.click()

    if alias:
        alias_input = page.get_by_placeholder(re.compile("alias", re.I))
        if await alias_input.count() == 0:
            alias_input = page.locator('input[name="alias"]')
        if await alias_input.count():
            await alias_input.fill(alias)

    if password:
        pwd_input = page.locator('input[type="password"]')
        if await pwd_input.count() == 0:
            pwd_input = page.get_by_placeholder(re.compile("password", re.I))
        if await pwd_input.count():
            await pwd_input.fill(password)

    print("[STEP] click")
    btn = page.get_by_role("button", name=re.compile("^shorten$", re.I))
    if await btn.count() == 0:
        btn = page.get_by_text(re.compile("^shorten$", re.I)).first
    await btn.click()

    # подождём сетевую тишину и появление кандидатов
    print("[STEP] wait")
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    try:
        await page.wait_for_selector('a[href^="https://n9.cl/"], input[value^="https://n9.cl/"], [data-clipboard-text^="https://n9.cl/"]', timeout=8000)
    except Exception:
        pass

    # Попытка 1: кандидаты на странице
    c1 = await harvest_candidates(page)
    print(f"[STEP] candidates1: {c1}")
    short = pick_short(c1)
    if short:
        return short

    # Попытка 2: буфер обмена (некоторые сайты копируют туда)
    print("[STEP] clipboard")
    try:
        clip = await page.evaluate("navigator.clipboard.readText()")
        if isinstance(clip, str) and clip.startswith("https://n9.cl/"):
            short = pick_short([clip])
            if short:
                return short
    except Exception as e:
        print(f"[STEP] clipboard error: {e}")

    # Попытка 3: JSON-ответы
    print("[STEP] wait json")
    try:
        resp = await page.wait_for_response(
            lambda r: r.url.startswith("https://n9.cl/") and "json" in (r.headers or {}).get("content-type","").lower(),
            timeout=8000
        )
        try:
            data = await resp.json()
            # ищем ключ со ссылкой
            for k in ("short","short_url","permalink","url","result","link"):
                v = data.get(k) if isinstance(data, dict) else None
                if isinstance(v, str) and v.startswith("https://n9.cl/"):
                    short = pick_short([v])
                    if short:
                        return short
        except Exception:
            pass
    except Exception:
        pass

    # Попытка 4: парсинг HTML на всякий
    html = await page.content()
    all_hits = re.findall(r'https://n9\\.cl/([A-Za-z0-9_-]{4,})\\b', html)
    if all_hits:
        return "https://n9.cl/" + all_hits[-1]

    return None

@app.post("/shorten")
async def shorten(p: Payload):
    async with _sem:
        page = await _ctx.new_page()
        try:
            if stealth_async is not None:
                await stealth_async(page)
            for attempt in (1, 2):
                print(f"[TRY] {attempt} url={p.url}")
                short = await do_shorten(page, p.url, p.alias, p.password)
                if short:
                    print(f"[OK] {short}")
                    return {"status": "OK", "short": short}
                print("[WARN] empty result, retrying...")
                await dump_debug(page, f"attempt{attempt}")
            await dump_debug(page, "final-fail")
            raise HTTPException(status_code=502, detail="Не удалось получить короткую ссылку (возможна защита/капча).")
        finally:
            await page.close()
