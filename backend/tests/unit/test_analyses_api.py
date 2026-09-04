from pathlib import Path

from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_analysis_precheck_runner,
    get_analysis_runner,
    get_tender_catalog,
)
from app.main import create_app
from app.schemas import (
    AnalysisPrecheck,
    AnalysisResult,
    Decision,
    DecisionStatus,
    OverallRecommendation,
    Requirement,
    RequirementType,
    SourceReference,
    TenderDocument,
    TraceMetadata,
)
from app.services.configured_analysis import (
    AnalysisConfigurationError,
    CompanyEvidenceMissingError,
)
from app.services.requirement_extractor import RequirementExtractionError
from app.services.tender_catalog import TenderSourceMissingError

"""Test the HTTP layer for POST /api/v1/analyses using fake local services."""

TEST_SHA256 = "A" * 64
ANALYSES_URL = "/api/v1/analyses"
PRECHECK_URL = f"{ANALYSES_URL}/precheck"
VALID_ANALYSIS_REQUEST = {"tender_id": "TENDER-001"}


def _tender() -> TenderDocument:
    return TenderDocument(
        tender_id="TENDER-001",
        title="Synthetic Tender",
        source_url="https://example.com/tender",
        file_hash=TEST_SHA256,
        local_filename="tender.pdf",
    )


def _result() -> AnalysisResult:
    requirement = Requirement(
        requirement_id="TENDER-001-REQ-001",
        tender_id="TENDER-001",
        requirement_text="The bidder must provide implementation services",
        normalized_requirement="Provide implementation services",
        requirement_type=RequirementType.MANDATORY,
        source_page=1,
        source_excerpt="The bidder must provide implementation services",
        source_references=[
            SourceReference(
                block_id="P001-B001",
                page_number=1,
                bounding_box=(72.0, 72.0, 500.0, 100.0),
            )
        ],
    )
    return AnalysisResult(
        analysis_id="ANALYSIS-TEST-001",
        tender_id="TENDER-001",
        requirements=[requirement],
        decisions=[
            Decision(
                requirement_id=requirement.requirement_id,
                status=DecisionStatus.SATISFIED,
                evidence_ids=["PROJECT-TEST-001"],
                reason="Stored project evidence supports the requirement",
            )
        ],
        overall_recommendation=OverallRecommendation.BID,
        trace=TraceMetadata(
            document_sha256=TEST_SHA256,
            model_version="mock-model",
            prompt_version="test-v1",
            latency_ms=10,
            extracted_requirement_ids=[requirement.requirement_id],
        ),
    )


class FakeCatalog:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def get(self, tender_id: str) -> tuple[TenderDocument, Path]:
        assert tender_id == "TENDER-001"
        if self._error is not None:
            raise self._error
        return _tender(), Path("tender.pdf")


class FakeRunner:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.called = False

    def run(self, tender: TenderDocument, pdf_path: Path) -> AnalysisResult:
        self.called = True
        assert tender.tender_id == "TENDER-001"
        assert pdf_path == Path("tender.pdf")
        if self._error is not None:
            raise self._error
        return _result()


class FakePrecheckRunner:
    def __init__(self, *, requires_confirmation: bool = False) -> None:
        self.requires_confirmation = requires_confirmation
        self.called = False

    def inspect(
        self,
        tender: TenderDocument,
        pdf_path: Path,
    ) -> AnalysisPrecheck:
        self.called = True
        return AnalysisPrecheck(
            tender_id=tender.tender_id,
            filename=pdf_path.name,
            document_sha256=TEST_SHA256,
            file_size_bytes=1024,
            file_size_mb=0.0,
            page_count=51 if self.requires_confirmation else 10,
            requires_confirmation=self.requires_confirmation,
            warnings=(
                ["Document exceeds a confirmation threshold"]
                if self.requires_confirmation
                else []
            ),
        )


def _client(
    catalog: FakeCatalog,
    runner: FakeRunner,
    precheck_runner: FakePrecheckRunner | None = None,
) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_tender_catalog] = lambda: catalog
    app.dependency_overrides[get_analysis_runner] = lambda: runner
    app.dependency_overrides[get_analysis_precheck_runner] = lambda: (
        precheck_runner or FakePrecheckRunner()
    )
    return TestClient(app)


# Basic tests

def test_create_analysis_returns_valid_result() -> None:
    runner = FakeRunner()

    response = _client(FakeCatalog(), runner).post(
        ANALYSES_URL,
        json=VALID_ANALYSIS_REQUEST,
    )

    assert response.status_code == 200
    assert response.json()["analysis_id"] == "ANALYSIS-TEST-001"
    assert response.json()["overall_recommendation"] == "bid"
    assert runner.called


def test_precheck_returns_document_facts_without_running_analysis() -> None:
    runner = FakeRunner()
    precheck_runner = FakePrecheckRunner()

    response = _client(FakeCatalog(), runner, precheck_runner).post(
        PRECHECK_URL,
        json=VALID_ANALYSIS_REQUEST,
    )

    assert response.status_code == 200
    assert response.json()["page_count"] == 10
    assert precheck_runner.called
    assert not runner.called


def test_large_analysis_requires_explicit_confirmation() -> None:
    runner = FakeRunner()
    response = _client(
        FakeCatalog(),
        runner,
        FakePrecheckRunner(requires_confirmation=True),
    ).post(ANALYSES_URL, json=VALID_ANALYSIS_REQUEST)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "large_document_confirmation_required"
    )
    assert not runner.called


def test_large_analysis_runs_after_confirmation() -> None:
    runner = FakeRunner()
    response = _client(
        FakeCatalog(),
        runner,
        FakePrecheckRunner(requires_confirmation=True),
    ).post(
        ANALYSES_URL,
        json={"tender_id": "TENDER-001", "confirm_large_document": True},
    )

    assert response.status_code == 200
    assert runner.called


# Corner-case tests

def test_create_analysis_maps_missing_pdf_without_exposing_path() -> None:
    response = _client(
        FakeCatalog(TenderSourceMissingError("private/path/tender.pdf")),
        FakeRunner(),
    ).post(ANALYSES_URL, json=VALID_ANALYSIS_REQUEST)

    assert response.status_code == 404
    assert response.json() == {"detail": "The tender PDF is missing"}
    assert "private" not in response.text


def test_create_analysis_reports_unseeded_company_evidence() -> None:
    response = _client(
        FakeCatalog(),
        FakeRunner(CompanyEvidenceMissingError("internal database detail")),
    ).post(ANALYSES_URL, json=VALID_ANALYSIS_REQUEST)

    assert response.status_code == 409
    assert response.json() == {"detail": "Company evidence has not been seeded"}
    assert "internal" not in response.text


def test_create_analysis_reports_disabled_external_calls_safely() -> None:
    response = _client(
        FakeCatalog(),
        FakeRunner(AnalysisConfigurationError("OPENAI_API_KEY is required")),
    ).post(ANALYSES_URL, json=VALID_ANALYSIS_REQUEST)

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Analysis model configuration is unavailable"
    }
    assert "OPENAI_API_KEY" not in response.text


def test_create_analysis_maps_invalid_model_output_to_bad_gateway() -> None:
    response = _client(
        FakeCatalog(),
        FakeRunner(RequirementExtractionError("raw provider output")),
    ).post(ANALYSES_URL, json=VALID_ANALYSIS_REQUEST)

    assert response.status_code == 502
    assert response.json() == {
        "detail": "The analysis model could not produce a valid result"
    }
    assert "provider" not in response.text
