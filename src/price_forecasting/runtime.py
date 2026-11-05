"""Operations-side orchestration, independent of individual model libraries."""

import logging
from datetime import UTC, datetime

from .contracts import ForecastRequest, ForecastResult
from .registry import LocalRegistry
from .store import SQLiteStore
from .validation import ContractViolation, validate_input

logger = logging.getLogger(__name__)


class ForecastService:
    def __init__(self, registry: LocalRegistry, store: SQLiteStore):
        self.registry = registry
        self.store = store

    def submit(self, request: ForecastRequest):
        loaded = self.registry.load(request.model_id, request.model_version)
        validate_input(loaded.manifest, request.inputs)
        return self.store.submit(request, loaded.package_digest)

    def work_once(self) -> str | None:
        job = self.store.claim()
        if job is None:
            return None
        run_id, token = job["id"], job["claim_token"]
        try:
            request = ForecastRequest.model_validate_json(job["request"])
            data = self.store.snapshot(job["snapshot_id"])
            loaded = self.registry.load(request.model_id, request.model_version)
            if loaded.package_digest != job["package_digest"]:
                raise ContractViolation("Pinned package changed after submission")
            prediction = loaded.predict(data)
            result = ForecastResult(
                run_id=run_id,
                model_id=request.model_id,
                model_version=request.model_version,
                package_digest=loaded.package_digest,
                snapshot_id=job["snapshot_id"],
                series=data.series,
                as_of=data.as_of,
                generated_at=datetime.now(UTC),
                prediction=prediction,
            )
        except Exception as exc:  # noqa: BLE001 - isolate failures in trusted model plugins
            # Store stable codes, not model exceptions or raw input data in API responses.
            code = (
                "CONTRACT_VIOLATION" if isinstance(exc, ContractViolation) else "EXECUTION_FAILED"
            )
            logger.error("Forecast run %s failed (%s; %s)", run_id, code, type(exc).__name__)
            self.store.finish(run_id, token, None, code)
        else:
            self.store.finish(run_id, token, result, None)
            logger.info("Forecast run %s succeeded", run_id)
        return run_id
