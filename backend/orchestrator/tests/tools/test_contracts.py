"""
Tests for ToolContract and ToolParameter.
"""

import pytest

from tools.contracts import (
    ToolContract,
    ToolParameter,
    validate_path_safe,
    validate_sql_safe,
    validate_url_safe,
)


class TestToolParameter:
    """Tests for ToolParameter dataclass."""

    def test_basic_string_parameter(self):
        """Test basic string parameter."""
        param = ToolParameter(
            name="query",
            type="string",
            description="Search query",
            required=True,
        )

        assert param.name == "query"
        assert param.type == "string"
        assert param.required is True

    def test_integer_parameter_with_range(self):
        """Test integer parameter with min/max."""
        param = ToolParameter(
            name="limit",
            type="integer",
            description="Max results",
            required=False,
            default=10,
            minimum=1,
            maximum=100,
        )

        assert param.minimum == 1
        assert param.maximum == 100
        assert param.default == 10

    def test_parameter_with_enum(self):
        """Test parameter with enum values."""
        param = ToolParameter(
            name="format",
            type="string",
            description="Output format",
            required=False,
            enum=["json", "csv", "text"],
        )

        assert param.enum == ["json", "csv", "text"]

    def test_to_json_schema(self):
        """Test conversion to JSON Schema."""
        param = ToolParameter(
            name="count",
            type="integer",
            description="Number of items",
            required=False,
            minimum=0,
            maximum=50,
        )

        schema = param.to_json_schema()

        assert schema["type"] == "integer"
        assert schema["description"] == "Number of items"
        assert schema["minimum"] == 0
        assert schema["maximum"] == 50


class TestToolContract:
    """Tests for ToolContract class."""

    @pytest.fixture
    def simple_contract(self):
        """Create a simple tool contract."""
        return ToolContract(
            name="test_tool",
            description="A test tool",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Search query",
                    required=True,
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Max results",
                    required=False,
                    default=10,
                ),
            ],
        )

    def test_contract_initialization(self, simple_contract):
        """Test contract creation."""
        assert simple_contract.name == "test_tool"
        assert len(simple_contract.parameters) == 2

    def test_validate_params_valid(self, simple_contract):
        """Test validation of valid parameters."""
        params = {"query": "test search", "limit": 5}

        is_valid, error, suggestions = simple_contract.validate_params(params)

        assert is_valid is True
        assert error is None

    def test_validate_params_missing_required(self, simple_contract):
        """Test validation catches missing required params."""
        params = {"limit": 5}  # Missing 'query'

        is_valid, error, suggestions = simple_contract.validate_params(params)

        assert is_valid is False
        assert error is not None
        assert "query" in error.lower()

    def test_validate_params_wrong_type(self, simple_contract):
        """Test validation catches wrong parameter types."""
        params = {"query": "test", "limit": "not a number"}

        is_valid, error, suggestions = simple_contract.validate_params(params)

        assert is_valid is False
        assert error is not None
        assert "limit" in error.lower()

    def test_validate_params_unknown_param(self, simple_contract):
        """Test validation catches unknown parameters."""
        params = {"query": "test", "unknown_param": "value"}

        is_valid, error, suggestions = simple_contract.validate_params(params)

        assert is_valid is False
        assert error is not None
        assert "unknown" in error.lower()

    def test_normalize_adds_defaults(self, simple_contract):
        """Test normalization adds default values."""
        params = {"query": "test"}

        normalized = simple_contract.normalize(params)

        assert normalized["query"] == "test"
        assert normalized["limit"] == 10

    def test_normalize_preserves_provided(self, simple_contract):
        """Test normalization preserves provided values."""
        params = {"query": "test", "limit": 25}

        normalized = simple_contract.normalize(params)

        assert normalized["limit"] == 25

    def test_to_openai_tool(self, simple_contract):
        """Test conversion to OpenAI tool format."""
        tool = simple_contract.to_openai_tool()

        assert tool["type"] == "function"
        assert tool["function"]["name"] == "test_tool"
        assert tool["function"]["description"] == "A test tool"
        assert "parameters" in tool["function"]
        assert tool["function"]["parameters"]["type"] == "object"

    def test_integer_range_validation(self):
        """Test integer range validation."""
        contract = ToolContract(
            name="range_test",
            description="Test range validation",
            parameters=[
                ToolParameter(
                    name="value",
                    type="integer",
                    description="A value",
                    required=True,
                    minimum=1,
                    maximum=10,
                ),
            ],
        )

        # Valid value
        is_valid, error, _ = contract.validate_params({"value": 5})
        assert is_valid is True

        # Below minimum
        is_valid, error, _ = contract.validate_params({"value": 0})
        assert is_valid is False
        assert "minimum" in error.lower()

        # Above maximum
        is_valid, error, _ = contract.validate_params({"value": 15})
        assert is_valid is False
        assert "maximum" in error.lower()

    def test_enum_validation(self):
        """Test enum value validation."""
        contract = ToolContract(
            name="enum_test",
            description="Test enum validation",
            parameters=[
                ToolParameter(
                    name="format",
                    type="string",
                    description="Format",
                    required=True,
                    enum=["json", "csv"],
                ),
            ],
        )

        # Valid enum value
        is_valid, error, _ = contract.validate_params({"format": "json"})
        assert is_valid is True

        # Invalid enum value
        is_valid, error, _ = contract.validate_params({"format": "xml"})
        assert is_valid is False
        assert "xml" in error.lower() or "enum" in error.lower() or "one of" in error.lower()


class TestCustomValidators:
    """Tests for custom validation functions."""

    def test_validate_sql_safe_valid(self):
        """Test SQL validation with safe queries."""
        safe_queries = [
            "SELECT * FROM users",
            "SELECT name, email FROM contacts WHERE id = 1",
            "SELECT COUNT(*) FROM events",
        ]

        for query in safe_queries:
            result = validate_sql_safe(query)
            assert result is True, f"Query should be safe: {query}"

    def test_validate_sql_safe_dangerous(self):
        """Test SQL validation catches dangerous patterns."""
        dangerous_queries = [
            "DROP TABLE users",
            "DELETE FROM contacts",
            "TRUNCATE events",
            "INSERT INTO users VALUES (1, 'test')",
            "UPDATE users SET admin = true",
        ]

        for query in dangerous_queries:
            result = validate_sql_safe(query)
            assert result is False, f"Query should be blocked: {query}"

    def test_validate_path_safe_valid(self):
        """Test path validation with safe paths."""
        safe_paths = [
            "/home/user/documents",
            "/var/log/app.log",
            "relative/path/file.txt",
        ]

        for path in safe_paths:
            result = validate_path_safe(path)
            assert result is True, f"Path should be safe: {path}"

    def test_validate_path_safe_dangerous(self):
        """Test path validation catches dangerous patterns."""
        dangerous_paths = [
            "../../../etc/passwd",
            "~/.ssh/id_rsa",
        ]

        for path in dangerous_paths:
            result = validate_path_safe(path)
            assert result is False, f"Path should be blocked: {path}"

    def test_validate_url_safe_valid(self):
        """Test URL validation with safe URLs."""
        safe_urls = [
            "https://example.com",
            "http://localhost:8080/api",
            "https://api.service.com/v1/data",
        ]

        for url in safe_urls:
            result = validate_url_safe(url)
            assert result is True, f"URL should be safe: {url}"

    def test_validate_url_safe_dangerous(self):
        """Test URL validation catches dangerous patterns."""
        dangerous_urls = [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
        ]

        for url in dangerous_urls:
            result = validate_url_safe(url)
            assert result is False, f"URL should be blocked: {url}"


class TestContractWithValidators:
    """Tests for contracts with custom validators."""

    def test_sql_tool_contract(self):
        """Test SQL tool contract with safety validation."""
        contract = ToolContract(
            name="execute_sql",
            description="Execute SQL query",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="SQL query",
                    required=True,
                ),
            ],
            value_validators={"query": validate_sql_safe},
        )

        # Safe query passes
        is_valid, error, _ = contract.validate_params({"query": "SELECT * FROM users"})
        assert is_valid is True

        # Dangerous query fails
        is_valid, error, _ = contract.validate_params({"query": "DROP TABLE users"})
        assert is_valid is False


class TestGetValidationFeedback:
    """Tests for get_validation_feedback method."""

    @pytest.fixture
    def contract(self):
        return ToolContract(
            name="test",
            description="Test",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Query",
                    required=True,
                ),
            ],
        )

    def test_feedback_for_valid_params(self, contract):
        """Test feedback for valid parameters."""
        feedback = contract.get_validation_feedback({"query": "test"})
        assert feedback["valid"] is True

    def test_feedback_for_invalid_params(self, contract):
        """Test feedback for invalid parameters."""
        feedback = contract.get_validation_feedback({})  # Missing query
        assert feedback["valid"] is False
        assert "error" in feedback
        assert "required_fields" in feedback
        assert "query" in feedback["required_fields"]
