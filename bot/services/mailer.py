import asyncio
import socket
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional, Literal, Tuple, List

import aiosmtplib

from common.config import settings
from common.security import decrypt_password


Mode = Literal["ssl", "starttls", "plain"]


@dataclass
class ProbeResult:
    ok: bool
    mode: Optional[Mode]
    error: Optional[str]
    smtp_response: Optional[str] = None


def _build_test_message(sender_email: str, recipient_email: Optional[str] = None) -> EmailMessage:
    """Compose a tiny plaintext test email.

    По умолчанию отправляем проверочное письмо на сам аккаунт (self-test),
    а не на сторонний адрес.
    """
    to = recipient_email or getattr(settings, "SMTP_TEST_RECIPIENT", None) or sender_email
    msg = EmailMessage()
    msg["From"] = sender_email
    msg["To"] = to
    msg["Subject"] = "SMTP Test"
    msg.set_content("OK")
    return msg


async def _try_send(
    host: str,
    port: int,
    login: str,
    password: str,
    sender_email: str,
    mode: Mode,
    timeout: Optional[float] = None,
    recipient_override: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """Try a single connection mode and send a minimal test email.

    Returns (ok, response_or_error).
    """
    timeout = (
        timeout
        if timeout is not None
        else float(getattr(settings, "SMTP_TEST_TIMEOUT", 12))
    )

    msg = _build_test_message(sender_email, recipient_override)

    # TLS contexts
    ssl_ctx = ssl.create_default_context()
    client = None
    client = aiosmtplib.SMTP(hostname=host, port=port, timeout=timeout)

    try:
        if mode == "ssl":
            # direct SSL
            client = aiosmtplib.SMTP(hostname=host, port=port, use_tls=True, timeout=timeout, tls_context=ssl_ctx)
            await client.connect()
        else:
            # plain connect first
            await client.connect()
            if mode == "starttls":
                await client.starttls(tls_context=ssl_ctx)

        await client.login(login, password)
        resp = await client.send_message(msg)

        def _parse_code_from_obj(o) -> Optional[int]:
            # direct int
            if isinstance(o, int):
                return o
            # SMTPResponse-like object
            c = getattr(o, "code", None)
            if c is not None:
                try:
                    return int(c)
                except Exception:
                    pass
            # strings like "250 Ok" or "2.0.0 Ok"
            if isinstance(o, str):
                s = o.strip()
                if s[:1] in ("2", "3"):
                    # try first 3 digits, else normalize to 250/350
                    import re as _re2
                    m = _re2.match(r"^(\d{3})", s)
                    if m:
                        return int(m.group(1))
                    return 250 if s[0] == "2" else 350
            # dict like {recipient: (code, message)}
            if isinstance(o, dict) and o:
                v = next(iter(o.values()))
                if isinstance(v, tuple) and len(v) >= 1:
                    return _parse_code_from_obj(v[0])
            # tuples (code, msg) or mixed like ({}, '2.0.0 Ok')
            if isinstance(o, tuple) and len(o) >= 1:
                for item in o:
                    c = _parse_code_from_obj(item)
                    if c is not None:
                        return c
            return None

        code: Optional[int] = _parse_code_from_obj(resp)
        # Build info string for logs
        if code is None:
            info = repr(resp)
        else:
            msg_str = getattr(resp, "message", None)
            if isinstance(resp, tuple) and len(resp) >= 2 and isinstance(resp[1], str):
                msg_str = resp[1]
            elif isinstance(resp, dict) and resp:
                v = next(iter(resp.values()))
                if isinstance(v, tuple) and len(v) >= 2:
                    msg_str = v[1]
            info = f"{code} {repr(msg_str) if msg_str is not None else ''}".strip()

        try:
            await client.quit()
        finally:
            pass

        ok = code is not None and 200 <= code < 400
        return ok, info

    except (aiosmtplib.SMTPException, asyncio.TimeoutError, socket.gaierror, ConnectionError) as e:
        try:
            if client is not None and getattr(client, "is_connected", False):
                await client.quit()
        except Exception:
            pass
        # Вынесем тип ошибки и порт/режим в строку, чтобы отчёт был полезнее
        return False, f"{type(e).__name__} on {host}:{port} [{mode}] — {e}"


async def _send_once(
    host: str,
    port: int,
    login: str,
    password: str,
    msg: EmailMessage,
    mode: Mode,
    timeout: float,
) -> Tuple[bool, Optional[str]]:
    """Отправить готовое письмо одним режимом подключения. (ok, info|error)."""
    ssl_ctx = ssl.create_default_context()
    if mode == "ssl":
        client = aiosmtplib.SMTP(hostname=host, port=port, use_tls=True, timeout=timeout, tls_context=ssl_ctx)
    else:
        client = aiosmtplib.SMTP(hostname=host, port=port, timeout=timeout)
    try:
        await client.connect()
        if mode == "starttls":
            await client.starttls(tls_context=ssl_ctx)
        await client.login(login, password)
        resp = await client.send_message(msg)
        try:
            await client.quit()
        except Exception:
            pass
        return True, repr(resp)
    except (aiosmtplib.SMTPException, asyncio.TimeoutError, socket.gaierror, ConnectionError, OSError) as e:
        try:
            if getattr(client, "is_connected", False):
                await client.quit()
        except Exception:
            pass
        return False, f"{type(e).__name__} on {host}:{port} [{mode}] — {e}"


async def send_via_account(
    smtp,
    to: str,
    subject: str,
    html: str,
    *,
    sender_name: Optional[str] = None,
    timeout: Optional[float] = None,
    text_fallback: Optional[str] = None,
    image_b64: Optional[str] = None,
    image_ct: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """Отправить HTML-письмо через переданный SMTP-аккаунт (модель SmtpAccount).

    sender_name — отображаемое имя отправителя (From: "Имя <email>").
    image_b64/image_ct (п.128): inline-картинка в HTML (Content-ID cid_src1), иначе — без неё.
    Возвращает (ok, info|error). Перебирает режимы (SSL/STARTTLS/PLAIN) по порту.
    """
    from email.utils import formataddr
    import base64 as _b64

    timeout = timeout if timeout is not None else float(getattr(settings, "SMTP_SEND_TIMEOUT", 20))
    password = decrypt_password(getattr(smtp, "password_enc", "") or "")

    msg = EmailMessage()
    msg["From"] = formataddr((sender_name, smtp.email)) if sender_name else smtp.email
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text_fallback or "Включите HTML-режим, чтобы увидеть содержимое письма.")
    if html:
        msg.add_alternative(html, subtype="html")
        if image_b64:
            ct = (image_ct or "image/png").split("/")
            mt, st = (ct[0], ct[1]) if len(ct) == 2 else ("image", "png")
            msg.get_payload()[-1].add_related(_b64.b64decode(image_b64), mt, st, cid="<cid_src1>")

    last_err: Optional[str] = None
    for mode in _modes_to_try(smtp.port):
        ok, info = await _send_once(smtp.host, smtp.port, smtp.login, password, msg, mode, timeout)
        if ok:
            return True, info
        last_err = info
    return False, last_err


def _modes_to_try(port: int) -> Tuple[Mode, ...]:
    # Heuristics by port, then fallback
    if port == 465:
        return ("ssl", "starttls", "plain")
    if port == 587:
        return ("starttls", "ssl", "plain")
    return ("starttls", "ssl", "plain")


async def probe_smtp(
    host: str,
    port: int,
    login: str,
    sender_email: str,
    password: str,
    *,
    timeout: Optional[float] = None,
    recipient_override: Optional[str] = None,
) -> ProbeResult:
    """Probe SMTP by attempting to send a test email.

    Tries SSL / STARTTLS / PLAIN in a safe order inferred from port.
    Returns ProbeResult with mode and response or error.
    """
    # Try modes
    for mode in _modes_to_try(port):
        ok, info = await _try_send(
            host=host,
            port=port,
            login=login,
            password=password,
            sender_email=sender_email,
            mode=mode,
            timeout=timeout,
            recipient_override=recipient_override,
        )
        if ok:
            return ProbeResult(ok=True, mode=mode, error=None, smtp_response=info)
    # Smart fallback for Mailgun: пробуем альтернативный порт и режимы,
    # если основная серия попыток не дала успеха.
    host_l = (host or "").lower()
    if "mailgun.org" in host_l:
        # если был 465 — пробуем 587 со STARTTLS первым, затем SSL/PLAIN
        # если был 587 — пробуем 465 со SSL первым
        alt_ports: Tuple[int, ...] = (587, 465) if port == 465 else (465, 587)
        for alt_port in alt_ports:
            pref_modes: Tuple[Mode, ...] = ("starttls", "ssl", "plain") if alt_port == 587 else ("ssl", "starttls", "plain")
            for mode in pref_modes:
                ok2, info2 = await _try_send(
                    host=host,
                    port=alt_port,
                    login=login,
                    password=password,
                    sender_email=sender_email,
                    mode=mode,
                    timeout=timeout,
                    recipient_override=recipient_override,
                )
                if ok2:
                    return ProbeResult(ok=True, mode=mode, error=None, smtp_response=f"{alt_port} {info2}")
    return ProbeResult(ok=False, mode=None, error=info, smtp_response=None)
