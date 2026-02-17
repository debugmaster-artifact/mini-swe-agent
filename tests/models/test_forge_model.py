import os
from unittest.mock import MagicMock, patch

import pytest
from openai import AuthenticationError
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.completion_usage import CompletionUsage

from debugmaster.models import GLOBAL_MODEL_STATS
from debugmaster.models.forge import (
    ForgeAuthenticationError,
    ForgeModel,
)


@pytest.fixture
def mock_response():
    """Create a mock successful Forge API response."""
    return ChatCompletion(
        id="chatcmpl-123",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    content="Hello! 2+2 equals 4.",
                    role="assistant",
                ),
            )
        ],
        created=1234567890,
        model="gpt-4",
        object="chat.completion",
        usage=CompletionUsage(
            prompt_tokens=16,
            completion_tokens=13,
            total_tokens=29,
        ),
    )


@pytest.fixture
def mock_response_no_usage():
    """Create a mock Forge API response without usage information."""
    return ChatCompletion(
        id="chatcmpl-123",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    content="Hello! 2+2 equals 4.",
                    role="assistant",
                ),
            )
        ],
        created=1234567890,
        model="gpt-4",
        object="chat.completion",
        usage=None,
    )


@pytest.fixture
def reset_global_stats():
    """Reset global stats before and after each test."""
    initial_cost = GLOBAL_MODEL_STATS.cost
    initial_calls = GLOBAL_MODEL_STATS.n_calls
    yield
    # Note: Global stats are cumulative, we track delta in tests


def test_forge_model_successful_query(mock_response, reset_global_stats):
    """Test successful Forge API query with cost tracking."""
    with patch.dict(os.environ, {"FORGE_API_KEY": "test-key"}):
        model = ForgeModel(model_name="gpt-4", model_kwargs={"temperature": 0.7})

        initial_cost = GLOBAL_MODEL_STATS.cost
        initial_calls = GLOBAL_MODEL_STATS.n_calls

        with patch.object(model._client.chat.completions, "create") as mock_create:
            mock_create.return_value = mock_response

            messages = [{"role": "user", "content": "Hello! What is 2+2?"}]
            result = model.query(messages)

            # Verify the request was made correctly
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["model"] == "gpt-4"
            assert call_kwargs["messages"] == messages
            assert call_kwargs["temperature"] == 0.7

            # Verify response
            assert result["content"] == "Hello! 2+2 equals 4."
            assert "response" in result["extra"]

            # Verify cost tracking (cost comes from litellm)
            assert model.cost > 0
            assert model.n_calls == 1
            assert GLOBAL_MODEL_STATS.cost > initial_cost
            assert GLOBAL_MODEL_STATS.n_calls == initial_calls + 1


def test_forge_model_authentication_error(reset_global_stats):
    """Test authentication error handling."""
    with patch.dict(os.environ, {"FORGE_API_KEY": "invalid-key"}):
        model = ForgeModel(model_name="gpt-4")

        with patch.object(model._client.chat.completions, "create") as mock_create:
            # Create a mock AuthenticationError
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_create.side_effect = AuthenticationError(
                message="Invalid API key",
                response=mock_response,
                body=None,
            )

            messages = [{"role": "user", "content": "test"}]

            # Patch the retry decorator to avoid waiting
            with patch("debugmaster.models.forge.retry", lambda **kwargs: lambda f: f):
                with pytest.raises(ForgeAuthenticationError) as exc_info:
                    model._query(messages)

                assert "Authentication failed" in str(exc_info.value)
                assert "mini-extra config set FORGE_API_KEY" in str(exc_info.value)


def test_forge_model_no_cost_information(mock_response_no_usage, reset_global_stats):
    """Test error when cost information is missing."""
    with patch.dict(os.environ, {"FORGE_API_KEY": "test-key"}):
        model = ForgeModel(model_name="gpt-4")

        with patch.object(model._client.chat.completions, "create") as mock_create:
            mock_create.return_value = mock_response_no_usage

            messages = [{"role": "user", "content": "test"}]

            with pytest.raises(RuntimeError) as exc_info:
                model.query(messages)

            assert "MSWEA_COST_TRACKING='ignore_errors'" in str(exc_info.value)


def test_forge_model_free_model_zero_cost(mock_response_no_usage, reset_global_stats):
    """Test that free models with zero cost work correctly when cost_tracking='ignore_errors' is set."""
    with patch.dict(os.environ, {"FORGE_API_KEY": "test-key"}):
        model = ForgeModel(model_name="gpt-4", cost_tracking="ignore_errors")

        initial_cost = GLOBAL_MODEL_STATS.cost
        initial_calls = GLOBAL_MODEL_STATS.n_calls

        with patch.object(model._client.chat.completions, "create") as mock_create:
            mock_create.return_value = mock_response_no_usage

            messages = [{"role": "user", "content": "test"}]

            # With cost_tracking='ignore_errors', free models should work without raising an error
            result = model.query(messages)

            # Verify response
            assert result["content"] == "Hello! 2+2 equals 4."
            assert "response" in result["extra"]

            # Verify cost tracking with zero cost
            assert model.cost == 0.0
            assert model.n_calls == 1
            assert GLOBAL_MODEL_STATS.n_calls == initial_calls + 1


def test_forge_model_config():
    """Test Forge model configuration."""
    with patch.dict(os.environ, {"FORGE_API_KEY": "test-key"}):
        model = ForgeModel(
            model_name="gpt-4", model_kwargs={"temperature": 0.5, "max_tokens": 1000}
        )

        assert model.config.model_name == "gpt-4"
        assert model.config.model_kwargs == {"temperature": 0.5, "max_tokens": 1000}
        assert model.config.base_url == "https://api.forge.tensorblock.co/v1"
        assert model._api_key == "test-key"
        assert model.cost == 0.0
        assert model.n_calls == 0


def test_forge_model_custom_base_url():
    """Test Forge model with custom base URL."""
    with patch.dict(os.environ, {"FORGE_API_KEY": "test-key"}):
        model = ForgeModel(
            model_name="gpt-4",
            base_url="http://localhost:8000",
        )

        assert model.config.base_url == "http://localhost:8000"


def test_forge_model_get_template_vars():
    """Test get_template_vars method."""
    with patch.dict(os.environ, {"FORGE_API_KEY": "test-key"}):
        model = ForgeModel(model_name="gpt-4", model_kwargs={"temperature": 0.7})

        # Simulate some usage
        model.cost = 0.001234
        model.n_calls = 5

        template_vars = model.get_template_vars()

        assert template_vars["model_name"] == "gpt-4"
        assert template_vars["model_kwargs"] == {"temperature": 0.7}
        assert template_vars["base_url"] == "https://api.forge.tensorblock.co/v1"
        assert template_vars["n_model_calls"] == 5
        assert template_vars["model_cost"] == 0.001234


def test_forge_model_no_api_key():
    """Test behavior when no API key is provided."""
    with patch.dict(os.environ, {}, clear=True):
        # Remove FORGE_API_KEY if it exists
        os.environ.pop("FORGE_API_KEY", None)
        model = ForgeModel(model_name="gpt-4")

        assert model._api_key == ""
