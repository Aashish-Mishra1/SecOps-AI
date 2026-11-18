from time import monotonic
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import select

from app.ai.agents import AgentLLM
from app.config import get_settings
from app.db import session_factory
from app.errors import AppError
from app.graph.contracts import (
    AgentAction,
    Critique,
    Findings,
    InvestigationState,
    Plan,
    Recommendations,
    Report,
    ToolRequest,
)
from app.models import Alert, Evidence, Investigation, Review
from app.services.evidence_validation import valid_ids, validate_finding
from app.services.execution import Execution
from app.services.investigation_tools import InvestigationTools, evidence_facts, safe_text

SPECIALISTS = ["log_analysis", "knowledge", "threat_intel"]


class Nodes:
    def __init__(self, llm: AgentLLM, deadline: float):
        self.llm, self.deadline = llm, deadline

    def execution(self, state: InvestigationState, name: str) -> Execution:
        return Execution(
            UUID(state["investigation_id"]), name, state.get("reflection_count", 0), self.llm, self.deadline
        )

    def facts(self, state: InvestigationState, ids: list[str] | None = None) -> list[dict]:
        facts = evidence_facts(UUID(state["investigation_id"]), UUID(state["organization_id"]), ids)
        # Budget evidence context deterministically; aggregate and policy evidence precede individual events.
        order = {"correlation": 0, "document": 1, "indicator": 2, "mitre": 3, "history": 4, "event": 5}
        return sorted(facts, key=lambda f: (order.get(f["kind"], 9), f["id"]))[:12]

    def result(self, run: Execution, update: dict) -> dict:
        run.check()
        run.finish(update)
        return {
            **update,
            "agent_run_ids": [str(run.id)],
            "agent_status": {run.agent: "completed"},
            "usage": {str(run.id): {"agent": run.agent, "provider": run.llm.provider}},
        }

    def supervisor(self, state: InvestigationState) -> dict:
        with self.execution(state, "supervisor") as run:
            feedback = state.get("critic_feedback", {})
            requested = feedback.get("retry_tasks", []) if state.get("reflection_count", 0) else []
            plan = run.generate("supervisor", {"incident": state["incident"], "retry_tasks": requested}, Plan)
            # Required evidence collection cannot be omitted by a model.
            plan.tasks = list(dict.fromkeys(requested or SPECIALISTS))
            return self.result(run, {"plan": plan.model_dump(mode="json")})

    def specialist(self, state: InvestigationState, agent: str) -> dict:
        try:
            with self.execution(state, agent) as run:
                tools = InvestigationTools(run, UUID(state["organization_id"]))
                ids, docs, limits = [], [], []
                if agent == "log_analysis":
                    allowed = ["event_correlation", "security_events", "ip_lookup", "historical_incidents"]
                    called, observations = [], []
                    for _ in range(get_settings().agent_max_steps):
                        action = run.generate(
                            "log_action",
                            {
                                "incident": state["incident"],
                                "allowed_tools": allowed,
                                "called": called,
                                "observations": observations,
                            },
                            AgentAction,
                        )
                        if action.action == "finish":
                            break
                        result = tools.run(action.tool, set(allowed))
                        called.append(action.tool.name)
                        ids.extend(f.id for f in result.facts)
                        observations.append(
                            {
                                "tool": action.tool.name,
                                "facts": [f.model_dump() for f in result.facts[:2]],
                                "data": result.data,
                            }
                        )
                    # A model cannot end a log investigation without collecting scoped aggregate evidence.
                    if "event_correlation" not in called:
                        result = tools.run(ToolRequest(name="event_correlation"), set(allowed))
                        ids.extend(f.id for f in result.facts)
                elif agent == "knowledge":
                    query = "authentication MFA session review"
                    with session_factory()() as db:
                        alert = db.get(Alert, UUID(state["alert_id"]))
                        query = safe_text(alert.title, 300)
                    result = tools.run(
                        ToolRequest(name="security_documents", query=query), {"security_documents"}
                    )
                    if not result.facts:
                        action = run.generate(
                            "knowledge_query",
                            {"query": query, "incident": state["incident"], "previous_result_count": 0},
                            AgentAction,
                        )
                        if action.action == "tool":
                            result = tools.run(action.tool, {"security_documents"})
                    ids.extend(f.id for f in result.facts)
                    docs.extend(result.data.get("document_ids", []))
                    if not result.facts:
                        limits.append("No relevant ready documents in the configured embedding index.")
                else:
                    for name in ("threat_intelligence", "mitre_lookup"):
                        result = tools.run(ToolRequest(name=name), {"threat_intelligence", "mitre_lookup"})
                        ids.extend(f.id for f in result.facts)
                        if result.data.get("limitation"):
                            limits.append(result.data["limitation"])
                facts = self.facts(state, ids)
                findings = run.generate("findings", {"agent": agent, "evidence": facts}, Findings)
                findings.limitations = (findings.limitations + limits)[:8]
                return self.result(
                    run,
                    {
                        "findings": {agent: findings.model_dump(mode="json")},
                        "evidence_ids": sorted(set(ids)),
                        "retrieved_documents": sorted(set(docs)),
                        "errors": {agent: ""},
                    },
                )
        except Exception as exc:
            code = exc.code if isinstance(exc, AppError) else "agent_error"
            return {
                "findings": {agent: Findings(limitations=[f"Specialist failed: {code}"]).model_dump()},
                "agent_status": {agent: "failed"},
                "errors": {agent: code},
            }

    def log_analysis(self, state):
        return self.specialist(state, "log_analysis")

    def knowledge(self, state):
        return self.specialist(state, "knowledge")

    def threat_intel(self, state):
        return self.specialist(state, "threat_intel")

    def remediation(self, state: InvestigationState) -> dict:
        with self.execution(state, "remediation") as run:
            proposals = run.generate("remediation", {"evidence": self.facts(state)}, Recommendations)
            return self.result(run, {"recommendations": proposals.model_dump(mode="json")["recommendations"]})

    def critic(self, state: InvestigationState) -> dict:
        with self.execution(state, "critic") as run:
            issues, retry_tasks = [], []
            for agent in SPECIALISTS:
                findings = Findings.model_validate(state.get("findings", {}).get(agent, {}))
                if not findings.findings or state.get("errors", {}).get(agent):
                    issues.append(f"{agent}: missing evidence or failed execution")
                    retry_tasks.append(agent)
                for finding in findings.findings:
                    if not validate_finding(
                        UUID(state["investigation_id"]), UUID(state["organization_id"]), finding
                    ):
                        issues.append(f"{agent}: unsupported or invalid evidence")
                        retry_tasks.append(agent)
            for rec in Recommendations(recommendations=state.get("recommendations", [])).recommendations:
                if not valid_ids(
                    UUID(state["investigation_id"]), UUID(state["organization_id"]), rec.evidence_ids
                ):
                    issues.append("remediation: invalid citations")
                    retry_tasks.extend(SPECIALISTS)
            feedback = run.generate(
                "critic",
                {
                    "findings": state.get("findings", {}),
                    "evidence": self.facts(state),
                    "validation_issues": list(dict.fromkeys(issues))[:12],
                    "retry_tasks": sorted(set(retry_tasks)),
                },
                Critique,
            )
            # Deterministic failures cannot be waived by a model.
            feedback.accepted = feedback.accepted and not issues
            feedback.retry_tasks = sorted(set(feedback.retry_tasks + retry_tasks))
            feedback.issues = list(dict.fromkeys(issues + feedback.issues))[:12]
            reflection = state.get("reflection_count", 0)
            if not feedback.accepted and reflection < get_settings().max_reflection_cycles:
                feedback.retry_tasks = feedback.retry_tasks or SPECIALISTS
                reflection += 1
            else:
                feedback.retry_tasks = []
            with session_factory()() as db:
                inv = db.get(Investigation, UUID(state["investigation_id"]))
                inv.attempt = reflection
                db.commit()
            return self.result(
                run, {"critic_feedback": feedback.model_dump(), "reflection_count": reflection}
            )

    def reporting(self, state: InvestigationState) -> dict:
        with self.execution(state, "reporting") as run:
            inv_id, org = UUID(state["investigation_id"]), UUID(state["organization_id"])
            clean, limitations = (
                {},
                [
                    "No remediation was executed. Human review is required.",
                    "Rule-based severity and tentative ATT&CK mappings are not confirmation of compromise.",
                ],
            )
            for agent, payload in state.get("findings", {}).items():
                parsed = Findings.model_validate(payload)
                filtered = [f for f in parsed.findings if validate_finding(inv_id, org, f)]
                if len(filtered) != len(parsed.findings):
                    limitations.append(f"Unsupported {agent} findings were excluded.")
                clean[agent] = Findings(findings=filtered, limitations=parsed.limitations)
                limitations.extend(parsed.limitations)
            summaries = run.generate("reporting", {"evidence": self.facts(state)}, Findings)
            summary = [f for f in summaries.findings if validate_finding(inv_id, org, f)]
            recommendations = [
                r
                for r in Recommendations(recommendations=state.get("recommendations", [])).recommendations
                if valid_ids(inv_id, org, r.evidence_ids)
            ]
            with session_factory()() as db:
                inv = db.get(Investigation, inv_id)
                alert = db.get(Alert, inv.alert_id)
                evidence = list(
                    db.scalars(
                        select(Evidence).where(
                            Evidence.investigation_id == inv_id, Evidence.organization_id == org
                        )
                    )
                )
                correlation = next((e for e in evidence if e.kind == "correlation"), None)
                kind = correlation.metadata_json["classification"] if correlation else "insufficient_evidence"
                severity = correlation.metadata_json["severity"] if correlation else "info"
                used = {eid for values in clean.values() for f in values.findings for eid in f.evidence_ids}
                used.update(eid for f in summary for eid in f.evidence_ids)
                used.update(eid for r in recommendations for eid in r.evidence_ids)
                # Report timeline and intelligence are evidence-backed snapshots, not model inventions.
                selected = [e for e in evidence if valid_ids(inv_id, org, [e.id])]
                used.update(
                    e.id for e in selected if e.kind in {"event", "indicator", "mitre", "correlation"}
                )
                feedback = Critique.model_validate(state["critic_feedback"])
                limitations.extend(feedback.issues)
                report = Report(
                    provider=self.llm.provider,
                    model=self.llm.model,
                    executive_summary=summary,
                    incident_classification=kind,
                    severity=severity,
                    confidence="low" if not feedback.accepted else "medium",
                    findings=clean,
                    timeline=sorted(
                        [
                            {
                                "timestamp": e.metadata_json["timestamp"],
                                "event": e.excerpt,
                                "evidence_id": str(e.id),
                            }
                            for e in selected
                            if e.kind == "event"
                        ],
                        key=lambda e: e["timestamp"],
                    ),
                    affected_resources=[safe_text(v, 200) for v in (alert.username, alert.source_ip) if v],
                    indicators=[
                        {**e.metadata_json, "evidence_id": str(e.id)}
                        for e in selected
                        if e.kind == "indicator"
                    ],
                    mitre_mappings=[
                        {**e.metadata_json, "source_event_ids": None, "evidence_id": str(e.id)}
                        for e in selected
                        if e.kind == "mitre"
                    ],
                    evidence_ids=sorted(used, key=str),
                    recommendations=recommendations,
                    limitations=list(dict.fromkeys(limitations)),
                    completeness="complete" if feedback.accepted else "incomplete",
                )
                inv.report = report.model_dump(mode="json")
                db.commit()
            return self.result(
                run,
                {
                    "final_report": {
                        "investigation_id": str(inv_id),
                        "schema_version": "1.0",
                        "completeness": report.completeness,
                    },
                    "approval_status": "pending",
                },
            )

    def human_review(self, state: InvestigationState) -> dict:
        # No writes before interrupt: this node is replayed on Command(resume=...).
        decision = interrupt(
            {
                "investigation_id": state["investigation_id"],
                "report_available": True,
                "requires_reviewer": True,
                "approval_does_not_execute_actions": True,
            }
        )
        with session_factory()() as db:
            review = db.scalar(
                select(Review).where(Review.investigation_id == UUID(state["investigation_id"]))
            )
            if review is None or str(review.id) != decision.get("review_id"):
                raise AppError(403, "review_required", "A persisted authorized review is required")
            return {
                "review_decision": {"review_id": str(review.id), "decision": review.decision},
                "approval_status": "approved" if review.decision == "approve" else "rejected",
            }

    def finalize(self, state: InvestigationState) -> dict:
        with self.execution(state, "finalize") as run:
            with session_factory()() as db:
                inv = db.get(Investigation, UUID(state["investigation_id"]))
                review = db.scalar(select(Review).where(Review.investigation_id == inv.id))
                if review is None:
                    raise AppError(409, "review_required", "Review is missing")
                report = Report.model_validate(inv.report)
                report.approval_status = state["approval_status"]
                report.review = {
                    "decision": review.decision,
                    "comment": review.comment,
                    "reviewed_at": review.reviewed_at.isoformat(),
                }
                inv.report = report.model_dump(mode="json")
                db.commit()
            return self.result(run, {"approval_status": state["approval_status"]})


def build_graph(saver, llm: AgentLLM, deadline: float | None = None):
    nodes = Nodes(llm, deadline or monotonic() + get_settings().investigation_timeout_seconds)
    graph = StateGraph(InvestigationState)
    for name in [
        "supervisor",
        *SPECIALISTS,
        "remediation",
        "critic",
        "reporting",
        "human_review",
        "finalize",
    ]:
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", lambda s: s["plan"]["tasks"], SPECIALISTS)
    # Same-superstep specialist updates merge, then the common successor runs once.
    for specialist in SPECIALISTS:
        graph.add_edge(specialist, "remediation")
    graph.add_edge("remediation", "critic")
    graph.add_conditional_edges(
        "critic",
        lambda s: "supervisor" if s["critic_feedback"]["retry_tasks"] else "reporting",
        ["supervisor", "reporting"],
    )
    graph.add_edge("reporting", "human_review")
    graph.add_edge("human_review", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=saver, name="secops-investigation-v1")
