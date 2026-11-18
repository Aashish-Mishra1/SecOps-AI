"""Measured execution records and atomic budgets shared by concurrent graph branches."""

import json
import logging
from time import monotonic, perf_counter
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import update

from app.ai.agents import AgentLLM, InvalidCompletion
from app.ai.prompts import PROMPT_VERSION
from app.config import get_settings
from app.db import session_factory
from app.errors import AppError
from app.models import AgentRun, Investigation, LLMCall, utcnow


def consume(investigation_id: UUID, kind: str) -> None:
    settings = get_settings()
    field = Investigation.tool_count if kind == "tool" else Investigation.llm_count
    limit = settings.max_tool_calls if kind == "tool" else settings.max_llm_calls
    with session_factory()() as db:
        row = db.execute(
            update(Investigation)
            .where(Investigation.id == investigation_id, field < limit)
            .values({field.key: field + 1})
            .returning(Investigation.id)
        ).scalar_one_or_none()
        db.commit()
    if row is None:
        raise AppError(409, "execution_limit", f"Investigation {kind} budget exhausted")


def cost(input_tokens: int | None, output_tokens: int | None, provider: str) -> float | None:
    settings = get_settings()
    prices = (settings.llm_input_usd_per_million, settings.llm_output_usd_per_million)
    if provider == "mock" or input_tokens is None or output_tokens is None or None in prices:
        return None
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000


class Execution:
    def __init__(self, investigation_id: UUID, agent: str, attempt: int, llm: AgentLLM, deadline: float):
        self.investigation_id, self.agent, self.attempt = investigation_id, agent, attempt
        self.llm, self.deadline = llm, deadline
        self.id = uuid4()
        self.started = perf_counter()

    def check(self) -> None:
        if monotonic() >= self.deadline:
            raise AppError(408, "execution_timeout", "Investigation execution deadline reached")

    def __enter__(self):
        self.check()
        with session_factory()() as db:
            db.add(
                AgentRun(
                    id=self.id,
                    investigation_id=self.investigation_id,
                    agent=self.agent,
                    attempt=self.attempt,
                    status="running",
                    started_at=utcnow(),
                    model=self.llm.model,
                    trace_id=str(self.investigation_id),
                    prompt_version=PROMPT_VERSION,
                )
            )
            db.commit()
        return self

    def finish(self, result: dict | None, error_code: str | None = None) -> None:
        with session_factory()() as db:
            run = db.get(AgentRun, self.id)
            run.status = "failed" if error_code else "completed"
            run.ended_at = utcnow()
            run.latency_ms = (perf_counter() - self.started) * 1000
            run.error_code, run.result = error_code, result
            db.commit()
        logging.getLogger("secops").info(
            "agent_completed",
            extra={
                "investigation_id": str(self.investigation_id),
                "agent": self.agent,
                "status": run.status,
                "duration_ms": run.latency_ms,
                "trace_id": str(self.investigation_id),
            },
        )

    def __exit__(self, kind, error, traceback):
        if error is not None:
            self.finish(None, error.code if isinstance(error, AppError) else "agent_error")

    def generate(self, operation: str, payload: dict, schema: type[BaseModel]) -> BaseModel:
        settings = get_settings()
        if len(json.dumps(payload)) > settings.llm_max_input_chars:
            raise AppError(422, "prompt_limit", "Bounded prompt context exceeded")
        for retry in range(settings.llm_max_retries + 1):
            self.check()
            consume(self.investigation_id, "llm")
            started = perf_counter()
            completion, failure = None, None
            call_id = uuid4()
            with session_factory()() as db:
                db.add(
                    LLMCall(
                        id=call_id,
                        investigation_id=self.investigation_id,
                        agent_run_id=self.id,
                        provider=self.llm.provider,
                        model=self.llm.model,
                        status="running",
                    )
                )
                db.commit()
            try:
                completion = self.llm.generate(operation, payload, schema)
                self.check()
            except InvalidCompletion as exc:
                completion, failure = exc.completion, "invalid_model_output"
            except Exception as exc:
                failure = exc.code if isinstance(exc, AppError) else "provider_error"
            elapsed = (perf_counter() - started) * 1000
            with session_factory()() as db:
                call = db.get(LLMCall, call_id)
                call.status, call.error_code, call.latency_ms = (
                    ("failed" if failure else "completed"),
                    failure,
                    elapsed,
                )
                if completion and self.llm.provider != "mock":
                    call.input_tokens, call.output_tokens = completion.input_tokens, completion.output_tokens
                    call.total_tokens = completion.total_tokens
                    call.cost_usd = cost(call.input_tokens, call.output_tokens, self.llm.provider)
                db.commit()
            if failure is None:
                return completion.value
            if retry == settings.llm_max_retries or failure == "execution_timeout":
                raise AppError(502, failure, "Agent provider failed; no fallback was used")
        raise AssertionError("Unreachable retry state")
