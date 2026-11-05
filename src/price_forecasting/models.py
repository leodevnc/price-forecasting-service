"""Model-side implementation. Production adapters are explicitly allowlisted."""

import json
from pathlib import Path
from typing import Protocol

from pydantic import Field

from .contracts import Contract, ForecastInput, ForecastPoint, ModelManifest, ModelPrediction


class ForecastModel(Protocol):
    @classmethod
    def load(cls, artifact_dir: Path, manifest: ModelManifest) -> "ForecastModel": ...

    def predict(self, inputs: ForecastInput) -> ModelPrediction: ...


class SeasonalConfig(Contract):
    seasonal_period: int = Field(ge=1)
    bias: float = Field(default=0, allow_inf_nan=False)


class SeasonalNaive:
    """Reference adapter for contract verification, not a trained price predictor."""

    def __init__(self, config: SeasonalConfig):
        self.config = config

    @classmethod
    def load(cls, artifact_dir: Path, manifest: ModelManifest) -> "SeasonalNaive":
        if "model.json" not in manifest.artifacts:
            raise ValueError("model.json must be checksummed in the manifest")
        config = SeasonalConfig.model_validate(
            json.loads((artifact_dir / "model.json").read_text())
        )
        if manifest.min_history < config.seasonal_period:
            raise ValueError("min_history must cover seasonal_period")
        if manifest.supported_quantiles or manifest.point_kind != "point":
            raise ValueError("Seasonal naive only supports point forecasts")
        return cls(config)

    def predict(self, inputs: ForecastInput) -> ModelPrediction:
        period = self.config.seasonal_period
        gap = int(
            (inputs.target_times[0] - inputs.history[-1].timestamp).total_seconds()
            / inputs.series.interval_seconds
        )
        values = [r.price for r in inputs.history[-period:]]
        return ModelPrediction(
            point_kind="point",
            points=tuple(
                ForecastPoint(timestamp=t, value=values[(gap - 1 + i) % period] + self.config.bias)
                for i, t in enumerate(inputs.target_times)
            ),
        )


ADAPTERS: dict[str, type[ForecastModel]] = {"seasonal-naive": SeasonalNaive}
