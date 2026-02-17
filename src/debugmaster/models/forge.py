import logging
import os
from typing import Any, Literal

import litellm
from openai import AuthenticationError, OpenAI, RateLimitError
from pydantic import BaseModel
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from debugmaster.models import GLOBAL_MODEL_STATS
from debugmaster.models.utils.cache_control import set_cache_control

logger = logging.getLogger("forge_model")


class ForgeModelConfig(BaseModel):
    model_name: str
    model_kwargs: dict[str, Any] = {}
    base_url: str = "https://api.forge.tensorblock.co/v1"
    set_cache_control: Literal["default_end"] | None = None
    """Set explicit cache control markers, for example for Anthropic models"""
    cost_tracking: Literal["default", "ignore_errors"] = os.getenv("MSWEA_COST_TRACKING", "default")
    """Cost tracking mode for this model. Can be "default" or "ignore_errors" (ignore errors/missing cost info)"""


class ForgeAPIError(Exception):
    """Custom exception for Forge API errors."""

    pass


class ForgeAuthenticationError(Exception):
    """Custom exception for Forge authentication errors."""

    pass


class ForgeRateLimitError(Exception):
    """Custom exception for Forge rate limit errors."""

    pass


# Parameters that are litellm-specific and not supported by the OpenAI client
LITELLM_SPECIFIC_PARAMS = {"drop_params"}


class ForgeModel:
    def __init__(self, **kwargs):
        self.config = ForgeModelConfig(**kwargs)
        self.cost = 0.0
        self.n_calls = 0
        self._api_key = os.getenv("FORGE_API_KEY", "")
        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self.config.base_url,
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(int(os.getenv("MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT", "10"))),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        retry=retry_if_not_exception_type(
            (
                ForgeAuthenticationError,
                KeyboardInterrupt,
            )
        ),
    )
    def _query(self, messages: list[dict[str, str]], **kwargs):
        # Filter out litellm-specific parameters that the OpenAI client doesn't support
        filtered_kwargs = {
            k: v for k, v in (self.config.model_kwargs | kwargs).items() if k not in LITELLM_SPECIFIC_PARAMS
        }
        try:
            response = self._client.chat.completions.create(
                model=self.config.model_name,
                messages=messages,
                **filtered_kwargs,
            )
            return response
        except AuthenticationError as e:
            error_msg = "Authentication failed. You can permanently set your API key with `mini-extra config set FORGE_API_KEY YOUR_KEY`."
            raise ForgeAuthenticationError(error_msg) from e
        except RateLimitError as e:
            raise ForgeRateLimitError("Rate limit exceeded") from e
        except Exception as e:
            # Retry with temperature=1 if the error is related to unsupported temperature value
            if "temperature" in str(e):
                filtered_kwargs["temperature"] = 1
                try:
                    response = self._client.chat.completions.create(
                        model=self.config.model_name,
                        messages=messages,
                        **filtered_kwargs,
                    )
                    return response
                except Exception as retry_e:
                    raise ForgeAPIError(f"Forge API error: {retry_e}") from retry_e
            raise ForgeAPIError(f"Forge API error: {e}") from e

    def query(self, messages: list[dict[str, str]], **kwargs) -> dict:
        if self.config.set_cache_control:
            messages = set_cache_control(messages, mode=self.config.set_cache_control)
        response = self._query([{"role": msg["role"], "content": msg["content"]} for msg in messages], **kwargs)

        try:
            cost = litellm.cost_calculator.completion_cost(response, model=self.config.model_name)
            if cost <= 0.0:
                raise ValueError(f"Cost must be > 0.0, got {cost}")
        except Exception as e:
            cost = 0.0
            if self.config.cost_tracking != "ignore_errors":
                msg = (
                    f"Error calculating cost for model {self.config.model_name}: {e}, perhaps it's not registered? "
                    "You can ignore this issue from your config file with cost_tracking: 'ignore_errors' or "
                    "globally with export MSWEA_COST_TRACKING='ignore_errors'. "
                    "Alternatively check the 'Cost tracking' section in the documentation at "
                    "https://klieret.short.gy/mini-local-models. "
                    " Still stuck? Please open a github issue at https://github.com/SWE-agent/mini-swe-agent/issues/new/choose!"
                )
                logger.critical(msg)
                raise RuntimeError(msg) from e

        self.n_calls += 1
        self.cost += cost
        GLOBAL_MODEL_STATS.add(cost)

        return {
            "content": response.choices[0].message.content or "",
            "extra": {
                "response": response.model_dump(),
            },
        }

    def get_template_vars(self) -> dict[str, Any]:
        return self.config.model_dump() | {"n_model_calls": self.n_calls, "model_cost": self.cost}
