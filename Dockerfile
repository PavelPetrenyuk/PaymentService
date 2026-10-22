FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /service

RUN groupadd --system payments && useradd --system --gid payments payments

COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

RUN python -m pip install --upgrade pip && python -m pip install .

USER payments

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

