"""Отправка писем через ноду-сервер по SSH.

Бот коннектится к ноде (login/ip/pass, порт по умолчанию 22) и выполняет на ней
самодостаточный Python-скрипт: данные задания зашиты как base64-литерал, сам
скрипт подаётся на вход `python3 -` через stdin. На ноде нужен только python3
(stdlib smtplib) — внешних зависимостей и временных файлов нет.
TCP-соединение к SMTP-серверу идёт с IP ноды.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Optional, Tuple

import asyncssh

from common.security import decrypt_password

log = logging.getLogger("bot")

DEFAULT_SSH_PORT = 22
SSH_CONNECT_TIMEOUT = 15

# Префикс ошибки «нода не отвечает» (п.59): SSH не подключился или на ноде нет python.
# Письмо в этом случае ТОЧНО не ушло → безопасно переключиться на резервную ноду.
# Таймаут уже ПОСЛЕ подключения сюда не относится — письмо могло уйти, повтор = дубль.
NODE_DOWN = "Нода недоступна: "


class NodeUnreachable(Exception):
    """Не удалось подключиться к ноде по SSH (таймаут/отказ/неверный пароль)."""


def is_node_down(info: str | None) -> bool:
    return bool(info) and info.startswith(NODE_DOWN)

# Тело скрипта, выполняемого на ноде. PAYLOAD_B64 добавляется первой строкой.
_NODE_SCRIPT = r'''
import base64, json, sys, ssl, smtplib
from email.message import EmailMessage
from email.utils import formataddr

def _run():
    data = json.loads(base64.b64decode(PAYLOAD_B64).decode("utf-8"))
    s = data["smtp"]
    msg = EmailMessage()
    name = data.get("sender_name")
    msg["From"] = formataddr((name, s["email"])) if name else s["email"]
    msg["To"] = data["to"]
    msg["Subject"] = data.get("subject") or ""
    msg.set_content(data.get("text") or "Enable HTML to view this message.")
    if data.get("html"):
        msg.add_alternative(data["html"], subtype="html")
        img = data.get("image_b64")
        if img:
            raw = base64.b64decode(img)
            ct = (data.get("image_ct") or "image/png").split("/")
            mt, st = (ct[0], ct[1]) if len(ct) == 2 else ("image", "png")
            # inline-картинка, на которую в HTML ссылается src="cid:cid_src1" (п.128)
            msg.get_payload()[-1].add_related(raw, mt, st, cid="<cid_src1>")

    host = s["host"]; port = int(s["port"]); timeout = int(data.get("timeout", 30))
    ctx = ssl.create_default_context()
    modes = ("ssl", "starttls", "plain") if port == 465 else ("starttls", "ssl", "plain")
    last = None
    for mode in modes:
        srv = None
        try:
            if mode == "ssl":
                srv = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx)
            else:
                srv = smtplib.SMTP(host, port, timeout=timeout)
                srv.ehlo()
                if mode == "starttls":
                    srv.starttls(context=ctx); srv.ehlo()
            srv.login(s["login"], s["password"])
            srv.send_message(msg)
            try: srv.quit()
            except Exception: pass
            print("OK " + mode)
            return 0
        except Exception as e:
            last = "%s:%s [%s] %s" % (host, port, mode, e)
            try:
                if srv is not None: srv.close()
            except Exception:
                pass
    print("ERR " + (last or "send failed"))
    return 1

sys.exit(_run())
'''


async def _run_on_node(node, command: str, *, input: Optional[str] = None, timeout: int = 30):
    """Открыть SSH-сессию к ноде и выполнить команду. Возвращает SSHCompletedProcess."""
    password = decrypt_password(getattr(node, "password_enc", "") or "")
    port = getattr(node, "port", None) or DEFAULT_SSH_PORT
    try:
        conn = await asyncio.wait_for(
            asyncssh.connect(
                node.ip,
                port=port,
                username=node.login,
                password=password,
                known_hosts=None,  # не требуем known_hosts для произвольных нод
            ),
            timeout=SSH_CONNECT_TIMEOUT,
        )
    except Exception as e:
        raise NodeUnreachable(f"{type(e).__name__}: {e}") from e
    try:
        return await asyncio.wait_for(conn.run(command, input=input, check=False), timeout=timeout)
    finally:
        conn.close()
        try:
            await conn.wait_closed()
        except Exception:
            pass


async def check_node(node) -> Tuple[bool, str]:
    """Проверить доступность ноды по SSH и наличие python3."""
    try:
        r = await _run_on_node(
            node,
            "python3 -c 'import sys;print(\"PY\",sys.version.split()[0])' || python --version",
            timeout=20,
        )
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if "PY" in out:
        return True, out
    if r.exit_status == 0 and out:
        return True, out  # python2 fallback отработал
    return False, (err or out or f"exit {r.exit_status}")


def ses_sigv4(region, access, secret, body, ctype, amzdate, datestamp):
    """SigV4-подпись запроса к SES (service=ses). stdlib-only.

    Эта же функция встраивается в скрипт, выполняемый на ноде (через inspect),
    поэтому тестируется ровно то, что исполняется.
    """
    import hashlib
    import hmac
    service = "ses"
    host = "email.%s.amazonaws.com" % region
    payload_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    canonical_headers = "content-type:%s\nhost:%s\nx-amz-date:%s\n" % (ctype, host, amzdate)
    signed_headers = "content-type;host;x-amz-date"
    canonical_request = "POST\n/\n\n%s\n%s\n%s" % (canonical_headers, signed_headers, payload_hash)
    scope = "%s/%s/%s/aws4_request" % (datestamp, region, service)
    sts = "AWS4-HMAC-SHA256\n%s\n%s\n%s" % (
        amzdate, scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    )

    def _s(k, m):
        return hmac.new(k, m.encode("utf-8"), hashlib.sha256).digest()

    k = _s(("AWS4" + secret).encode("utf-8"), datestamp)
    k = _s(k, region)
    k = _s(k, service)
    k = _s(k, "aws4_request")
    sig = hmac.new(k, sts.encode("utf-8"), hashlib.sha256).hexdigest()
    auth = "AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s" % (
        access, scope, signed_headers, sig,
    )
    return auth, payload_hash


_SES_MAIN = r'''
import base64, json, sys, datetime, urllib.request, urllib.parse

def _run():
    d = json.loads(base64.b64decode(PAYLOAD_B64).decode("utf-8"))
    region = d["region"]
    host = "email.%s.amazonaws.com" % region
    endpoint = "https://" + host + "/"
    if d.get("image_b64"):
        # С картинкой: SES SendEmail (query API) не умеет вложения → строим сырой MIME
        # (multipart/related, inline-картинка Content-ID cid_src1) и шлём SendRawEmail (п.128).
        from email.message import EmailMessage
        m = EmailMessage()
        m["From"] = d["source"]; m["To"] = d["to"]; m["Subject"] = d.get("subject", "")
        m.set_content("Enable HTML to view this message.")
        m.add_alternative(d.get("html", ""), subtype="html")
        raw_img = base64.b64decode(d["image_b64"])
        ct = (d.get("image_ct") or "image/png").split("/")
        mt, st = (ct[0], ct[1]) if len(ct) == 2 else ("image", "png")
        m.get_payload()[-1].add_related(raw_img, mt, st, cid="<cid_src1>")
        params = {
            "Action": "SendRawEmail",
            "RawMessage.Data": base64.b64encode(m.as_bytes()).decode("ascii"),
        }
    else:
        params = {
            "Action": "SendEmail",
            "Source": d["source"],
            "Destination.ToAddresses.member.1": d["to"],
            "Message.Subject.Data": d.get("subject", ""),
            "Message.Subject.Charset": "UTF-8",
            "Message.Body.Html.Data": d.get("html", ""),
            "Message.Body.Html.Charset": "UTF-8",
        }
    body = urllib.parse.urlencode(params)
    ctype = "application/x-www-form-urlencoded; charset=utf-8"
    now = d.get("_now")
    if now:
        amzdate = now; datestamp = now[:8]
    else:
        n = datetime.datetime.utcnow()
        amzdate = n.strftime("%Y%m%dT%H%M%SZ"); datestamp = n.strftime("%Y%m%d")
    auth, _ = ses_sigv4(region, d["access"], d["secret"], body, ctype, amzdate, datestamp)
    if d.get("_dryrun"):
        print("AUTH " + auth); return 0
    req = urllib.request.Request(endpoint, data=body.encode("utf-8"), method="POST")
    req.add_header("Content-Type", ctype)
    req.add_header("X-Amz-Date", amzdate)
    req.add_header("Authorization", auth)
    try:
        resp = urllib.request.urlopen(req, timeout=int(d.get("timeout", 25)))
        print("OK " + resp.read().decode("utf-8", "ignore")[:200]); return 0
    except Exception as e:
        err = ""
        try: err = e.read().decode("utf-8", "ignore")[:300]
        except Exception: pass
        print("ERR %s %s" % (e, err)); return 1

sys.exit(_run())
'''


_SES_QUOTA_MAIN = r'''
import base64, json, sys, datetime, re, urllib.request, urllib.parse

def _run():
    d = json.loads(base64.b64decode(PAYLOAD_B64).decode("utf-8"))
    region = d["region"]
    host = "email.%s.amazonaws.com" % region
    endpoint = "https://" + host + "/"
    body = urllib.parse.urlencode({"Action": "GetSendQuota"})
    ctype = "application/x-www-form-urlencoded; charset=utf-8"
    n = datetime.datetime.utcnow()
    amzdate = n.strftime("%Y%m%dT%H%M%SZ"); datestamp = n.strftime("%Y%m%d")
    auth, _ = ses_sigv4(region, d["access"], d["secret"], body, ctype, amzdate, datestamp)
    req = urllib.request.Request(endpoint, data=body.encode("utf-8"), method="POST")
    req.add_header("Content-Type", ctype)
    req.add_header("X-Amz-Date", amzdate)
    req.add_header("Authorization", auth)
    try:
        resp = urllib.request.urlopen(req, timeout=int(d.get("timeout", 25)))
        xml = resp.read().decode("utf-8", "ignore")
        def g(tag):
            m = re.search("<%s>([^<]*)</%s>" % (tag, tag), xml)
            return (m.group(1) if m else "?").split(".")[0]
        print("OK 24h: %s/%s, rate %s/s" % (g("SentLast24Hours"), g("Max24HourSend"), g("MaxSendRate")))
        return 0
    except Exception as e:
        err = ""
        try: err = e.read().decode("utf-8", "ignore")[:300]
        except Exception: pass
        print("ERR %s %s" % (e, err)); return 1

sys.exit(_run())
'''


def _build_ses_node_source(payload_b64: str) -> str:
    import inspect
    return 'PAYLOAD_B64 = "%s"\n%s\n%s' % (payload_b64, inspect.getsource(ses_sigv4), _SES_MAIN)


def _build_ses_quota_source(payload_b64: str) -> str:
    import inspect
    return 'PAYLOAD_B64 = "%s"\n%s\n%s' % (payload_b64, inspect.getsource(ses_sigv4), _SES_QUOTA_MAIN)


async def check_ses_via_node(node, aws, *, timeout: int = 25) -> Tuple[bool, str]:
    """Проверить доступ к SES (GetSendQuota) НА НОДЕ её региона по SSH, stdlib-SigV4.

    Запрос к api AWS идёт с IP ноды региона — никогда с IP бота (как и отправка).
    """
    secret = decrypt_password(getattr(aws, "secret_key_enc", "") or "")
    payload = {"region": aws.region, "access": aws.access_key, "secret": secret, "timeout": timeout}
    b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    source_code = _build_ses_quota_source(b64)
    for interp in ("python3 -", "python -"):
        try:
            r = await _run_on_node(node, interp, input=source_code, timeout=timeout + 20)
        except NodeUnreachable as e:
            return False, f"{NODE_DOWN}{e}"
        except Exception as e:
            return False, f"SSH: {type(e).__name__}: {e}"
        if r.exit_status == 127:
            continue
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if r.exit_status == 0 and out.startswith("OK"):
            return True, out[3:].strip()
        return False, (out or err or f"exit {r.exit_status}")
    return False, f"{NODE_DOWN}python3/python не найден"


async def send_ses_via_node(
    node,
    aws,
    to: str,
    subject: str,
    html: str,
    *,
    sender_name: Optional[str] = None,
    timeout: int = 30,
    image_b64: Optional[str] = None,
    image_ct: Optional[str] = None,
) -> Tuple[bool, str]:
    """Выполнить SES-отправку НА НОДЕ (её IP) по SSH, stdlib-SigV4 на ноде.

    image_b64/image_ct (п.128): если заданы — на ноде строится сырой MIME с inline-картинкой
    (Content-ID cid_src1) и шлётся через SES SendRawEmail; иначе — обычный SendEmail."""
    from email.utils import formataddr
    secret = decrypt_password(getattr(aws, "secret_key_enc", "") or "")
    source = formataddr((sender_name, aws.email_from)) if sender_name else aws.email_from
    payload = {
        "region": aws.region, "access": aws.access_key, "secret": secret,
        "source": source, "to": to, "subject": subject, "html": html, "timeout": timeout,
        "image_b64": image_b64, "image_ct": image_ct,
    }
    b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    source_code = _build_ses_node_source(b64)
    for interp in ("python3 -", "python -"):
        try:
            r = await _run_on_node(node, interp, input=source_code, timeout=timeout + 20)
        except NodeUnreachable as e:
            return False, f"{NODE_DOWN}{e}"
        except Exception as e:
            return False, f"SSH: {type(e).__name__}: {e}"
        if r.exit_status == 127:
            continue
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if r.exit_status == 0 and out.startswith("OK"):
            return True, out
        return False, (out or err or f"exit {r.exit_status}")
    return False, f"{NODE_DOWN}python3/python не найден"


async def send_via_node(
    node,
    smtp,
    to: str,
    subject: str,
    html: str,
    *,
    sender_name: Optional[str] = None,
    text_fallback: Optional[str] = None,
    timeout: int = 30,
    image_b64: Optional[str] = None,
    image_ct: Optional[str] = None,
) -> Tuple[bool, str]:
    """Отправить письмо через ноду (SSH + python3 на ноде). (ok, info|error).

    image_b64/image_ct (п.128): если заданы — в HTML-часть встраивается inline-картинка
    (multipart/related, Content-ID cid_src1), иначе письмо как обычный text/html."""
    payload = {
        "smtp": {
            "host": smtp.host,
            "port": smtp.port,
            "login": smtp.login,
            "email": smtp.email,
            "password": decrypt_password(getattr(smtp, "password_enc", "") or ""),
        },
        "to": to,
        "subject": subject,
        "html": html,
        "text": text_fallback,
        "sender_name": sender_name,
        "timeout": timeout,
        "image_b64": image_b64,
        "image_ct": image_ct,
    }
    b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    source = 'PAYLOAD_B64 = "%s"\n%s' % (b64, _NODE_SCRIPT)

    # python3, с откатом на python при отсутствии python3 (exit 127)
    for interp in ("python3 -", "python -"):
        try:
            r = await _run_on_node(node, interp, input=source, timeout=timeout + 20)
        except NodeUnreachable as e:
            return False, f"{NODE_DOWN}{e}"
        except Exception as e:
            return False, f"SSH: {type(e).__name__}: {e}"
        if r.exit_status == 127:  # интерпретатор не найден — пробуем следующий
            continue
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if r.exit_status == 0 and out.startswith("OK"):
            return True, out
        return False, (out or err or f"exit {r.exit_status}")
    return False, f"{NODE_DOWN}python3/python не найден"
