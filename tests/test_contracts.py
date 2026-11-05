from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from price_forecasting.contracts import ForecastInput, ModelManifest, ModelPrediction
from price_forecasting.validation import ContractViolation, validate_input, validate_output


@pytest.mark.parametrize(
    "mutation", ["naive", "future-publication", "duplicate", "nan", "future-history"]
)
def test_invalid_inputs(input_dict, mutation):
    if mutation == "naive":
        input_dict["as_of"] = "2026-01-01T01:00:00"
    elif mutation == "future-publication":
        input_dict["history"][0]["available_at"] = "2026-01-01T02:00:00Z"
    elif mutation == "duplicate":
        input_dict["target_times"][1] = input_dict["target_times"][0]
    elif mutation == "nan":
        input_dict["history"][0]["price"] = float("nan")
    elif mutation == "future-history":
        input_dict["history"][1]["timestamp"] = "2026-01-02T01:00:00Z"
    with pytest.raises(ValidationError):
        ForecastInput.model_validate(input_dict)


def test_negative_prices_are_valid(inputs):
    assert inputs.history[0].price == -5


def test_native_dst_fold_normalized_to_utc(input_dict):
    zone = ZoneInfo("America/Chicago")
    # Both 01:00 values represent distinct UTC instants during fall-back.
    input_dict["history"][0].update(
        timestamp=datetime(2026, 11, 1, 1, tzinfo=zone, fold=0), available_at="2026-11-01T06:00:00Z"
    )
    input_dict["history"][1].update(
        timestamp=datetime(2026, 11, 1, 1, tzinfo=zone, fold=1), available_at="2026-11-01T07:00:00Z"
    )
    input_dict["as_of"] = "2026-11-01T07:00:00Z"
    input_dict["target_times"] = ["2026-11-01T08:00:00Z", "2026-11-01T09:00:00Z"]
    inputs = ForecastInput.model_validate(input_dict)
    assert inputs.history[0].timestamp != inputs.history[1].timestamp
    assert inputs.history[0].timestamp.utcoffset().total_seconds() == 0


def test_future_features_reject_unavailable_vintage(input_dict):
    input_dict["future_features"] = [
        {"timestamp": t, "available_at": "2026-01-02T00:00:00Z", "values": {"demand": 1}}
        for t in input_dict["target_times"]
    ]
    with pytest.raises(ValidationError, match="unavailable"):
        ForecastInput.model_validate(input_dict)


def test_model_specific_requirements(service, inputs):
    manifest = service.registry.load("seasonal-naive", "1.0.0").manifest
    manifest = ModelManifest.model_validate(
        {**manifest.model_dump(), "future_features": ["demand"]}
    )
    with pytest.raises(ContractViolation, match="future features"):
        validate_input(manifest, inputs)


def test_wrong_output_time_rejected(service, inputs, expected):
    manifest = service.registry.load("seasonal-naive", "1.0.0").manifest
    output = ModelPrediction(point_kind="point", points=expected.points[::-1])
    with pytest.raises(ContractViolation, match="timestamps"):
        validate_output(manifest, inputs, output)


def test_wrong_point_meaning_rejected(service, inputs, expected):
    manifest = service.registry.load("seasonal-naive", "1.0.0").manifest
    output = ModelPrediction(point_kind="mean", points=expected.points)
    with pytest.raises(ContractViolation, match="meaning"):
        validate_output(manifest, inputs, output)


def test_unsupported_quantiles_rejected(service, input_dict):
    input_dict["quantiles"] = [0.5]
    inputs = ForecastInput.model_validate(input_dict)
    manifest = service.registry.load("seasonal-naive", "1.0.0").manifest
    with pytest.raises(ContractViolation, match="quantiles"):
        validate_input(manifest, inputs)
