# Ranti runtime image. Lean by design: no compilers, no test tooling.
# The app is served from /app/src, so the uvicorn target is main:app with
# --app-dir src (PYTHONPATH is set as well for anything importing modules
# directly, such as the scripts in scripts/).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src \
    RANTI_DATABASE_PATH=/app/artifacts/ranti.db

WORKDIR /app

# pyproject.toml declares the runtime dependencies but no build backend, so
# pip cannot build the project itself. Read [project].dependencies out of
# pyproject.toml and install exactly those instead of duplicating the list.
COPY pyproject.toml ./
RUN python -c "import pathlib, tomllib; data = tomllib.loads(pathlib.Path('pyproject.toml').read_text('utf-8')); print(chr(10).join(data['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

# Non-root runtime user. UID 10001 stays clear of the base image users.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin ranti

COPY --chown=ranti:ranti src ./src
# SQLite needs a writable directory for the local index. On an ephemeral host
# this file is disposable: it can be rebuilt from Walrus Memory.
RUN mkdir -p /app/artifacts && chown -R ranti:ranti /app/artifacts

USER ranti

# 7860 is the Hugging Face Docker Space app port; 8000 is the local default.
EXPOSE 7860 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860", "--app-dir", "src"]
