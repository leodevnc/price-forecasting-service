# Operations

## State and processes

Local state contains `runs.db` and `registry/<model_id>/<version>/`. Use a persistent local disk and keep API and workers on the same host or a supported shared volume. SQLite WAL is not a distributed queue and should not be placed on an arbitrary network filesystem.

Set `FORECAST_STATE_DIR` for the API and pass the same path to the CLI:

```bash
FORECAST_STATE_DIR=/srv/forecast python -m uvicorn price_forecasting.api:create_app --factory
price-forecast --state-dir /srv/forecast worker
```

The reference worker has a 300-second execution lease. A worker that dies leaves a running record. The next claim after lease expiry marks it `failed` with `WORKER_LOST`. A stale worker cannot overwrite a finalized record. There is no automatic retry, heartbeat or forced process termination yet. Keep model execution below the lease; long jobs need heartbeat support before deployment.

After investigating a failure, submit a new request with a new idempotency key. Do not edit queue rows to silently replay work.

## Scheduling

The CLI supports the same submission path as the API:

```bash
price-forecast --state-dir /srv/forecast submit /srv/requests/current.json
```

An external scheduler must construct fresh as-of times, target times and available data for each scheduled run. Use a deterministic key per schedule slot to prevent duplicates. Reusing `examples/request.json` repeatedly intentionally returns the same demonstration run.

## Docker

```bash
docker compose build
docker compose run --rm -v "$PWD/examples:/examples:ro" api \
  price-forecast --state-dir /state register /examples/seasonal-naive \
  --input /examples/input.json --expected /examples/expected.json
docker compose up
```

The image runs as a non-root user. The API is published on loopback by Compose. Mounts and filesystem access must be provisioned for the container user. The Docker setup is provided as a reference; run the container checks in the target environment before deploying.

## Failure codes and monitoring

| Code | Meaning | Action |
| --- | --- | --- |
| `CONTRACT_VIOLATION` | Model semantics or output timeline failed validation | Inspect model/data compatibility |
| `EXECUTION_FAILED` | Loading, artifact integrity, adapter or snapshot failure | Inspect registry and deployment logs |
| `WORKER_LOST` | Execution lease expired | Check worker availability and model runtime |

`GET /status` reports queue counts. `GET /health` confirms only that the HTTP process responds. Neither proves a worker is active or predictions are accurate. Centralized logs, latency histograms, worker readiness, actual price ingestion, forecast accuracy and drift alerts remain deployment work.

## Deployment boundary

Before exposing a shared service, connect authentication and tenant authorization, apply request/resource limits, configure backup/retention, and pin the runtime image and model versions. The current API trusts caller-supplied series data and cannot attest its origin. Store business data outside the public source repository.

A package checksum detects accidental mutation relative to its registered digest; it is not a signature against an attacker who can rewrite the registry and checksums. Keep registry writes operator-only. Model code is trusted installed code, not a sandboxed plugin.

Use a new immutable version for every model change. To roll back, update forecast submitters to a previously registered version. No moving alias is implemented, so queued runs retain their exact requested version.
