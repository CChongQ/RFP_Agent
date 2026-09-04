"""Test configured service wiring without external calls."""

from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services import configured_analysis
from app.services.configured_analysis import ConfiguredAnalysisRunner
from app.services.rule_evaluator import DeterministicRuleEvaluator


def test_configured_runner_includes_deterministic_rule_evaluator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirm the real runner passes the generic evaluator to DecisionService."""

    session = Mock(spec=Session)
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://test:test@localhost/test",
        openai_api_key="test-key",
        openai_model="mock-model",
        openai_embedding_model="mock-embedding-model",
        enable_external_api_calls=True,
    )
    decision_service_factory = Mock()
    monkeypatch.setattr(configured_analysis, "OpenAI", Mock(return_value=Mock()))
    monkeypatch.setattr(
        configured_analysis,
        "DecisionService",
        decision_service_factory,
    )

    ConfiguredAnalysisRunner(session, settings)._build_components()

    evaluator = decision_service_factory.call_args.kwargs["rule_evaluator"]
    assert isinstance(evaluator, DeterministicRuleEvaluator)
