from typing import Annotated, Literal, TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Specialist = Literal["log_analysis", "knowledge", "threat_intel"]
Severity = Literal["info", "low", "medium", "high", "critical"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Plan(Contract):
    tasks: list[Specialist] = Field(min_length=1, max_length=3)
    objective: str = Field(max_length=300)


class Finding(Contract):
    claim: str = Field(min_length=1, max_length=1600)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=8)
    severity: Severity = "info"
    confidence: Literal["low", "medium", "high"] = "low"


class Findings(Contract):
    findings: list[Finding] = Field(default_factory=list, max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=8)


ToolName = Literal[
    "security_events",
    "ip_lookup",
    "event_correlation",
    "historical_incidents",
    "security_documents",
    "threat_intelligence",
    "severity_assessment",
    "mitre_lookup",
]


class ToolRequest(Contract):
    name: ToolName
    query: str = Field(default="", max_length=500)
    mode: Literal["semantic", "keyword", "hybrid"] = "hybrid"
    document_ids: list[UUID] | None = Field(default=None, max_length=10)


class AgentAction(Contract):
    action: Literal["tool", "finish"]
    tool: ToolRequest | None = None

    @model_validator(mode="after")
    def require_tool(self):
        if self.action == "tool" and self.tool is None:
            raise ValueError("Tool action requires an allowlisted tool")
        return self


class Critique(Contract):
    accepted: bool
    retry_tasks: list[Specialist] = Field(default_factory=list, max_length=3)
    issues: list[str] = Field(default_factory=list, max_length=12)


class Recommendation(Contract):
    action: str = Field(min_length=1, max_length=600)
    category: Literal["immediate", "short_term", "preventive"]
    priority: Literal["high", "medium", "low"]
    evidence_ids: list[UUID] = Field(min_length=1, max_length=8)
    risks: str = Field(max_length=600)
    prerequisites: str = Field(max_length=600)
    requires_approval: Literal[True] = True


class Recommendations(Contract):
    recommendations: list[Recommendation] = Field(default_factory=list, max_length=6)


class ReviewInput(Contract):
    decision: Literal["approve", "reject"]
    comment: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def reject_comment(self):
        if self.decision == "reject" and not self.comment.strip():
            raise ValueError("Rejection requires a comment")
        return self


class Report(Contract):
    schema_version: Literal["1.0"] = "1.0"
    provider: str
    model: str
    executive_summary: list[Finding]
    incident_classification: str
    severity: Severity
    confidence: Literal["low", "medium", "high"]
    findings: dict[str, Findings]
    timeline: list[dict]
    affected_resources: list[str]
    indicators: list[dict]
    evidence_ids: list[UUID]
    mitre_mappings: list[dict]
    recommendations: list[Recommendation]
    limitations: list[str]
    completeness: Literal["complete", "incomplete"]
    approval_status: Literal["pending", "approved", "rejected"] = "pending"
    review: dict | None = None


def merge_maps(left: dict, right: dict) -> dict:
    return {**left, **right}


def union_ids(left: list[str], right: list[str]) -> list[str]:
    return sorted(set(left) | set(right))


class InvestigationState(TypedDict, total=False):
    investigation_id: str
    organization_id: str
    alert_id: str
    incident: dict
    plan: dict
    findings: Annotated[dict[str, dict], merge_maps]
    agent_status: Annotated[dict[str, str], merge_maps]
    evidence_ids: Annotated[list[str], union_ids]
    retrieved_documents: Annotated[list[str], union_ids]
    agent_run_ids: Annotated[list[str], union_ids]
    critic_feedback: dict
    reflection_count: int
    recommendations: list[dict]
    approval_status: str
    errors: Annotated[dict[str, str], merge_maps]
    usage: Annotated[dict[str, dict], merge_maps]
    final_report: dict
    review_decision: dict
