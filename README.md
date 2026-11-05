# Price Forecasting Service

Run, track, and retrieve electricity price forecasts through a stable service API. The service separates market data, execution, model implementations, and result storage so models can change without rewriting the application.

This initial implementation provides a web console, FastAPI endpoints, a durable SQLite job queue, a standalone worker, immutable data snapshots, a local model registry, and model contract validation. It includes a deterministic seasonal-naive example using **synthetic DEMO data**, not a trained production forecasting model.

## Run locally

Requires Python 3.11+ and two terminals. No cloud credentials are required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

price-forecast register examples/seasonal-naive \
  --input examples/input.json --expected examples/expected.json
python -m uvicorn price_forecasting.api:create_app --factory --host 127.0.0.1
```

In a second terminal, activate the same environment and start the worker:

```bash
source .venv/bin/activate
price-forecast worker
```

Open <http://127.0.0.1:8000> for the console or <http://127.0.0.1:8000/docs> for the API. Both processes must use the same state directory. The default is `./state`.

Submit a forecast:

```bash
curl -s http://127.0.0.1:8000/forecasts \
  -H 'Content-Type: application/json' \
  --data-binary @examples/request.json
curl -s http://127.0.0.1:8000/forecasts/RUN_ID
```

Submission returns HTTP `202` with a run ID. The worker changes the status from `queued` to `running`, then to `succeeded` or `failed`. Completed runs include the prediction and model/data provenance. Reusing an idempotency key with the same request returns the same run; different content returns `409`.

For a terminal-only run:

```bash
price-forecast submit examples/request.json
price-forecast worker --once
```

## Components

| Component | Implementation | Responsibility |
| --- | --- | --- |
| Web console | `console.html` | Submit forecasts; inspect models, runs, and results |
| API | `api.py` | Request validation, submission, status and result queries |
| Execution | `runtime.py` | Coordinate data access, model loading, inference and persistence |
| Queue / run store | `store.py` | Transactional claims, idempotency, leases, durable status |
| Data snapshots | `store.py` | Store and verify immutable, content-addressed model inputs |
| Model registry / loader | `registry.py` | Validate packages, pin versions and verify artifacts |
| Model implementations | `models.py` | Model-specific transforms, inference and output conversion |
| Contracts | `contracts.py`, `validation.py` | Structural and semantic input/output validation |
| Administration | `cli.py` | Package checks, registration, job submission and workers |

See [architecture](docs/architecture.md), [model contracts](docs/model-contract.md), and [operations](docs/operations.md).

## API

| Method | Path | Response |
| --- | --- | --- |
| GET | `/` | Web console |
| GET | `/health` | API process health; not worker readiness |
| GET | `/status` | Persisted run counts by state |
| GET | `/models` | Registered model versions and integrity state |
| POST | `/forecasts` | Queue a forecast; HTTP 202 |
| GET | `/forecasts?limit=25` | Recent runs, including available results |
| GET | `/forecasts/{run_id}` | Run status, error code and result |

Inputs currently arrive inline with the request and are stored as snapshots. Live market ingestion is a planned data-source integration. Model registration is an operator CLI action, not a public API.

## Development

```bash
python -m ruff check .
python -m pytest -q
price-forecast export-schemas /tmp/forecast-schemas
```

Tests cover HTTP-to-worker execution, process restart persistence, concurrent claims, idempotency, data publication cutoffs, DST, negative prices, artifact tampering, output contracts, and failed handovers. CI runs checks on Python 3.11–3.13.

## Scope and next steps

The repository is for **the complete forecasting service**. Model handover is one subsystem.

| Area | Current | Next |
| --- | --- | --- |
| Application | Request/run/result API and reference console | Tenant access, richer result comparisons |
| Market data | Caller-supplied, persisted input snapshots | Market connectors, vintage-aware scheduled ingestion |
| Forecast execution | Durable local queue and standalone worker | Heartbeats, explicit retry policy, distributed queue |
| Models | Seasonal-naive adapter and immutable local registry | DS models, additional adapters, remote registry |
| Training / evaluation | Package golden-case and reload checks | Training jobs, rolling backtests, business acceptance gates |
| Scheduling | CLI submission supports external schedulers | Managed schedule configuration |
| Observability | Logs, run errors and status counts | Latency metrics, actual-vs-forecast accuracy, alerts |
| Deployment | Docker / Compose reference deployment | Authenticated cloud deployment, managed storage, CI promotion |

The local deployment has no built-in authentication or tenant isolation. Keep it on loopback. Shared deployments need authentication, authorization, resource limits and durable infrastructure; see the [runbook](docs/operations.md).
