# tg-mailer — full skeleton

Telegram mailer bot (aiogram v3) + FastAPI tracker & logs API + DB (SQLAlchemy/SQLModel) + channel logging (errors/sends).

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env
# Fill .env: BOT_TOKEN, ADMIN_IDS, ERR_CHANNEL_ID, SEND_CHANNEL_ID
python bot/main.py  # run bot
# In a separate shell (optional API):
uvicorn api.main:app --reload --port 8080
```
