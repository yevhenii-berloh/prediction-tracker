FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Спершу метадані+код пакета, потім install — код запікається (не editable)
COPY pyproject.toml ./
COPY src ./src
RUN pip install .

# Alembic-файли потрібні сервісу migrate (у пакет не входять — лежать у корені репо)
COPY alembic.ini ./
COPY alembic ./alembic

RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
CMD ["python", "-m", "prophet_checker"]
