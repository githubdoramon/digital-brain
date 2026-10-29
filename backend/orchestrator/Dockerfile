# Build the small pinned Tryx binding patch inside the image build, not at runtime.
FROM rust:1.98-slim AS tryx-rust
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
# Cache public tokenizer data at build time; requests must not download vocabularies.
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

# Restore the original CPU Kokoro assets. No model download on a voice request.
RUN mkdir -p /app/models/kokoro \
    && curl -fL --retry 3 -o /app/models/kokoro/model.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx \
    && echo "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb  /app/models/kokoro/model.onnx" | sha256sum -c - \
    && curl -fL --retry 3 -o /app/models/kokoro/voices.bin https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin \
    && echo "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d  /app/models/kokoro/voices.bin" | sha256sum -c -

COPY . /app

EXPOSE 8000
CMD ["uvicorn", "app:api", "--host", "0.0.0.0", "--port", "8000"]
