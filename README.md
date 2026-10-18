# Document Service

Enterprise-grade document service built with FastAPI, PostgreSQL, Redis, and ARQ.
Supports OCR, document storage, bulk uploading, compression, full-text search, versioning,
and audit logging.

## Features

- **Document ingestion**: single & bulk upload with SHA-256 deduplication
- **Compression**: gzip/zlib + pikepdf (qpdf) structural PDF optimization
- **OCR**: Tesseract-powered OCR via ARQ background workers (PDF → images → text)
- **Full-text search**: PostgreSQL `tsvector` + GIN index across filename + OCR content
- **Versioning**: per-document version history with change notes
- **Audit logging**: immutable action log for compliance
- **Storage**: pluggable backend (local filesystem default, S3/MinIO swap-ready)
- **Soft delete**: recoverable document removal

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API | FastAPI (Python 3.13) |
| Database | PostgreSQL 15 + SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| Cache/Queue | Redis 7 + ARQ |
| OCR | Tesseract + pytesseract + pdf2image + Pillow |
| PDF Optimization | pikepdf (qpdf) |
| Validation | Pydantic v2 |
| Testing | pytest + httpx |
| Lint/Type | ruff + mypy |

## Quick Start

### Prerequisites

- Python 3.13+
- Docker & Docker Compose (recommended)
- Tesseract OCR binary (for local dev without Docker)

### Using Docker Compose (recommended)

```bash
# Clone and enter the project
cd DocumentService

# Copy environment config
cp .env.example .env

# Start infrastructure + app + worker
docker compose up --build

# The API is available at http://localhost:8000
# Interactive docs: http://localhost:8000/docs
```

### Local Development

```bash
# Install system dependencies (Fedora)
sudo dnf install -y tesseract poppler-utils

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Copy environment config
cp .env.example .env
# Edit .env to point at your local PostgreSQL/Redis

# Run database migrations
alembic upgrade head

# Start the API
uvicorn app.main:app --reload --port 8000

# Start the ARQ worker (in a separate terminal)
arq app.worker.settings.WorkerSettings
```

## API Endpoints

### Documents

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/documents/upload` | Upload a single document |
| `POST` | `/api/v1/documents/bulk-upload` | Bulk upload multiple files |
| `GET` | `/api/v1/documents/` | List documents (with full-text search via `?search=`) |
| `GET` | `/api/v1/documents/{id}` | Get document metadata |
| `GET` | `/api/v1/documents/{id}/download` | Download the document file |
| `PUT` | `/api/v1/documents/{id}` | Update document metadata |
| `DELETE` | `/api/v1/documents/{id}` | Soft-delete a document |
| `GET` | `/api/v1/documents/{id}/versions` | List document versions |
| `POST` | `/api/v1/documents/{id}/versions` | Upload a new version |
| `POST` | `/api/v1/documents/{id}/ocr` | Trigger OCR processing |
| `GET` | `/api/v1/documents/{id}/ocr` | Get OCR result |

### Admin

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/documents/admin/audit-logs` | List audit log entries (compliance) |

### Health

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/health` | Overall health |
| `GET` | `/api/v1/health/ready` | Readiness (DB + Redis) |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://docuser:docpass@localhost:5432/docservice` | PostgreSQL connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string (ARQ) |
| `STORAGE_BACKEND` | `local` | `local` or `s3` |
| `STORAGE_ROOT` | `/app/data/storage` | Local storage directory |
| `STORAGE_BUCKET` | (none) | S3 bucket name (when backend=s3) |
| `MAX_FILE_SIZE_BYTES` | `104857600` (100 MB) | Max upload size |
| `OCR_LANGUAGE` | `eng` | Tesseract language |
| `ENABLE_PDF_OPTIMIZE` | `true` | Enable pikepdf PDF structural optimization |
| `DEFAULT_COMPRESSION_LEVEL` | `6` | gzip compression level (0-9) |

## Running Tests

```bash
pip install -r requirements.txt pytest pytest-asyncio httpx
python -m pytest tests/ -v
```

## Code Quality

```bash
ruff check app/ tests/
mypy app/
```

## Project Structure

```
DocumentService/
├── app/
│   ├── api/v1/documents.py   # Document + audit-log routes
│   ├── api/v1/health.py      # Health endpoints
│   ├── api/v1/api.py         # Router aggregation
│   ├── api/deps.py           # DB session dependency
│   ├── core/config.py        # Pydantic Settings
│   ├── core/security.py      # Filename sanitization
│   ├── db/base.py            # SQLAlchemy Base
│   ├── db/env.py             # Alembic env (async)
│   ├── models/               # SQLAlchemy models
│   ├── schemas/              # Pydantic schemas
│   ├── services/             # Business logic (storage, compression, document)
│   ├── worker/               # ARQ worker (OCR, thumbnails)
│   └── main.py               # FastAPI app factory
├── tests/
│   ├── conftest.py           # Test fixtures
│   ├── test_documents.py     # CRUD tests
│   ├── test_compression.py   # Compression tests
│   └── test_search.py        # Full-text search tests
├── docker-compose.yml        # Postgres + Redis + App + Worker
├── Dockerfile
├── pyproject.toml
├── requirements.txt
├── .env.example
├── alembic.ini
└── README.md
```

## License

Internal use.
