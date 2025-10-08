FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml /app/
RUN pip install -U pip && pip install -e .
COPY . /app
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
