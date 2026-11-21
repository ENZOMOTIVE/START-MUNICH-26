FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
# OCR for requests and documents that arrive as a scan (app/workflows/documents.py).
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1
# Truckers also send their offer as a voice message (live round 69): local speech-to-text, no
# model calls. Installed before the app is copied, so a code change does not download it again.
RUN uv venv /app/.venv \
    && uv pip install --python /app/.venv/bin/python faster-whisper==1.2.1 av==18.1.1 \
    && python -c "from faster_whisper import download_model; download_model('base.en', output_dir='/models/base.en')"
COPY pyproject.toml uv.lock ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev --inexact
EXPOSE 8000
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000
