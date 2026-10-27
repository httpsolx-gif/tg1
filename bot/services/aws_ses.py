"""Отправка писем через Amazon SES (API, boto3).

Формат аккаунта: region:accesskey:secretkey:emailfrom.
Синхронный boto3 исполняется в отдельном потоке, чтобы не блокировать loop.
"""
from __future__ import annotations

import asyncio
import re
from email.utils import formataddr
from typing import Optional, Tuple

from common.security import decrypt_password

_CONNECT_TIMEOUT = 15
_READ_TIMEOUT = 25


def parse_aws_line(line: str) -> Tuple[str, str, str, str]:
    """`region:accesskey:secretkey:emailfrom` → (region, access, secret, email)."""
    parts = (line or "").strip().split(":")
    if len(parts) < 4:
        raise ValueError("нужно region:accesskey:secretkey:emailfrom")
    region, access, secret = parts[0].strip(), parts[1].strip(), parts[2].strip()
    email = ":".join(parts[3:]).strip()  # на случай двоеточий в хвосте (обычно их нет)
    if not region or not access or not secret:
        raise ValueError("пустой region/accesskey/secretkey")
    if "@" not in email:
        raise ValueError(f"emailfrom без @: {email}")
    if not re.match(r"^[a-z]{2}-[a-z]+-\d+$", region):
        # не жёстко: просто предупреждаем через исключение, если совсем не похоже
        if " " in region:
            raise ValueError(f"подозрительный region: {region}")
    return region, access, secret, email


def _client(region: str, access: str, secret: str):
    import boto3
    from botocore.config import Config
    return boto3.client(
        "ses",
        region_name=region,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        config=Config(
            connect_timeout=_CONNECT_TIMEOUT,
            read_timeout=_READ_TIMEOUT,
            retries={"max_attempts": 1},
        ),
    )


def _send_sync(region, access, secret, source, to, subject, html) -> str:
    client = _client(region, access, secret)
    resp = client.send_email(
        Source=source,
        Destination={"ToAddresses": [to]},
        Message={
            "Subject": {"Data": subject or "", "Charset": "UTF-8"},
            "Body": {"Html": {"Data": html or "", "Charset": "UTF-8"}},
        },
    )
    return resp.get("MessageId", "")


def _quota_sync(region, access, secret) -> dict:
    return _client(region, access, secret).get_send_quota()


async def send_via_ses(
    aws,
    to: str,
    subject: str,
    html: str,
    *,
    sender_name: Optional[str] = None,
) -> Tuple[bool, str]:
    """Отправить письмо через SES-аккаунт. (ok, info|error)."""
    secret = decrypt_password(getattr(aws, "secret_key_enc", "") or "")
    source = formataddr((sender_name, aws.email_from)) if sender_name else aws.email_from
    try:
        mid = await asyncio.to_thread(
            _send_sync, aws.region, aws.access_key, secret, source, to, subject, html
        )
        return True, f"SES ok, MessageId={mid}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def check_aws(aws) -> Tuple[bool, str]:
    """Проверить доступ SES (get_send_quota)."""
    secret = decrypt_password(getattr(aws, "secret_key_enc", "") or "")
    try:
        q = await asyncio.to_thread(_quota_sync, aws.region, aws.access_key, secret)
        return True, (
            f"24h: {int(q.get('SentLast24Hours', 0))}/{int(q.get('Max24HourSend', 0))}, "
            f"rate {q.get('MaxSendRate')}/s"
        )
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
