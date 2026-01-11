"""
Pytest configuration and shared fixtures for agent tests.
"""

import os
import sys
import pytest
from typing import Any, Dict, List
from unittest.mock import MagicMock, AsyncMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def sample_question():
    """Sample question for testing."""
    return "What meetings did I have last week?"


@pytest.fixture
def sample_conversation_history():
    """Sample conversation history."""
    return [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi! How can I help you today?"},
    ]


@pytest.fixture
def mock_llm_response():
    """Factory fixture for mock LLM responses."""
    def _create_response(
        content: str = "",
        tool_calls: List[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        response = {
            "message": {
                "content": content,
                "role": "assistant",
            }
        }
        if tool_calls:
            response["message"]["tool_calls"] = tool_calls
        return response
    return _create_response


@pytest.fixture
def mock_tool_call():
    """Factory fixture for mock tool calls."""
    def _create_tool_call(
        name: str,
        arguments: Dict[str, Any],
        call_id: str = "call_123",
    ) -> Dict[str, Any]:
        return {
            "id": call_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": arguments,
            }
        }
    return _create_tool_call


@pytest.fixture
def mock_search_result():
    """Sample search results."""
    return {
        "results": [
            {
                "id": "event:123",
                "title": "Team Meeting",
                "summary": "Weekly sync",
                "score": 0.85,
            },
            {
                "id": "event:456",
                "title": "Project Review",
                "summary": "Q4 planning",
                "score": 0.72,
            },
        ],
        "count": 2,
    }


@pytest.fixture
def mock_sql_result():
    """Sample SQL query results."""
    return {
        "rows": [
            {"id": 1, "name": "John Doe", "email": "john@example.com"},
            {"id": 2, "name": "Jane Smith", "email": "jane@example.com"},
        ],
        "columns": ["id", "name", "email"],
    }


@pytest.fixture
def agent_config():
    """Test configuration for agent."""
    from agent.limits import AgentConfig
    return AgentConfig(
        max_steps=5,
        max_tool_calls=10,
        max_repairs=2,
        enable_intent_routing=True,
        enable_validation=True,
    )


@pytest.fixture
def mock_tool_registry():
    """Mock tool registry for testing."""
    from tools.registry import ToolRegistry
    from tools.contracts import ToolContract, ToolParameter

    registry = ToolRegistry()

    # Add a simple test tool
    registry.register(
        ToolContract(
            name="test_tool",
            description="A test tool",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Test query",
                    required=True,
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Max results",
                    required=False,
                    default=5,
                    minimum=1,
                    maximum=100,
                ),
            ],
        ),
        groups=["test"],
    )

    return registry


@pytest.fixture
def agent_state():
    """Create a fresh agent state for testing."""
    from agent.state import AgentState
    return AgentState(goal="Test goal")


@pytest.fixture
def mock_requests(monkeypatch):
    """Mock requests library for LLM calls."""
    mock_post = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "Test response"}}]
    }
    mock_response.raise_for_status = MagicMock()
    mock_post.return_value = mock_response

    monkeypatch.setattr("requests.post", mock_post)
    return mock_post


# Environment setup for tests
@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    """Set up environment variables for testing."""
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_CHAT_MODEL", "llama3.3:70b")
    monkeypatch.setenv("AGENT_MAX_STEPS", "5")
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "10")
    monkeypatch.setenv("AGENT_MAX_REPAIRS", "2")
