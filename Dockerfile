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

# Install the project itself so the dependency list has exactly one home,
# pyproject.toml. Only src/ is needed at build time for the package metadata.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# Non-root runtime user. UID 10001 stays clear of the base image users.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin ranti

# SQLite needs a writable directory for the local index, and the runtime user
# must be able to read the application source. Chowning /app covers both; a
# build-context umask that produces 0600 files otherwise makes the container
# start and immediately die with PermissionError on src/main.py.
RUN mkdir -p /app/artifacts && chown -R ranti:ranti /app

USER ranti

# 7860 is the Hugging Face Docker Space app port; 8000 is the local default.
# Render and most other hosts inject PORT instead, so honour it when present.
EXPOSE 7860 8000

CMD uvicorn main:app --host 0.0.0.0 --port "${PORT:-7860}" --app-dir src
