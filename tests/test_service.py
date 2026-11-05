from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from price_forecasting.api import build_service, create_app
from price_forecasting.contracts import ForecastInput
from price_forecasting.store import IdempotencyConflict


def test_submit_restart_worker_and_result(service, forecast_request, expected):
    queued = service.submit(forecast_request)
    assert queued.status == "queued"
    restarted = build_service(service.store.path.parent)
    assert restarted.work_once() == queued.run_id
    done = restarted.store.get(queued.run_id)
    assert done.status == "succeeded"
    assert done.result.prediction == expected
    assert done.result.snapshot_id == queued.snapshot_id
    assert done.result.package_digest
    assert restarted.work_once() is None


def test_idempotency_and_conflict(service, forecast_request):
    one = service.submit(forecast_request)
    assert service.submit(forecast_request).run_id == one.run_id
    changed = forecast_request.model_copy(update={"model_version": "different"})
    with pytest.raises(IdempotencyConflict):
        service.store.submit(changed, "irrelevant")


def test_concurrent_workers_claim_once(service, forecast_request):
    service.submit(forecast_request)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: service.store.claim(), range(4)))
    assert sum(x is not None for x in claims) == 1


def test_abandoned_run_marked_failed(service, forecast_request):
    queued = service.submit(forecast_request)
    service.store.claim(lease_seconds=-1)
    assert service.store.claim() is None
    assert service.store.get(queued.run_id).error_code == "WORKER_LOST"


def test_package_corrupted_after_submit_fails_run(service, forecast_request):
    queued = service.submit(forecast_request)
    (service.registry.root / "seasonal-naive/1.0.0/model.json").write_text("corrupted")
    service.work_once()
    run = service.store.get(queued.run_id)
    assert run.status == "failed"
    assert run.error_code == "EXECUTION_FAILED"
    assert run.result is None


def test_snapshot_corruption_fails_run(service, forecast_request):
    queued = service.submit(forecast_request)
    with service.store.connect() as db:
        db.execute("UPDATE snapshots SET body='{}'")
    service.work_once()
    assert service.store.get(queued.run_id).status == "failed"


def test_future_start_uses_correct_seasonal_phase(service, input_dict):
    input_dict["target_times"] = ["2026-01-02T00:00:00Z", "2026-01-02T01:00:00Z"]
    inputs = ForecastInput.model_validate(input_dict)
    output = service.registry.load("seasonal-naive", "1.0.0").predict(inputs)
    assert [p.value for p in output.points] == [-5, 30]


def test_api_flow(service, forecast_request):
    app = create_app(service.store.path.parent)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        response = client.post("/forecasts", json=forecast_request.model_dump(mode="json"))
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        assert response.json()["status"] == "queued"
        app.state.service.work_once()
        result = client.get(f"/forecasts/{run_id}")
        assert result.status_code == 200
        assert result.json()["status"] == "succeeded"
        assert client.get("/forecasts/missing").status_code == 404
        assert client.get("/openapi.json").status_code == 200


def test_api_contract_errors(service, forecast_request):
    with TestClient(create_app(service.store.path.parent)) as client:
        bad = forecast_request.model_dump(mode="json")
        bad["inputs"]["history"][0]["available_at"] = "2099-01-01T00:00:00Z"
        assert client.post("/forecasts", json=bad).status_code == 422
        bad = forecast_request.model_dump(mode="json")
        bad["model_version"] = "missing"
        assert client.post("/forecasts", json=bad).status_code == 404
        assert (
            client.post("/forecasts", json=forecast_request.model_dump(mode="json")).status_code
            == 202
        )
        bad = forecast_request.model_dump(mode="json")
        bad["inputs"]["history"][0]["price"] = 100
        assert client.post("/forecasts", json=bad).status_code == 409


def test_console_models_and_recent_runs(service, forecast_request):
    with TestClient(create_app(service.store.path.parent)) as client:
        assert "Price Forecasting" in client.get("/").text
        assert client.get("/models").json()[0]["version"] == "1.0.0"
        assert client.get("/status").json()["runs"]["queued"] == 0
        client.post("/forecasts", json=forecast_request.model_dump(mode="json"))
        assert len(client.get("/forecasts?limit=1").json()) == 1
        assert client.get("/status").json()["runs"]["queued"] == 1
        assert client.get("/forecasts?limit=10000").status_code == 422
