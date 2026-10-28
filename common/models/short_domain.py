from sqlmodel import SQLModel, Field
from sqlalchemy import UniqueConstraint


class ShortDomain(SQLModel, table=True):
    """Личный short-домен: свой редирект-сервис за Cloudflare на ноде-сокращалке.

    Жизненный цикл (`status`):
      new         — только создан в БД, ещё ничего не сделано
      zone        — зона в CF создана, получены NS
      pending_ns  — ждём, пока регистратор прокинет NS (зона CF != active)
      active      — зона CF active, SSL=strict, A-запись (проксированная) создана
      provisioning— идёт SSH-настройка origin-ноды
      ready       — мини-сервис редиректа поднят и проверен, домен можно выбирать в SHORT
      error       — что-то упало (см. last_error), шаг можно перезапустить

    Slug-ссылки — stateless: целевой URL + HMAC-подпись (`secret_enc`) зашиты в сам
    slug, origin-нода расшифровывает и делает 301. Синхронизации БД с нодой нет.
    """
    __tablename__ = "short_domain"
    __table_args__ = (UniqueConstraint("domain", name="uq_short_domain_domain"),)

    id: int | None = Field(default=None, primary_key=True)

    domain: str = Field(index=True)        # хост в ссылках (go.example.com или example.com)
    zone_name: str = ""                     # apex-домен зоны CF (example.com)

    cf_token_enc: str = ""                  # CF API token (scoped), зашифрован
    registrar: str | None = None            # 'namesilo' | None (ручная смена NS)
    registrar_token_enc: str | None = None  # токен регистратора для авто-NS, зашифрован
    cf_account_email: str | None = None     # e-mail аккаунта CF / для записей
    ssl_email: str | None = None            # e-mail для origin-cert (на будущее/логи)
    cf_zone_id: str | None = None
    cf_cert_id: str | None = None           # id origin-сертификата CF
    cf_record_id: str | None = None         # id проксированной A-записи
    ns1: str | None = None                  # выданные CF неймсерверы
    ns2: str | None = None

    node_id: int | None = Field(default=None, index=True)  # origin-нода (роль 'short')
    origin_ip: str | None = None            # IP origin на момент создания A-записи
    service_port: int = 443                 # порт мини-сервиса (TLS с origin-cert)

    secret_enc: str = ""                    # HMAC-секрет для подписи slug (base64), зашифрован

    status: str = "new"
    last_error: str | None = None
    is_active: bool = True                  # можно ли выбирать как сокращатель

    created_at: str | None = None           # ISO
    activated_at: str | None = None         # когда зона стала active
    ready_at: str | None = None             # когда мини-сервис проверен
