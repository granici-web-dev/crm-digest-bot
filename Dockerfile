FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY config ./config
COPY templates ./templates
COPY src ./src
COPY deploy/entrypoint.sh ./deploy/entrypoint.sh
# CONFIG_DIR и TEMPLATES_DIR считаются от __file__: проект ставится editable (по умолчанию uv),
# чтобы код остался в /app/src рядом с config/ и templates/.
RUN uv sync --frozen --no-dev
# tzdata в uv.lock только для win32: Europe/Bucharest держится на tzdata базового образа,
# смена базы без него не должна пройти молча.
RUN python -c "from zoneinfo import ZoneInfo; ZoneInfo('Europe/Bucharest')"

RUN useradd --system --uid 10001 --no-create-home app
USER app

ARG GIT_SHA=unknown
ENV APP_VERSION=$GIT_SHA

ENTRYPOINT ["sh", "/app/deploy/entrypoint.sh"]
