import json
from pathlib import Path

import pytest

from price_forecasting.api import build_service
from price_forecasting.contracts import ForecastInput, ForecastRequest, ModelPrediction

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def input_dict():
    return json.loads((EXAMPLES / "input.json").read_text())


@pytest.fixture
def inputs(input_dict):
    return ForecastInput.model_validate(input_dict)


@pytest.fixture
def expected():
    return ModelPrediction.model_validate_json((EXAMPLES / "expected.json").read_bytes())


@pytest.fixture
def package(tmp_path):
    import shutil

    destination = tmp_path / "package"
    shutil.copytree(EXAMPLES / "seasonal-naive", destination)
    return destination


@pytest.fixture
def service(tmp_path, package, inputs, expected):
    result = build_service(tmp_path / "state")
    result.registry.register(package, inputs, expected)
    return result


@pytest.fixture
def forecast_request(inputs):
    return ForecastRequest(
        model_id="seasonal-naive", model_version="1.0.0", inputs=inputs, idempotency_key="test-001"
    )
