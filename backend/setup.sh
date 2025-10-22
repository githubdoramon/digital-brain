#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${SCRIPT_DIR}"

cd "${ROOT_DIR}"

echo "🚀 Personal Memory Orchestrator Setup"
echo "===================================="
echo ""

ENV_FILE="${BACKEND_DIR}/.env"
ENV_TEMPLATE="${BACKEND_DIR}/env.template"

if [ ! -f "${ENV_FILE}" ]; then
    if [ ! -f "${ENV_TEMPLATE}" ]; then
        echo "✗ env.template not found at ${ENV_TEMPLATE}" >&2
        exit 1
    fi
    echo "📝 Creating backend .env file from template..."
    cp "${ENV_TEMPLATE}" "${ENV_FILE}"
    echo "✓ Created backend .env file"
    echo ""
    echo "⚠️  Please review and edit backend/.env if needed"
    echo ""
else
    echo "✓ backend/.env already exists"
fi

if grep -q "EMBEDDINGS=ollama" "${ENV_FILE}"; then
    echo ""
    echo "🤖 Checking Ollama on host machine..."
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo "✓ Ollama is running on localhost:11434"
        OLLAMA_MODEL=$(grep OLLAMA_EMBED_MODEL "${ENV_FILE}" | cut -d '=' -f2 | tr -d ' ')
        if ollama list | grep -q "${OLLAMA_MODEL:-nomic-embed-text}"; then
            echo "✓ Ollama model '${OLLAMA_MODEL:-nomic-embed-text}' is already available"
        else
            echo "📥 Pulling Ollama model '${OLLAMA_MODEL:-nomic-embed-text}'..."
            ollama pull "${OLLAMA_MODEL:-nomic-embed-text}"
            echo "✓ Ollama model ready"
        fi
    else
        echo "⚠️  Warning: Ollama not detected on localhost:11434"
        echo "   Make sure Ollama is running: 'ollama serve'"
        echo "   Continuing anyway..."
    fi
fi

echo ""
echo "🐳 Starting Docker services (backend only)..."
docker compose \
    -f "${BACKEND_DIR}/docker-compose.yml" \
    --env-file "${ENV_FILE}" \
    up -d

echo ""
echo "⏳ Waiting for services to be healthy..."
sleep 5

echo ""
echo "⏳ Waiting for orchestrator to be ready..."
for i in {1..30}; do
    if curl -s http://localhost:8000/docs > /dev/null 2>&1; then
        echo "✓ Orchestrator is ready!"
        break
    fi
    sleep 2
done

echo ""
echo "🌱 Seeding database with test data..."
python3 "${BACKEND_DIR}/seed_data.py"

echo ""
echo "✅ Setup complete!"
echo ""
echo "🎯 Services running:"
echo "  - API: http://localhost:8000/docs"
echo "  - Database: localhost:5432"
if grep -q "EMBEDDINGS=ollama" "${ENV_FILE}"; then
    echo "  - Ollama: http://localhost:11434 (host machine)"
fi
echo ""
echo "🧪 Try a test query:"
echo '  curl -X POST http://localhost:8000/search -H "Content-Type: application/json" -d '\''{"query":"meetings with Alice","limit":3}'\''
echo "" 