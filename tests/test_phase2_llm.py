from decimal import Decimal

import pytest

from backend.ai.schemas import RankingAdvice
from backend.llm.providers import LLMReply, LLMUnavailable, ToolCall
from backend.llm.service import structured
from backend.llm.telemetry import Trace, tracing
from backend.settings import settings


class FakeModel:
    name = "test_double"
    model = "scripted_no_real_model"

    def __init__(self, replies):
        self.replies = iter(replies)
        self.messages = []

    def complete(self, messages, schema, tools):
        self.messages.append(list(messages))
        response = next(self.replies)
        if isinstance(response, Exception):
            raise response
        return response


def test_bounded_schema_correction_tracks_observed_usage(monkeypatch):
    monkeypatch.setattr(settings, "llm_input_usd_per_million", 1.0)
    monkeypatch.setattr(settings, "llm_output_usd_per_million", 2.0)
    model = FakeModel(
        [
            LLMReply("not JSON", input_tokens=10, output_tokens=2),
            LLMReply('{"recommendations":[]}', input_tokens=20, output_tokens=4),
        ]
    )
    trace = Trace()
    with tracing(trace):
        result = structured(RankingAdvice, [{"role": "user", "content": "rank"}], purpose="test", provider=model)
    assert result.recommendations == []
    assert trace.metrics()["structured_output_validity"] == 0.5
    assert trace.metrics()["input_tokens"] == 30
    assert Decimal(trace.metrics()["estimated_cost_usd"]) == Decimal("0.000042")
    assert len(model.messages) == 2


def test_invalid_output_and_provider_failures_are_bounded():
    model = FakeModel([LLMReply("bad")] * 5)
    with pytest.raises(LLMUnavailable):
        structured(RankingAdvice, [], purpose="test", provider=model)
    assert len(model.messages) == settings.llm_correction_attempts
    trace = Trace()
    with tracing(trace), pytest.raises(LLMUnavailable):
        structured(RankingAdvice, [], purpose="test", provider=FakeModel([RuntimeError("offline")]))
    assert trace.metrics()["usage_complete"] is False
    assert trace.metrics()["estimated_cost_usd"] is None


def test_native_tool_calls_execute_and_return_results_to_model(client):
    from backend.ai.constraints import prepare_candidates
    from backend.ai.group import policy_for
    from backend.ai.pipeline import loaded_trip
    from backend.ai.ranking import rank_candidates
    from backend.ai.retrieval import retrieve
    from backend.ai.tools import PlanningTools
    from backend.db import SessionLocal
    from tests.phase2_fixtures import advanced_trip

    trip_id = advanced_trip(client)["id"]
    with SessionLocal() as db:
        trip = loaded_trip(db, trip_id)
        policy = policy_for(db, trip)
        _, records = retrieve(db, trip, policy)
        candidates, _ = prepare_candidates(records, trip, policy)
        ranked = rank_candidates(candidates, trip.travelers, policy, "balanced")
        registry = PlanningTools(db, trip, candidates, policy, ranked, {"hotel": 10000})
        place_id = candidates[0].place_id
        model = FakeModel(
            [
                LLMReply(
                    calls=[ToolCall("call-1", "get_place_details", {"place_id": place_id})],
                    continuation=[{"role": "assistant", "content": ""}],
                    input_tokens=20,
                    output_tokens=5,
                ),
                LLMReply(
                    '{"recommendations":[{"place_id":'
                    + str(place_id)
                    + ',"relevance":0.9,"reason":"preference_match"}]}',
                    input_tokens=30,
                    output_tokens=10,
                ),
            ]
        )
        trace = Trace()
        with tracing(trace):
            result = structured(RankingAdvice, [], purpose="test", provider=model, registry=registry)
        assert result.recommendations[0].place_id == place_id
        assert any(
            item.get("call_id") == "call-1" and candidates[0].name in item["output"] for item in model.messages[1]
        )
        assert trace.metrics()["tool_success_rate"] == 1
        assert registry.execute("delete_database", {})["error"]
        assert registry.execute("get_place_details", {"place_id": 999999})["error"]
        with pytest.raises(ValueError):
            registry.validate_output(
                RankingAdvice.model_validate(
                    {"recommendations": [{"place_id": 999999, "relevance": 1, "reason": "variety"}]}
                )
            )


def test_models_cannot_supply_prices_or_addresses():
    model = FakeModel([LLMReply('{"recommendations":[],"hotel_price":10,"address":"invented"}')] * 3)
    with pytest.raises(LLMUnavailable):
        structured(RankingAdvice, [], purpose="test", provider=model)


def test_display_names_reject_factual_prose_and_legacy_generation_records_usage(client, monkeypatch):
    import json

    from backend.naming.plan_name_generator import PROFILE_WORDS, DisplayNameBatch
    from tests.phase2_fixtures import advanced_trip

    with pytest.raises(ValueError):
        DisplayNameBatch.model_validate(
            {"names": [{"plan_type": key, "emphasis": "interests", "title": "Flights $10"} for key in PROFILE_WORDS]}
        )
    trip = advanced_trip(client)
    model = FakeModel(
        [
            LLMReply(
                json.dumps({"names": [{"plan_type": key, "emphasis": "interests"} for key in PROFILE_WORDS]}),
                input_tokens=42,
                output_tokens=12,
            )
        ]
    )
    monkeypatch.setattr(settings, "orchestrator_provider", "openai")
    monkeypatch.setattr("backend.llm.service.get_llm_provider", lambda: model)
    response = client.post(f"/api/v1/trips/{trip['id']}/generate")
    assert response.status_code == 200, response.text
    runs = client.get(f"/api/v1/trips/{trip['id']}/ai/runs").json()
    run = next(row for row in runs if row["operation"] == "legacy_generate")
    assert run["metrics"]["llm_calls"] == 1
    assert run["metrics"]["input_tokens"] == 42
    assert run["metrics"]["estimated_cost_usd"] is None


def test_ollama_native_tool_schema_does_not_conflict_with_final_answer_grammar(monkeypatch):
    from backend.llm.providers import OllamaProvider

    payloads = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "message": {"role": "assistant", "content": '{"recommendations":[]}'},
                "prompt_eval_count": 2,
                "eval_count": 3,
            }

    def post(url, *, json, timeout):
        payloads.append(json)
        return Response()

    monkeypatch.setattr("backend.llm.providers.httpx.post", post)
    provider = OllamaProvider()
    schema = RankingAdvice.model_json_schema()
    tool = {
        "type": "function",
        "name": "search_places",
        "description": "Find known places",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": True,
    }
    provider.complete([], schema, [tool])
    provider.complete([], schema, [])
    assert payloads[0]["tools"][0]["function"]["parameters"] == tool["parameters"]
    assert "format" not in payloads[0]
    assert payloads[1]["format"] == schema
