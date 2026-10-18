# Build the small pinned Tryx binding patch inside the image build, not at runtime.
FROM rust:1.96-slim AS tryx-rust
FROM python:3.11-slim AS tryx-builder
COPY --from=tryx-rust /usr/local/cargo /usr/local/cargo
COPY --from=tryx-rust /usr/local/rustup /usr/local/rustup
ENV PATH="/usr/local/cargo/bin:${PATH}" CARGO_HOME=/usr/local/cargo RUSTUP_HOME=/usr/local/rustup
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential pkg-config libssl-dev patchelf \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir maturin==1.12.0
COPY scripts/build_tryx_history.py /build/scripts/build_tryx_history.py
COPY vendor/tryx /build/vendor/tryx
RUN python /build/scripts/build_tryx_history.py --source-dir /src/tryx --wheel-dir /wheels
RUN pip install --no-cache-dir --find-links=/wheels 'tryx==1.5.0+digitalbrain.1' \
    && python -c "from tryx.client import AdvancedClient; assert hasattr(AdvancedClient, 'fetch_message_history')"

FROM python:3.11-slim

ENV PIP_NO_CACHE_DIR=1     PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client-16 openssl gzip \
    build-essential gcc libpq-dev git \
    wget jq \
    libglib2.0-0 \
    ocrmypdf tesseract-ocr tesseract-ocr-eng ghostscript \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
COPY --from=tryx-builder /wheels /wheels
RUN pip install --find-links=/wheels -r /app/requirements.txt

COPY . /app

EXPOSE 8000
CMD ["uvicorn", "app:api", "--host", "0.0.0.0", "--port", "8000"]
