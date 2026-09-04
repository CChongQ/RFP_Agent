import json
from pathlib import Path

from app.schemas import AnalysisResult

"""For dev evalaution only. Generate a compact, human-readable JSON result for inspection, debugging, and evaluation comparison. 
"""
EXPORT_SCHEMA_VERSION = "1.0"


class AnalysisResultExportError(RuntimeError):
    """Raised when a completed analysis cannot be exported."""


def export_analysis_result(result: AnalysisResult, output_directory: Path) -> Path:
    """Save a compact, human-readable comparison artifact."""

    destination = Path(output_directory) / f"{result.analysis_id}.json"
    temporary = destination.with_suffix(".json.tmp")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(_comparison_payload(result), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    except (OSError, TypeError, ValueError) as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise AnalysisResultExportError("completed analysis export failed") from exc
    return destination


def _comparison_payload(result: AnalysisResult) -> dict[str, object]:
    decisions = {item.requirement_id: item for item in result.decisions}
    comparison_items: list[dict[str, object]] = []

    for requirement in result.requirements:
        decision = decisions[requirement.requirement_id]
        item: dict[str, object] = {
            "requirement_id": requirement.requirement_id,
            "requirement_type": requirement.requirement_type.value,
            "requirement_text": requirement.requirement_text,
            "normalized_requirement": requirement.normalized_requirement,
            "source_page": requirement.source_page,
            "source_block_ids": [
                reference.block_id for reference in requirement.source_references
            ],
            "extraction_requires_human_review": requirement.requires_human_review,
            "decision_status": decision.status.value,
            "evidence_ids": decision.evidence_ids,
            "decision_reason": decision.reason,
        }
        if requirement.rules:
            item["rules"] = [rule.model_dump(mode="json") for rule in requirement.rules]
        if decision.rule_result is not None:
            item["rule_result"] = decision.rule_result.model_dump(mode="json")
        comparison_items.append(item)

    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "analysis_id": result.analysis_id,
        "tender_id": result.tender_id,
        "document_sha256": result.trace.document_sha256,
        "model_version": result.trace.model_version,
        "prompt_version": result.trace.prompt_version,
        "overall_recommendation": result.overall_recommendation.value,
        "run_metrics": {
            "latency_ms": result.trace.latency_ms,
            "input_tokens": result.trace.input_tokens,
            "output_tokens": result.trace.output_tokens,
            "estimated_cost_usd": result.trace.estimated_cost_usd,
        },
        "results": comparison_items,
    }
