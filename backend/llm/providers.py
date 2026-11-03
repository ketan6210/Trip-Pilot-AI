import copy
import json
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from backend.settings import settings


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMReply:
    content: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    continuation: list[dict] = field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMUnavailable(Exception):
    def __init__(self, message: str, code: str = "LLM_UNAVAILABLE"):
        super().__init__(message)
        self.code = code


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, messages: list[dict], schema: dict, tools: list[dict]) -> LLMReply: ...


def strict_schema(schema: dict) -> dict:
    """Convert optional/default fields to required nullable fields for API strict mode."""
    result = copy.deepcopy(schema)

    def visit(value):
        if isinstance(value, dict):
            value.pop("default", None)
            if value.get("type") == "object":
                value["additionalProperties"] = False
                value["required"] = list(value.get("properties", {}))
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(result)
    return result


class OllamaProvider:
    name = "ollama"

    @property
    def model(self):
        return settings.ollama_model

    def complete(self, messages, schema, tools):
        converted = []
        for item in messages:
            if item.get("type") == "function_call_output":
                converted.append({"role": "tool", "tool_name": item["name"], "content": item["output"]})
            else:
                converted.append(item)
        payload = {
            "model": self.model,
            "messages": converted,
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "num_predict": 2400},
        }
        if tools:
            # Ollama's final-answer grammar masks native tool-call tokens. During
            # tool selection the function argument schemas govern the response;
            # the service still validates any returned final JSON with Pydantic.
            payload["tools"] = [
                {"type": "function", "function": {k: v for k, v in t.items() if k not in {"type", "strict"}}}
                for t in tools
            ]
            payload["messages"] = [
                {
                    "role": "system",
                    "content": "For a final answer instead of a tool call, return only JSON matching this schema: "
                    + json.dumps(schema),
                },
                *converted,
            ]
        else:
            payload["format"] = schema
        response = httpx.post(
            f"{settings.ollama_base_url.rstrip('/')}/api/chat", json=payload, timeout=settings.llm_timeout_seconds
        )
        response.raise_for_status()
        data = response.json()
        message = data["message"]
        calls = [
            ToolCall(str(index), row["function"]["name"], row["function"]["arguments"])
            for index, row in enumerate(message.get("tool_calls", []))
        ]
        return LLMReply(
            message.get("content", ""), calls, [message], data.get("prompt_eval_count"), data.get("eval_count")
        )


class OpenAIProvider:
    name = "openai"

    @property
    def model(self):
        return settings.openai_model

    def complete(self, messages, schema, tools):
        from openai import OpenAI

        if not settings.openai_api_key:
            raise LLMUnavailable("OpenAI credentials are not configured")
        # Never persist remote conversations or share a previous_response_id between trips.
        inputs = [
            {k: v for k, v in item.items() if k != "name"} if item.get("type") == "function_call_output" else item
            for item in messages
        ]
        with OpenAI(
            api_key=settings.openai_api_key.get_secret_value(), timeout=settings.llm_timeout_seconds, max_retries=0
        ) as client:
            response = client.responses.create(
                model=self.model,
                input=inputs,
                store=False,
                include=["reasoning.encrypted_content"],
                tools=tools,
                max_output_tokens=2400,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "trip_output",
                        "strict": True,
                        "schema": strict_schema(schema),
                    }
                },
            )
        calls = [
            ToolCall(item.call_id, item.name, json.loads(item.arguments))
            for item in response.output
            if item.type == "function_call"
        ]
        usage = response.usage
        return LLMReply(
            response.output_text,
            calls,
            [item.model_dump(exclude_none=True) for item in response.output],
            usage.input_tokens if usage else None,
            usage.output_tokens if usage else None,
        )


class HuggingFaceProvider:
    """HF-routed chat completions; credentials never leave the backend."""

    name = "huggingface"

    @property
    def model(self):
        return settings.hf_model

    def complete(self, messages, schema, tools):
        if not settings.hf_token:
            raise LLMUnavailable("Hugging Face token is not configured", "LLM_NOT_CONFIGURED")
        converted = [
            {"role": "tool", "tool_call_id": item["call_id"], "content": item["output"]}
            if item.get("type") == "function_call_output"
            else item
            for item in messages
        ]
        payload = {
            "model": self.model,
            "messages": converted,
            "stream": False,
            "temperature": 0,
            "max_tokens": 2400,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "trip_output",
                    "strict": True,
                    "schema": strict_schema(schema),
                },
            },
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {k: v for k, v in tool.items() if k != "type"}} for tool in tools
            ]
            payload["tool_choice"] = "auto"
        try:
            response = httpx.post(
                "https://router.huggingface.co/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.hf_token.get_secret_value()}"},
                json=payload,
                timeout=settings.llm_timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            code = {
                401: "LLM_AUTH_FAILED",
                403: "LLM_AUTH_FAILED",
                402: "LLM_CREDITS_EXHAUSTED",
                429: "LLM_RATE_LIMITED",
                400: "LLM_MODEL_UNSUPPORTED",
                404: "LLM_MODEL_UNSUPPORTED",
                422: "LLM_MODEL_UNSUPPORTED",
            }.get(exc.response.status_code, "LLM_UNAVAILABLE")
            # Provider bodies can echo prompts; never expose them to the UI/logs.
            raise LLMUnavailable("Hugging Face request failed", code) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable("Hugging Face could not be reached") from exc
        data = response.json()
        message = data["choices"][0]["message"]
        calls = [
            ToolCall(row["id"], row["function"]["name"], json.loads(row["function"]["arguments"]))
            for row in message.get("tool_calls", [])
        ]
        continuation = {key: value for key, value in message.items() if key in {"role", "content", "tool_calls"}}
        usage = data.get("usage") or {}
        return LLMReply(
            message.get("content") or "",
            calls,
            [continuation],
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
        )


def get_llm_provider() -> LLMProvider:
    configured = settings.orchestrator_provider.casefold()
    if configured in {"huggingface", "hf"} or (configured == "auto" and settings.hf_token):
        return HuggingFaceProvider()
    if configured in {"ollama", "auto"}:
        return OllamaProvider()
    if configured == "openai":
        return OpenAIProvider()
    raise LLMUnavailable("Deterministic mode is enabled")
