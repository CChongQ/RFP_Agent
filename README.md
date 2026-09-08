# Evidence-First RFP Qualification Agent

An **evidence-first service** that helps proposal teams **review tenders** before investing significant time in a bid.

## The Problem We’re Trying to Solve

RFPs can span **dozens or hundreds of pages**, with important requirements **scattered** across technical sections, appendices, and legal terms. Their language is often **nuanced**: mandatory conditions may be implied, exceptions may appear elsewhere, and the same concept may be described using different terminology.

Before preparing a response, a proposal team needs to understand for example:

- Which requirements are mandatory?
- What company evidence supports each requirement?
- Which requirements are unmet, ambiguous, or risky?
- Is the opportunity worth bidding on?

Manual review is slow and inconsistent. This project uses an LLM and deterministic checks to help organize the requirements, connect them with stored company evidence, and highlight uncertain conclusions for human review.

## Why AI, Retrieval, and Deterministic Rules?

I designed the system so that each part has a limited responsibility:

| Part | Responsibility |
|---|---|
| LLM | Extract requirements, propose supported rule candidates, and assess qualitative evidence |
| Evidence retrieval | Find relevant stored company evidence without sending the entire evidence store in every prompt |
| Deterministic rules | Calculate exact counts, thresholds, allowed values, and validity results |
| System policy | Validate model output, enforce safeguards, and produce the final recommendation |

Tender requirements can be written in many different ways, so keyword matching alone is not enough. The LLM interprets the wording and returns structured business concepts, but it does not query the database, generate SQL, or invent company evidence.

For example, if a tender requires at least 3 qualifying projects:

1. The LLM identifies a minimum project-count requirement.
2. PostgreSQL finds two matching project records.
3. Python compares `2` with the required minimum of `3`.
4. The result is sent to Human Review.

When a requirement cannot be traced or checked safely, the system requests human review rather than guessing.

## Current Version

The system accepts one tender PDF, extracts traceable requirements, compares them with stored company evidence, and returns a `Bid`, `No-Bid`, or `Human Review` recommendation.

Provide your own tender PDF and company evidence. Currently the system reads registered local files; it does not offer a PDF upload interface.

### Current Scope

| Supported | Current boundary |
|---|---|
| One accepted tender | `TENDER-001` only |
| One company | No multi-company or tenant separation |
| Text-based PDF extraction | No OCR or scanned-document processing |
| Semantic evidence retrieval | Evidence must be seeded before analysis |
| Five deterministic rule operators | No arbitrary or model-generated code |
| Synchronous analysis endpoint | A request can take several minutes |

## Provide Your Data

### Tender PDF

1. Create `data/tenders/raw/` and place your text-based PDF there.
2. Calculate its SHA-256 hash:

```powershell
Get-FileHash "data/tenders/raw/your-tender.pdf" -Algorithm SHA256
```

3. Create `data/tenders/manifest.csv` with this header and one row. Replace the title, source URL, filename, and hash with your values:

```csv
tender_id,title,notice_url,local_filename,sha256,selection_status
TENDER-001,Your tender title,https://example.com/your-tender,your-tender.pdf,PASTE_SHA256_HERE,accepted
```

**Use `TENDER-001`: it is the only ID the API currently accepts**. Keep one accepted row for this ID. The system reads the local PDF, not the URL.

### Company evidence

Create `data/company/raw/my-company.json`. Use this minimal structure and replace the example with your actual evidence:

```json
{
  "evidence": [
    {
      "evidence_id": "PROJECT-001",
      "evidence_type": "project",
      "supporting_text": "Describe a completed project and the work your company performed."
    }
  ]
}
```

- Every record needs a unique ID, a type, and supporting text or structured values.
- `evidence_type` need to be one of: `company_profile`, `project`, `certification`, `capability`, or `policy`.
- Optional fields: `structured_value`, `valid_from`, and `valid_until`. Dates use YYYY-MM-DD.
- Seeding loads these records into the database. It updates matching IDs and inserts new ones; it does not delete omitted records.

Relevant PDF text and company evidence are sent to the configured model during analysis.

## Processing Overview

| Step | What happens |
|---|---|
| Check the PDF | Verify the registered file, hash, and document limits. **Larger documents need user confirmation**. |
| Extract requirements | Read the PDF and identify requirements with source references. |
| Find evidence | Create missing evidence embeddings, retrieve relevant records, and run supported rule checks. |
| Make decisions | Assess each requirement and apply the recommendation policy. |
| Save results | Store decisions, supporting references, and run details; return the result and save a compact export. |


## Project Layout

```text
backend/app/
├── api/                 HTTP routes, dependencies, and error mapping
├── core/                Environment-backed settings
├── database/            SQLAlchemy models, engine, and sessions
├── prompts/             Versioned model instructions
├── schemas/             Pydantic API and domain contracts
├── services/            Extraction, retrieval, rules, decisions, and orchestration
└── main.py              FastAPI application

backend/tests/
├── unit/                Offline behavior tests
└── integration/         PostgreSQL and explicitly enabled API tests

alembic/                 Database migrations
data/                    User-provided tender and company inputs
scripts/                 Evidence seeding and developer commands
```


## Environment Setup

Requires Python 3.12, Docker with Docker Compose, and an OpenAI API key.

Run the PowerShell commands below from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock.txt
python -m pip install --no-deps --no-build-isolation -e .
```

Copy `.env.example` to `.env` for initial setup. Set:

| Setting | Value |
|---|---|
| `DATABASE_PASSWORD` | Your local database password |
| `DATABASE_URL` | Matching PostgreSQL connection URL |
| `OPENAI_API_KEY` | Your API key |
| `OPENAI_MODEL` | Model for extraction and assessment |
| `OPENAI_EMBEDDING_MODEL` | Model for evidence search |
| `ENABLE_EXTERNAL_API_CALLS` | `true` for real analysis |

Preserve existing settings if `.env` already exists. The next step temporarily sets the database URL for a fresh run.

## Run an Analysis

### 1. Prepare the database and start the API

Use a fresh database for each run. In Terminal A:

```powershell
docker compose up -d --wait db

# Match DATABASE_USER and DATABASE_PORT in .env if customized.
$runDbUser = "rfp_agent"
$runDbPort = 5432
$runDbName = "rfp_agent_run_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
docker compose exec -T db createdb -U $runDbUser $runDbName

$runDbPassword = Read-Host "Enter DATABASE_PASSWORD from .env"
$runEncodedPassword = [System.Uri]::EscapeDataString($runDbPassword)
$env:DATABASE_URL = "postgresql+psycopg://${runDbUser}:${runEncodedPassword}@localhost:${runDbPort}/${runDbName}"

python -m alembic upgrade head
python scripts/seed_company_data.py --path "data/company/raw/my-company.json"
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Stop at any failed command. Leave Terminal A running. **Please note enable real analysis will make paid model and embedding calls; there is no spending cap now**.

### 2. Precheck your PDF

Open Terminal B at the repository root:

```powershell
$body = @{ tender_id = "TENDER-001" } | ConvertTo-Json
$precheck = Invoke-RestMethod -Method Post `
    -Uri "http://127.0.0.1:8000/api/v1/analyses/precheck" `
    -ContentType "application/json" -Body $body
$precheck | Format-List
```

Verify the filename, hash, size, and page count. Precheck makes no model calls. Read any warnings before continuing.

### 3. Analyze and save the response

The default confirmation threshold is 50 pages. Confirmation does not bypass the hard document limits or truncate the PDF.

```powershell
$confirmed = $false
if ($precheck.requires_confirmation) {
    $answer = Read-Host "This PDF exceeds the page threshold. Continue? (y/N)"
    if ($answer -ne "y") { throw "Analysis cancelled" }
    $confirmed = $true
}
$body = @{
    tender_id = "TENDER-001"
    confirm_large_document = $confirmed
} | ConvertTo-Json
$result = Invoke-RestMethod -Method Post `
    -Uri "http://127.0.0.1:8000/api/v1/analyses" `
    -ContentType "application/json" -Body $body -TimeoutSec 1800

$result | Select-Object analysis_id, overall_recommendation
$responseDir = "data/evaluation/api_responses"
New-Item -ItemType Directory -Force -Path $responseDir | Out-Null
$responsePath = Join-Path $responseDir "$($result.analysis_id).json"
$result | ConvertTo-Json -Depth 50 | Set-Content -Path $responsePath -Encoding utf8
```

| Output | Location |
|---|---|
| Full response saved above | `data/evaluation/api_responses/<analysis_id>.json` |
| Automatic compact export | `data/evaluation/runs/<analysis_id>.json` |
| Stored run and decisions | The database created in Terminal A |

Keep the response and compact export in separate folders. Review requirement decisions, evidence references, risks, and Human Review reasons.

Requests can take several minutes.

After completion, stop the API and close Terminal A to clear its temporary database setting. You can also explore the endpoints in [the local API docs](http://127.0.0.1:8000/docs).

## Current Limitations

- Repeated analyses can overwrite requirement rows. Use a fresh database for each run and preserve exports.
- Human Review means a person needs to check unresolved evidence or interpretation. Failed proposed rules also require review.
- A supported mandatory rejection produces No-Bid; missing mandatory requirements or unresolved mandatory decisions otherwise require Human Review.
- Source references and evidence IDs help review; they do not prove the interpretation is correct.
- End-to-end execution has been demonstrated. Decision accuracy has not been measured.
- Cost is unknown unless prices and usage are available. Estimates exclude embeddings; timing covers the analysis stage, not the full request.
