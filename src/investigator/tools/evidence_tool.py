"""get_evidence: look up evidence already collected in this investigation."""

from typing import Any

from pydantic import BaseModel, Field

from investigator.harness.tools import Tool, ToolContext, ToolOutput, tool


class GetEvidenceInput(BaseModel):
    evidence_ids: list[str] = Field(
        min_length=1,
        max_length=20,
        description="Evidence IDs exactly as returned by earlier tool calls.",
    )


def build_evidence_tools() -> list[Tool]:
    @tool(
        name="get_evidence",
        input_model=GetEvidenceInput,
        description=(
            "Fetch the stored details of evidence items by ID (any ID returned earlier in this investigation by "
            "any agent's tools). Call it to verify what a cited item actually shows before relying on it."
        ),
    )
    async def get_evidence(args: GetEvidenceInput, ctx: ToolContext) -> ToolOutput:
        found: list[dict[str, Any]] = []
        missing: list[str] = []
        for evidence_id in args.evidence_ids:
            item = ctx.evidence.get(evidence_id)
            if item is None:
                missing.append(evidence_id)
            else:
                found.append(item.model_dump(mode="json"))
        warnings = [f"Unknown evidence id(s): {', '.join(missing)}"] if missing else []
        return ToolOutput(data={"evidence": found, "missing": missing}, warnings=warnings)

    return [get_evidence]
