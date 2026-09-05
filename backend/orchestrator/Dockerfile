FROM python:3.11-slim

ENV PIP_NO_CACHE_DIR=1     PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1

ARG KOKORO_MODEL_URL=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx
ARG KOKORO_MODEL_SHA256=6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb
ARG KOKORO_VOICES_URL=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
ARG KOKORO_VOICES_SHA256=bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc libpq-dev git \
    curl wget jq \
    libglib2.0-0 \
    ocrmypdf tesseract-ocr tesseract-ocr-eng ghostscript \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# Keep the GPU model server isolated from speech output: the quantized Kokoro
# graph is verified and bundled for CPU-only inference in the final image.
RUN mkdir -p /app/models/kokoro \
    && curl -fL --retry 3 --retry-delay 2 \
        -o /app/models/kokoro/model.onnx "${KOKORO_MODEL_URL}" \
    && echo "${KOKORO_MODEL_SHA256}  /app/models/kokoro/model.onnx" | sha256sum -c - \
    && curl -fL --retry 3 --retry-delay 2 \
        -o /app/models/kokoro/voices.bin "${KOKORO_VOICES_URL}" \
    && echo "${KOKORO_VOICES_SHA256}  /app/models/kokoro/voices.bin" | sha256sum -c -

ENV KOKORO_MODEL_PATH=/app/models/kokoro/model.onnx \
    KOKORO_VOICES_PATH=/app/models/kokoro/voices.bin \
    KOKORO_VOICE=af_heart \
    KOKORO_LANG_CODE=en-us \
    KOKORO_MAX_CONCURRENCY=1 \
    GLASSES_AUDIO_TTL_SECONDS=300

COPY . /app

EXPOSE 8000
CMD ["uvicorn", "app:api", "--host", "0.0.0.0", "--port", "8000"]
