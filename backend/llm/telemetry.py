from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal
from time import perf_counter

from backend.models import LLMCall, PlanningRun


@dataclass
class Trace:
    calls: list[dict] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    stages: list[dict] = field(default_factory=list)
    unsupported_references: int = 0
    reference_count: int = 0
    fallback: bool = False
    started: float = field(default_factory=perf_counter)

    @contextmanager
    def stage(self, name: str):
        start = perf_counter()
        row = {"name": name, "status": "ok"}
        try:
            yield
        except Exception:
            row["status"] = "failed"
            raise
        finally:
            row["latency_ms"] = round((perf_counter() - start) * 1000, 3)
            self.stages.append(row)

    def metrics(self) -> dict:
        received = [x for x in self.calls if x["status"] != "provider_error"]
        costs = [x["estimated_cost_usd"] for x in self.calls]
        return {
            "llm_calls": len(self.calls),
            "input_tokens": sum(x["input_tokens"] or 0 for x in self.calls),
            "output_tokens": sum(x["output_tokens"] or 0 for x in self.calls),
            "usage_complete": all(x["input_tokens"] is not None and x["output_tokens"] is not None for x in self.calls),
            "estimated_cost_usd": str(sum(costs, Decimal(0))) if all(x is not None for x in costs) else None,
            "structured_output_validity": sum(x["status"] == "valid" for x in received) / len(received)
            if received
            else None,
            "received_responses": len(received),
            "valid_structured_responses": sum(x["status"] == "valid" for x in received),
            "tool_calls": len(self.tools),
            "tool_success_rate": sum(x["success"] for x in self.tools) / len(self.tools) if self.tools else None,
            "unsupported_reference_rate": self.unsupported_references / self.reference_count
            if self.reference_count
            else None,
            "references_checked": self.reference_count,
            "unsupported_references": self.unsupported_references,
            "fallback_used": self.fallback,
            "tools": self.tools,
        }

    def save(
        self,
        db,
        *,
        trip_id=None,
        session_id=None,
        user_id=None,
        operation="generate",
        status="succeeded",
        error_code=None,
        extra=None,
    ):
        run = PlanningRun(
            trip_id=trip_id,
            session_id=session_id,
            user_id=user_id,
            operation=operation,
            status=status,
            error_code=error_code,
            latency_ms=round((perf_counter() - self.started) * 1000, 3),
            stages=self.stages,
            metrics={**self.metrics(), **(extra or {})},
        )
        db.add(run)
        db.flush()
        db.add_all([LLMCall(run_id=run.id, **call) for call in self.calls])
        return run


active_trace: ContextVar[Trace | None] = ContextVar("planning_trace", default=None)


@contextmanager
def tracing(trace: Trace):
    token = active_trace.set(trace)
    try:
        yield trace
    finally:
        active_trace.reset(token)
