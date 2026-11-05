"""Reference API. Run behind authenticated infrastructure for shared deployments."""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from .contracts import ForecastRequest, RunView
from .registry import LocalRegistry
from .runtime import ForecastService
from .store import IdempotencyConflict, SQLiteStore
from .validation import ContractViolation


def build_service(state_dir: Path) -> ForecastService:
    return ForecastService(
        LocalRegistry(state_dir / "registry"), SQLiteStore(state_dir / "runs.db")
    )


def create_app(state_dir: Path | None = None) -> FastAPI:
    service = build_service(state_dir or Path(os.environ.get("FORECAST_STATE_DIR", "state")))
    app = FastAPI(title="Price Forecasting Service", version="0.1.0")
    app.state.service = service

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def console():
        return (Path(__file__).parent / "console.html").read_text()

    @app.get("/models")
    def models():
        return service.registry.list_models()

    @app.get("/status")
    def status():
        return {"runs": service.store.status_counts()}

    @app.get("/forecasts", response_model=list[RunView])
    def list_runs(limit: int = Query(default=50, ge=1, le=100)):
        return service.store.list_runs(limit)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/forecasts", response_model=RunView, status_code=202)
    def submit(request: ForecastRequest):
        try:
            return service.submit(request)
        except FileNotFoundError as exc:
            raise HTTPException(404, "Model version not found") from exc
        except IdempotencyConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ContractViolation as exc:
            raise HTTPException(422, str(exc)) from exc
        except (ValueError, ValidationError) as exc:
            raise HTTPException(409, "Model package is invalid; contact the operator") from exc

    @app.get("/forecasts/{run_id}", response_model=RunView)
    def get_run(run_id: str):
        try:
            return service.store.get(run_id)
        except KeyError as exc:
            raise HTTPException(404, "Forecast run not found") from exc

    return app
