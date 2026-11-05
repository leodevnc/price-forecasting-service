"""Validate semantic input/output contracts independently of the model."""

from datetime import UTC, timedelta
from itertools import pairwise

from .contracts import ForecastInput, ModelManifest, ModelPrediction


class ContractViolation(ValueError):
    pass


def validate_input(manifest: ModelManifest, data: ForecastInput) -> None:
    series = data.series
    if (
        series.market not in manifest.markets
        or series.market_type not in manifest.market_types
        or series.price_type not in manifest.price_types
        or series.currency != manifest.currency
    ):
        raise ContractViolation("Unsupported market, price type or currency")
    if series.interval_seconds != manifest.interval_seconds:
        raise ContractViolation("Unsupported interval")
    if len(data.history) < manifest.min_history:
        raise ContractViolation("Insufficient history")
    if len(data.target_times) > manifest.max_horizon:
        raise ContractViolation("Unsupported forecast horizon")
    # UTC conversion also handles local DST transitions unambiguously.
    first = data.target_times[0].astimezone(UTC)
    last = data.history[-1].timestamp.astimezone(UTC)
    gap = (first - last) / timedelta(seconds=manifest.interval_seconds)
    if gap < 1 or not gap.is_integer():
        raise ContractViolation("Targets must align with the historical time grid")
    if gap + len(data.target_times) - 1 > manifest.max_horizon:
        raise ContractViolation("Target lead time exceeds max_horizon")
    if not set(data.quantiles).issubset(manifest.supported_quantiles):
        raise ContractViolation("Requested quantiles are unsupported")
    required_past = set(manifest.historical_features)
    if any(not required_past.issubset(row.features) for row in data.history):
        raise ContractViolation("Missing historical features")
    required_future = set(manifest.future_features)
    if required_future and not data.future_features:
        raise ContractViolation("Missing future features")
    if any(not required_future.issubset(row.values) for row in data.future_features):
        raise ContractViolation("Missing required future feature columns")


def validate_output(manifest: ModelManifest, data: ForecastInput, output: ModelPrediction) -> None:
    if output.point_kind != manifest.point_kind:
        raise ContractViolation("Point forecast meaning differs from the manifest")
    if tuple(p.timestamp for p in output.points) != data.target_times:
        raise ContractViolation("Output timestamps differ from target_times")
    expected = {str(q) for q in data.quantiles}
    for point in output.points:
        if set(point.quantiles) != expected:
            raise ContractViolation("Output quantiles differ from the request")
        values = [point.quantiles[str(q)] for q in data.quantiles]
        if any(a > b for a, b in pairwise(values)):
            raise ContractViolation("Output quantiles cross")
