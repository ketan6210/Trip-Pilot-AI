"""Run synthetic benchmarks in a newly created temporary database, never user data."""

import argparse
import json
import os
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import mean, median
from time import perf_counter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="artifacts/phase2-evaluation.json")
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Use the explicitly configured model; may incur its configured provider costs",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="trippilot-evaluation-") as directory:
        os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(directory) / "evaluation.db")
        os.environ["PLACE_PROVIDER"] = "mock"
        if not args.use_llm:
            os.environ["ORCHESTRATOR_PROVIDER"] = "deterministic"
        from alembic import command
        from alembic.config import Config
        from fastapi.testclient import TestClient
        from sqlalchemy import select

        from backend.db import SessionLocal
        from backend.evaluation.metrics import evaluate_plan
        from backend.evaluation.scenarios import SCENARIOS
        from backend.main import app
        from backend.models import Place
        from backend.settings import settings

        command.upgrade(Config("alembic.ini"), "head")
        results = []
        with TestClient(app) as client:
            account = client.post(
                "/api/v1/auth/register",
                json={
                    "email": "benchmark@example.test",
                    "display_name": "Synthetic benchmark",
                    "password": "temporary-benchmark-password",
                },
            )
            account.raise_for_status()
            client.headers["Authorization"] = "Bearer " + account.json()["access_token"]
            with SessionLocal() as db:
                for place in db.scalars(select(Place)):
                    place.opening_hours = {"raw": "18:00-02:00" if place.category == "nightlife" else "08:00-23:00"}
                    place.age_restriction = 21 if place.category == "nightlife" else None
                    place.source_metadata = {
                        "fixture": True,
                        "accessibility": "yes",
                        "dietary_options": ["vegetarian", "vegan"],
                        "evidence_source": "Synthetic benchmark fixture",
                    }
                db.commit()
            for scenario in SCENARIOS:
                trip_response = client.post(
                    "/api/v1/trips",
                    json={
                        "origin": "Chicago",
                        "destination": "New York City",
                        "start_date": "2027-06-01",
                        "duration_days": 2,
                        "travelers": 2,
                        "budget_amount": scenario["budget"],
                        "currency": "USD",
                        "interests": scenario["interests"],
                    },
                )
                trip_response.raise_for_status()
                trip_id = trip_response.json()["id"]
                trip = client.patch(f"/api/v1/trips/{trip_id}", json={"flight_included": False}).json()
                policy = client.put(f"/api/v1/trips/{trip_id}/ai/constraints", json=scenario["policy"])
                policy.raise_for_status()
                preferences = [set(scenario["interests"])] * 2
                if scenario.get("second_interests"):
                    travelers = client.get(f"/trips/{trip_id}/travelers").json()
                    response = client.put(
                        f"/travelers/{travelers[1]['id']}/preferences",
                        json={
                            "preferences": [
                                {"category": key, "score": value} for key, value in scenario["second_interests"].items()
                            ]
                        },
                    )
                    response.raise_for_status()
                    preferences = [preferences[0], set(scenario["second_interests"])]
                started = perf_counter()
                response = client.post(f"/api/v1/trips/{trip_id}/ai/generate", json={"use_llm": args.use_llm})
                row = {
                    "scenario": scenario["name"],
                    "http_status": response.status_code,
                    "latency_ms": round((perf_counter() - started) * 1000, 3),
                    "expected_rejection": scenario.get("expected_rejection", False),
                }
                if response.status_code == 200:
                    data = response.json()
                    row["plans"] = [evaluate_plan(plan, trip, preferences) for plan in data["plans"]]
                    row["metrics"] = data["metrics"]
                    row["distinct_activity_sets"] = len(
                        {tuple(sorted(plan["summary"]["ai_place_ids"])) for plan in data["plans"]}
                    )
                    row["passed"] = (
                        not row["expected_rejection"]
                        and row["distinct_activity_sets"] == 4
                        and all(
                            plan["constraint_satisfaction_rate"] == 1 and plan["provider_reference_grounding_rate"] == 1
                            for plan in row["plans"]
                        )
                    )
                else:
                    row["error"] = response.json()
                    recent = client.get(f"/api/v1/trips/{trip_id}/ai/runs").json()
                    row["failed_run"] = recent[0] if recent else None
                    row["passed"] = row["expected_rejection"] and response.status_code == 422
                results.append(row)
        plans = [plan for row in results for plan in row.get("plans", [])]
        run_metrics = [row.get("metrics", (row.get("failed_run") or {}).get("metrics", {})) for row in results]
        received = sum(row.get("received_responses", 0) for row in run_metrics)
        references = sum(row.get("references_checked", 0) for row in run_metrics)
        tools = [tool for row in run_metrics for tool in row.get("tools", [])]
        costs = [row.get("estimated_cost_usd") for row in run_metrics]
        metrics = {
            "scenarios_passed": sum(row["passed"] for row in results),
            "scenarios_total": len(results),
            "plans_evaluated": len(plans),
            "constraint_satisfaction_rate": mean(plan["constraint_satisfaction_rate"] for plan in plans)
            if plans
            else None,
            "budget_compliance_rate": mean(plan["budget_compliant"] for plan in plans) if plans else None,
            "schedule_validity_rate": mean(plan["schedule_valid"] for plan in plans) if plans else None,
            "provider_reference_grounding_rate": mean(plan["provider_reference_grounding_rate"] for plan in plans)
            if plans
            else None,
            "mean_preference_coverage": mean(
                plan["preference_coverage"] for plan in plans if plan["preference_coverage"] is not None
            )
            if plans
            else None,
            "median_generation_latency_ms": median(row["latency_ms"] for row in results),
            "llm_calls": sum(row.get("llm_calls", 0) for row in run_metrics),
            "input_tokens": sum(row.get("input_tokens", 0) for row in run_metrics),
            "output_tokens": sum(row.get("output_tokens", 0) for row in run_metrics),
            "usage_complete": all(row.get("usage_complete", False) for row in run_metrics),
            "estimated_cost_usd": str(sum((Decimal(cost) for cost in costs), Decimal(0)))
            if all(cost is not None for cost in costs)
            else None,
            "structured_output_validity": sum(row.get("valid_structured_responses", 0) for row in run_metrics)
            / received
            if received
            else None,
            "unsupported_reference_rate": sum(row.get("unsupported_references", 0) for row in run_metrics) / references
            if references
            else None,
            "tool_calls": len(tools),
            "tool_success_rate": sum(tool["success"] for tool in tools) / len(tools) if tools else None,
            "fallback_requests": sum(row.get("fallback_used", False) for row in run_metrics),
        }
        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "evidence": "Synthetic mock-provider fixtures with the configured model; not live travel evidence"
            if args.use_llm
            else "Synthetic mock-provider fixtures; not live travel or real-model quality evidence",
            "llm_enabled": args.use_llm,
            "configured_llm_provider": settings.orchestrator_provider,
            "embedding_backend": settings.embedding_backend,
            "metrics": metrics,
            "scenarios": results,
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(metrics, indent=2))
        print(f"Saved {output}")
        if not all(row["passed"] for row in results):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
