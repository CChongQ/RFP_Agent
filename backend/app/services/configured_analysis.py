import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from openai import OpenAI
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.database.models import EvidenceRecord
from app.schemas import AnalysisResult, TenderDocument
from app.services.analysis_service import AnalysisService
from app.services.decision_service import (
    DecisionService,
    OpenAIEvidenceAssessmentClient,
    StoredEvidenceReader,
)
from app.services.evidence_retrieval import (
    OpenAIEmbeddingClient,
    embed_missing_evidence,
)
from app.services.model_usage import ModelUsageTracker
from app.services.requirement_extractor import OpenAIRequirementModelClient
from app.services.rule_evaluator import DeterministicRuleEvaluator
from app.services.rule_evidence import RuleEvidenceService

logger = logging.getLogger(__name__)


class AnalysisConfigurationError(RuntimeError):
    """when a real analysis is not safely configured"""


class CompanyEvidenceMissingError(RuntimeError):
    """when the company evidence store has not been seeded"""


class AnalysisDatabaseError(RuntimeError):
    """when analysis persistence cannot complete"""


class AnalysisRunner(Protocol):
    def run(self, tender: TenderDocument, pdf_path: Path) -> AnalysisResult: ...


@dataclass(frozen=True)
class ModelConfiguration:
    """Validated model values needed for one real analysis"""

    model: str
    embedding_model: str
    api_key: SecretStr


@dataclass(frozen=True)
class AnalysisComponents:
    """Services created together because they share one OpenAI client"""

    analysis_service: AnalysisService
    embedding_client: OpenAIEmbeddingClient
    embedding_model: str


class ConfiguredAnalysisRunner:
    """Wires the explicit analysis flow using request-scoped dependencies"""

    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def run(self, tender: TenderDocument, pdf_path: Path) -> AnalysisResult:
        try:
            self._require_company_evidence()
            
            components = self._build_components()
            
            # to be chnage, after MVP
            embed_missing_evidence(
                self._session,
                components.embedding_client,
                model=components.embedding_model,
                batch_size=self._settings.evidence_embedding_batch_size,
            )
            
            result = components.analysis_service.analyze(tender, pdf_path)
            
            # commit the result and its trace as one unit.
            self._session.commit()
            
            return result
        except CompanyEvidenceMissingError:
            raise
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise AnalysisDatabaseError("analysis database operation failed") from exc
        except Exception:
            logger.exception("Analysis failed for tender %s", tender.tender_id)
            self._commit_failed_trace()
            raise

    def _require_company_evidence(self) -> None:
        
        # check first to avoid calls with an empty evidence
        evidence_count = self._session.scalar(
            select(func.count()).select_from(EvidenceRecord)
        )
        
        if evidence_count:
            return
        self._session.rollback()
        raise CompanyEvidenceMissingError(
            "company evidence must be seeded before running an analysis"
        )

    def _read_model_configuration(self) -> ModelConfiguration:
        
        settings = self._settings
        model_setting = settings.openai_model
        embedding_model_setting = settings.openai_embedding_model
        api_key_setting = settings.openai_api_key
        
        if not settings.enable_external_api_calls:
            raise AnalysisConfigurationError("external API calls are disabled")
        if api_key_setting is None:
            raise AnalysisConfigurationError("OPENAI_API_KEY is required")
        if model_setting is None or not model_setting.strip():
            raise AnalysisConfigurationError("OPENAI_MODEL is required")
        if embedding_model_setting is None or not embedding_model_setting.strip():
            raise AnalysisConfigurationError("OPENAI_EMBEDDING_MODEL is required")

        return ModelConfiguration(
            model=model_setting.strip(),
            embedding_model=embedding_model_setting.strip(),
            api_key=api_key_setting,
        )

    def _build_components(self) -> AnalysisComponents:
        
        settings = self._settings
        model_config = self._read_model_configuration()
        
        # Share usage counts across extraction and decision calls
        usage_tracker = ModelUsageTracker()
        
        #note: use 1 client so timeout and retry rules stay the same
        client = OpenAI(
            api_key=model_config.api_key.get_secret_value(),
            max_retries=settings.openai_max_retries,
            timeout=settings.openai_timeout_seconds,
        )
        
        #prep clients 
        requirement_client = OpenAIRequirementModelClient(client, usage_tracker)
        embedding_client = OpenAIEmbeddingClient(client)
        evidence_reader = StoredEvidenceReader(
            self._session,
            embedding_client,
            embedding_model=model_config.embedding_model,
            min_score=settings.min_retrieval_score,
        )
        assessment_client = OpenAIEvidenceAssessmentClient(client, usage_tracker)
        # Exact rules query structured evidence independently of semantic search.
        rule_evaluator = DeterministicRuleEvaluator(
            RuleEvidenceService(self._session),
            as_of=date.today(),
        )
        
        #build decision service 
        decision_service = DecisionService(
            evidence_reader,
            assessment_client,
            model=model_config.model,
            rule_evaluator=rule_evaluator,
            top_k=settings.retrieval_top_k,
        )
        
        #build anlaysis service 
        analysis_service = AnalysisService(
            self._session,
            requirement_client,
            decision_service,
            model=model_config.model,
            usage_tracker=usage_tracker,
            max_pdf_mb=settings.max_pdf_mb,
            max_pdf_pages=settings.max_pdf_pages,
            max_chunk_characters=settings.max_requirement_chunk_characters,
        )
        
        return AnalysisComponents(
            analysis_service=analysis_service,
            embedding_client=embedding_client,
            embedding_model=model_config.embedding_model,
        )

    def _commit_failed_trace(self) -> None:
        try:
            self._session.commit()
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise AnalysisDatabaseError(
                "failed analysis trace could not be persisted"
            ) from exc
