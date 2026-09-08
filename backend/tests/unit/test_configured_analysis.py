"""Test configured service wiring without external calls."""

from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services import configured_analysis
from app.services.configured_analysis import ConfiguredAnalysisRunner
from app.services.model_usage import ModelUsageSnapshot
from app.services.rule_evaluator import DeterministicRuleEvaluator


def test_configured_runner_wires_rule_evaluator_and_cost_rates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirm configured rules and prices reach the analysis services."""

    session = Mock(spec=Session)
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://test:test@localhost/test",
        openai_api_key="test-key",
        openai_model="mock-model",
        openai_embedding_model="mock-embedding-model",
        enable_external_api_calls=True,
        openai_input_cost_per_mil_tokens=2.0,
        openai_output_cost_per_mil_tokens=8.0,
    )
    decision_service_factory = Mock()
    analysis_service_factory = Mock()
    monkeypatch.setattr(configured_analysis, "AnalysisService", analysis_service_factory)
    monkeypatch.setattr(configured_analysis, "OpenAI", Mock(return_value=Mock()))
    monkeypatch.setattr(
        configured_analysis,
        "DecisionService",
        decision_service_factory,
    )

    ConfiguredAnalysisRunner(session, settings)._build_components()

    evaluator = decision_service_factory.call_args.kwargs["rule_evaluator"]
    assert isinstance(evaluator, DeterministicRuleEvaluator)
    tracker = analysis_service_factory.call_args.kwargs["usage_tracker"]
    assert tracker.estimate_cost(
        ModelUsageSnapshot(input_tokens=1000, output_tokens=100)
    ) == pytest.approx(0.0028)
