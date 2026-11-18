from sqlalchemy import select

from app.models import AgentRun, Investigation, LLMCall, ToolRun


def measured_sum(rows: list, field: str):
    values = [getattr(row, field) for row in rows]
    return sum(values) if values and all(value is not None for value in values) else None


def investigation_metrics(db, investigation: Investigation) -> dict:
    calls = list(db.scalars(select(LLMCall).where(LLMCall.investigation_id == investigation.id)))
    agents = list(
        db.scalars(
            select(AgentRun)
            .where(AgentRun.investigation_id == investigation.id)
            .order_by(AgentRun.started_at, AgentRun.id)
        )
    )
    tools = list(db.scalars(select(ToolRun).where(ToolRun.investigation_id == investigation.id)))
    live = [call for call in calls if call.provider != "mock"]
    outputs = measured_sum(live, "output_tokens")
    duration = measured_sum(live, "latency_ms")
    elapsed = (
        (investigation.ended_at - investigation.started_at).total_seconds() * 1000
        if investigation.started_at and investigation.ended_at
        else None
    )
    return {
        "provider": investigation.provider,
        "model": investigation.model,
        "input_tokens": measured_sum(live, "input_tokens"),
        "output_tokens": outputs,
        "total_tokens": measured_sum(live, "total_tokens"),
        "cost_usd": measured_sum(live, "cost_usd"),
        "tokens_per_second": outputs / (duration / 1000) if outputs is not None and duration else None,
        "throughput_definition": "output tokens / non-streaming provider request duration; excludes mock calls",
        "time_to_first_token_ms": None,
        "active_execution_ms": investigation.active_ms,
        "end_to_end_ms": elapsed,
        "end_to_end_includes_human_wait": True,
        "tool_invocations": len(tools),
        "tool_failures": sum(t.status in {"failed", "interrupted"} for t in tools),
        "llm_invocations": len(calls),
        "reflection_iterations": investigation.attempt,
        "embedding_input_tokens": measured_sum(
            [t for t in tools if t.tool == "security_documents"], "input_tokens"
        ),
        "agents": [
            {
                "id": str(a.id),
                "agent": a.agent,
                "attempt": a.attempt,
                "status": a.status,
                "latency_ms": a.latency_ms,
                "error_code": a.error_code,
            }
            for a in agents
        ],
        "calls": [
            {
                "id": str(c.id),
                "agent_run_id": str(c.agent_run_id),
                "provider": c.provider,
                "status": c.status,
                "input_tokens": c.input_tokens,
                "output_tokens": c.output_tokens,
                "total_tokens": c.total_tokens,
                "cost_usd": c.cost_usd,
                "latency_ms": c.latency_ms,
                "time_to_first_token_ms": c.time_to_first_token_ms,
            }
            for c in calls
        ],
    }
