"""Amazon SES: парсинг строки аккаунта и проверка доступа.

Формат аккаунта: region:accesskey:secretkey:emailfrom.

ВАЖНО: ни бот, ни какой-либо другой сервер НЕ подключается к api AWS напрямую.
И отправка (`node_runner.send_ses_via_node`), и проверка доступа (`check_aws` ниже)
выполняются ТОЛЬКО на ноде, в которой указан регион этого аккаунта SES, — запрос
к AWS всегда идёт с IP такой ноды. Поэтому здесь нет boto3 и сетевых вызовов.
"""
from __future__ import annotations

import re
from typing import Tuple


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


async def check_aws(aws) -> Tuple[bool, str]:
    """Проверить доступ к SES (GetSendQuota) — ТОЛЬКО через ноду региона, не с IP бота."""
    from bot.services.node_pool import ses_nodes, send_with_failover
    from bot.services.node_runner import check_ses_via_node
    nodes = ses_nodes(aws)
    if not nodes:
        return False, (
            f"нет активной ноды отправки с регионом {aws.region} (NODES) — "
            f"проверка SES идёт только через неё"
        )
    ok, info, node, _all_down = await send_with_failover(nodes, lambda n: check_ses_via_node(n, aws))
    if node is None:
        return False, f"ноды региона {aws.region} недоступны"
    return ok, f"{info} · через {node.login}@{node.ip}"
