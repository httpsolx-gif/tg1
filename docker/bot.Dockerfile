FROM python:3.11-slim
WORKDIR /app
# код копируем ДО установки: editable-install пустой папки не даёт пакетов
COPY . /app
RUN pip install -U pip && pip install -e .
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "bot.main"]
