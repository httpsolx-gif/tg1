"""Публичная страница с инструкцией для клиента на корне домена (https://grzl.cc/).

Источник — api/CLIENT.md (рендер markdown → HTML один раз при первом запросе).
Страница без секретов: тот же текст, что выдаём клиенту файлом.
"""
from functools import lru_cache
from pathlib import Path

import markdown
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse

router = APIRouter()

_MD = Path(__file__).resolve().parent.parent / "CLIENT.md"

_PAGE = """<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Mailer API — инструкция</title>
<style>
:root {{ --bg:#ffffff; --fg:#1f2328; --muted:#59636e; --line:#d1d9e0; --code:#f6f8fa; --accent:#0969da; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1; --line:#30363d; --code:#161b22; --accent:#4493f8; }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg);
  font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
main {{ max-width:860px; margin:0 auto; padding:32px 16px 64px; }}
h1 {{ font-size:28px; margin:0 0 16px; }}
h2 {{ font-size:21px; margin:36px 0 12px; padding-bottom:6px; border-bottom:1px solid var(--line); }}
a {{ color:var(--accent); }}
hr {{ border:0; border-top:1px solid var(--line); margin:28px 0; }}
code {{ font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; background:var(--code);
  padding:1px 5px; border-radius:5px; }}
pre {{ background:var(--code); border:1px solid var(--line); border-radius:8px; padding:14px; overflow-x:auto; }}
pre code {{ background:none; padding:0; }}
table {{ border-collapse:collapse; width:100%; display:block; overflow-x:auto; margin:12px 0; }}
th, td {{ border:1px solid var(--line); padding:6px 10px; text-align:left; vertical-align:top; }}
th {{ background:var(--code); }}
blockquote {{ margin:12px 0; padding:4px 14px; border-left:3px solid var(--line); color:var(--muted); }}
.raw {{ float:right; font-size:14px; color:var(--muted); }}
</style>
</head><body><main>
<a class="raw" href="/client.md">CLIENT.md</a>
{body}
</main></body></html>
"""


@lru_cache(maxsize=1)
def _html() -> str:
    body = markdown.markdown(_MD.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"])
    return _PAGE.format(body=body)


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def client_docs():
    return HTMLResponse(_html())


@router.get("/client.md", response_class=PlainTextResponse, include_in_schema=False)
async def client_docs_raw():
    return PlainTextResponse(_MD.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")
