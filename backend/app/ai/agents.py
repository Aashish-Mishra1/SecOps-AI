import json
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from app.ai.prompts import PROMPTS
from app.ai.providers import GeminiProvider
from app.config import get_settings


@dataclass
class Completion:
    value: BaseModel | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class AgentLLM(Protocol):
    provider: str
    model: str

    def generate(self, operation: str, payload: dict, schema: type[BaseModel]) -> Completion: ...


class MockAgentLLM:
    provider = "mock"
    model = "mock:agents-v1"

    def generate(self, operation: str, payload: dict, schema: type[BaseModel]) -> Completion:
        if operation == "supervisor":
            value = {
                "tasks": payload.get("retry_tasks") or ["log_analysis", "knowledge", "threat_intel"],
                "objective": "Collect scoped evidence and prepare recommendations for human review.",
            }
        elif operation == "log_action":
            called = payload.get("called", [])
            next_tool = next((name for name in payload["allowed_tools"] if name not in called), None)
            value = {"action": "tool", "tool": {"name": next_tool}} if next_tool else {"action": "finish"}
        elif operation == "knowledge_query":
            value = {
                "action": "tool",
                "tool": {
                    "name": "security_documents",
                    "query": "authentication MFA session review",
                    "mode": "hybrid",
                },
            }
        elif operation in {"findings", "reporting"}:
            evidence = payload.get("evidence", [])
            value = {
                "findings": [
                    {
                        "claim": fact["statement"],
                        "evidence_ids": [fact["id"]],
                        "severity": fact.get("severity", "info"),
                        "confidence": "medium",
                    }
                    for fact in evidence[:8]
                ],
                "limitations": [] if evidence else ["No supporting evidence was available."],
            }
        elif operation == "critic":
            issues = payload.get("validation_issues", [])
            value = {"accepted": not issues, "retry_tasks": payload.get("retry_tasks", []), "issues": issues}
        elif operation == "remediation":
            ids = [fact["id"] for fact in payload.get("evidence", [])][:3]
            value = {
                "recommendations": []
                if not ids
                else [
                    {
                        "action": action,
                        "category": category,
                        "priority": priority,
                        "evidence_ids": ids,
                        "risks": risks,
                        "prerequisites": prerequisites,
                        "requires_approval": True,
                    }
                    for action, category, priority, risks, prerequisites in (
                        (
                            "Preserve relevant logs and verify the affected account or resource owner.",
                            "immediate",
                            "high",
                            "Evidence handling may expose sensitive records.",
                            "Authorized analyst and approved evidence storage.",
                        ),
                        (
                            "Review active sessions and consider revocation only after confirming suspicious activity.",
                            "short_term",
                            "medium",
                            "Revocation can interrupt legitimate work.",
                            "Reviewer approval and verified scope before any external action.",
                        ),
                        (
                            "Review MFA, least privilege and detection coverage for the affected resources.",
                            "preventive",
                            "low",
                            "Policy changes can affect legitimate access.",
                            "Change approval, owner consultation and rollback plan.",
                        ),
                    )
                ]
            }
        else:
            raise ValueError("Unknown prompt operation")
        return Completion(schema.model_validate(value))


class GeminiAgentLLM:
    provider = "gemini"

    def __init__(self):
        self.settings = get_settings()
        self.adapter = GeminiProvider(self.settings)
        self.model = self.settings.gemini_model

    def generate(self, operation: str, payload: dict, schema: type[BaseModel]) -> Completion:
        from google.genai import types

        with self.adapter.client() as client:
            response = client.models.generate_content(
                model=self.model,
                contents=json.dumps(payload),
                config=types.GenerateContentConfig(
                    system_instruction=PROMPTS[operation],
                    response_mime_type="application/json",
                    response_schema=schema,
                    max_output_tokens=self.settings.llm_max_output_tokens,
                    temperature=0,
                ),
            )
        usage = response.usage_metadata
        # Parse after saving usage at the caller even when schema validation fails.
        completion = Completion(
            value=None,
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            total_tokens=getattr(usage, "total_token_count", None),
        )
        try:
            completion.value = schema.model_validate_json(response.text or "")
        except ValueError:
            raise InvalidCompletion(completion) from None
        return completion


class InvalidCompletion(ValueError):
    def __init__(self, completion: Completion):
        self.completion = completion
        super().__init__("Provider returned an invalid structured response")


def get_agent_llm() -> AgentLLM:
    return MockAgentLLM() if get_settings().ai_provider == "mock" else GeminiAgentLLM()
