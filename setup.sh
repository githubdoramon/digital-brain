#!/bin/bash
set -e

echo "🚀 Personal Memory Orchestrator Setup"
echo "===================================="
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "📝 Creating .env file from template..."
    cp env.template .env
    echo "✓ Created .env file"
    echo ""
    echo "⚠️  Please review and edit .env if needed (especially if using OpenAI)"
    echo ""
else
    echo "✓ .env file already exists"
fi

# Check if Ollama is running on host
if grep -q "EMBEDDINGS=ollama" .env; then
    echo ""
    echo "🤖 Checking Ollama on host machine..."
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo "✓ Ollama is running on localhost:11434"
        
        # Check if model is available
        OLLAMA_MODEL=$(grep OLLAMA_EMBED_MODEL .env | cut -d '=' -f2 | tr -d ' ')
        if ollama list | grep -q "${OLLAMA_MODEL:-nomic-embed-text}"; then
            echo "✓ Ollama model '${OLLAMA_MODEL:-nomic-embed-text}' is already available"
        else
            echo "📥 Pulling Ollama model '${OLLAMA_MODEL:-nomic-embed-text}'..."
            ollama pull ${OLLAMA_MODEL:-nomic-embed-text}
            echo "✓ Ollama model ready"
        fi
    else
        echo "⚠️  Warning: Ollama not detected on localhost:11434"
        echo "   Make sure Ollama is running: 'ollama serve'"
        echo "   Continuing anyway..."
    fi
fi

# Start services
echo ""
echo "🐳 Starting Docker services..."
docker compose up -d

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
python3 seed_data.py

echo ""
echo "✅ Setup complete!"
echo ""
echo "🎯 Services running:"
echo "  - API: http://localhost:8000/docs"
echo "  - Database: localhost:5432"
if grep -q "EMBEDDINGS=ollama" .env; then
    echo "  - Ollama: http://localhost:11434 (host machine)"
fi
echo ""
echo "🧪 Try a test query:"
echo '  curl -X POST http://localhost:8000/search -H "Content-Type: application/json" -d '"'"'{"query":"meetings with Alice","limit":3}'"'"''
echo ""

