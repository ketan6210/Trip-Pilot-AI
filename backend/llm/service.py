import json
from decimal import Decimal
from time import perf_counter

from pydantic import BaseModel, ValidationError

from backend.llm.providers import LLMProvider, LLMUnavailable, get_llm_provider
from backend.llm.telemetry import active_trace
from backend.settings import settings


def _record(provider, purpose, status, started, reply=None):
    trace = active_trace.get()
    if trace is None:
        return
    incoming, outgoing = (reply.input_tokens, reply.output_tokens) if reply else (None, None)
    cost = None
    rates = settings.llm_input_usd_per_million, settings.llm_output_usd_per_million
    if incoming is not None and outgoing is not None and all(value is not None for value in rates):
        cost = (Decimal(incoming) * Decimal(str(rates[0])) + Decimal(outgoing) * Decimal(str(rates[1]))) / 1000000
    trace.calls.append(
        {
            "provider": provider.name,
            "model": provider.model,
            "purpose": purpose,
            "status": status,
            "input_tokens": incoming,
            "output_tokens": outgoing,
            "estimated_cost_usd": cost,
            "latency_ms": round((perf_counter() - started) * 1000, 3),
        }
    )


def structured[T: BaseModel](
    schema: type[T], messages: list[dict], *, purpose: str, provider: LLMProvider | None = None, registry=None
) -> T:
    provider = provider or get_llm_provider()
    messages = list(messages)
    corrections = 0
    tool_rounds = 0
    while corrections < settings.llm_correction_attempts:
        started = perf_counter()
        reply = None
        try:
            reply = provider.complete(
                messages,
                schema.model_json_schema(),
                registry.definitions() if registry and tool_rounds < 2 and corrections == 0 else [],
            )
        except Exception as exc:
            _record(provider, purpose, "provider_error", started)
            if isinstance(exc, LLMUnavailable):
                raise
            raise LLMUnavailable("Language model unavailable") from exc
        if reply.calls:
            if not registry or tool_rounds >= 2 or len(reply.calls) > 4:
                _record(provider, purpose, "invalid", started, reply)
                raise LLMUnavailable("Model exceeded the tool-call boundary")
            # Native function calls are executed, then their outputs go back to this model.
            _record(provider, purpose, "valid", started, reply)
            messages.extend(reply.continuation)
            for call in reply.calls:
                result = registry.execute(call.name, call.arguments)
                messages.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.id,
                        "name": call.name,
                        "output": json.dumps(result, default=str),
                    }
                )
            tool_rounds += 1
            continue
        try:
            value = schema.model_validate_json(reply.content)
            if registry:
                registry.validate_output(value)
        except (ValueError, ValidationError) as exc:
            _record(provider, purpose, "invalid", started, reply)
            corrections += 1
            # No provider text is promoted to instructions or parsed with prose regexes.
            messages.append(
                {
                    "role": "user",
                    "content": f"Your previous response failed validation ({type(exc).__name__}). Return only the requested schema; use only IDs supplied in tool results/context.",
                }
            )
            continue
        _record(provider, purpose, "valid", started, reply)
        return value
    raise LLMUnavailable("Structured output remained invalid after bounded correction")
