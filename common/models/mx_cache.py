"""Кэш провайдера по MX-записям домена (п.52).

Чтобы не дёргать DNS на каждую отправку: домен → провайдер (google/microsoft/…)
или "" (проверен, провайдер не распознан). Создаётся через create_all (импорт в
bot/main.py).
"""
from sqlmodel import SQLModel, Field


class MxCache(SQLModel, table=True):
    __tablename__ = "mx_cache"

    domain: str = Field(primary_key=True)   # нижний регистр
    provider: str = ""                      # google|microsoft|yahoo|…|"" (нераспознан)
    checked_at: str
