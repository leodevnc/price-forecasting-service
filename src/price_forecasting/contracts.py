"""Versioned, timezone-aware contracts at the DS / operations boundary."""

from datetime import UTC, timedelta
from itertools import pairwise
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)

UTCDateTime = Annotated[AwareDatetime, AfterValidator(lambda t: t.astimezone(UTC))]

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Series(Contract):
    market: Identifier
    market_type: Literal["DA", "RT"]
    location: Identifier
    price_type: Identifier = "LMP"
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "USD"
    unit: Literal["currency/MWh"] = "currency/MWh"
    market_timezone: str = "UTC"
    interval_seconds: int = Field(default=3600, gt=0, le=86400)

    @field_validator("market_timezone")
    @classmethod
    def timezone_exists(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown IANA market timezone") from exc
        return value


class Observation(Contract):
    timestamp: UTCDateTime
    available_at: UTCDateTime
    price: FiniteFloat
    features: dict[str, FiniteFloat] = Field(default_factory=dict)


class FutureFeatures(Contract):
    timestamp: UTCDateTime
    available_at: UTCDateTime
    values: dict[str, FiniteFloat]


class ForecastInput(Contract):
    contract_version: Literal["1.0"] = "1.0"
    series: Series
    as_of: UTCDateTime
    target_times: tuple[UTCDateTime, ...] = Field(min_length=1, max_length=10000)
    history: tuple[Observation, ...] = Field(min_length=1, max_length=100000)
    future_features: tuple[FutureFeatures, ...] = ()
    quantiles: tuple[float, ...] = ()

    @model_validator(mode="after")
    def validate_time_and_values(self):
        step = timedelta(seconds=self.series.interval_seconds)
        times = self.target_times
        if any(t <= self.as_of for t in times):
            raise ValueError("Targets must be strictly after as_of")
        if any(b - a != step for a, b in pairwise(times)):
            raise ValueError("Targets must be unique, ordered and regularly spaced in UTC")
        past = [row.timestamp for row in self.history]
        if any(b - a != step for a, b in pairwise(past)):
            raise ValueError("History must be unique, ordered and regularly spaced in UTC")
        if any(row.timestamp > self.as_of or row.available_at > self.as_of for row in self.history):
            raise ValueError("History contains observations unavailable at as_of")
        if any(row.available_at > self.as_of for row in self.future_features):
            raise ValueError("Future features contain forecasts unavailable at as_of")
        future_times = [row.timestamp for row in self.future_features]
        if self.future_features and future_times != list(times):
            raise ValueError("Future feature rows must match target_times exactly")
        if (
            any(not 0 < q < 1 for q in self.quantiles)
            or tuple(sorted(set(self.quantiles))) != self.quantiles
        ):
            raise ValueError("Quantiles must be unique, increasing and between 0 and 1")
        return self


class ModelManifest(Contract):
    contract_version: Literal["1.0"] = "1.0"
    model_id: Identifier
    version: Identifier
    adapter: Identifier
    description: str
    code_revision: str = Field(min_length=1)
    runtime_dependencies: tuple[str, ...]
    interval_seconds: int = Field(gt=0, le=86400)
    min_history: int = Field(ge=1)
    max_horizon: int = Field(ge=1, le=10000)
    # Explicit applicability, never inferred from numeric sample values.
    markets: tuple[Identifier, ...] = Field(min_length=1)
    market_types: tuple[Literal["DA", "RT"], ...] = Field(min_length=1)
    price_types: tuple[Identifier, ...] = ("LMP",)
    currency: str = "USD"
    historical_features: tuple[str, ...] = ()
    future_features: tuple[str, ...] = ()
    supported_quantiles: tuple[float, ...] = ()
    point_kind: Literal["mean", "median", "point"] = "point"
    # Relative file paths -> SHA-256. No pickle or dynamic imports by the loader.
    artifacts: dict[str, Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_quantiles(self):
        qs = self.supported_quantiles
        if any(not 0 < q < 1 for q in qs) or tuple(sorted(set(qs))) != qs:
            raise ValueError("supported_quantiles must be unique, sorted and between 0 and 1")
        return self


class ForecastPoint(Contract):
    timestamp: UTCDateTime
    value: FiniteFloat
    quantiles: dict[str, FiniteFloat] = Field(default_factory=dict)


class ModelPrediction(Contract):
    point_kind: Literal["mean", "median", "point"]
    points: tuple[ForecastPoint, ...]


class ForecastResult(Contract):
    contract_version: Literal["1.0"] = "1.0"
    run_id: str
    model_id: str
    model_version: str
    package_digest: str
    snapshot_id: str
    series: Series
    as_of: UTCDateTime
    generated_at: UTCDateTime
    prediction: ModelPrediction


class ForecastRequest(Contract):
    model_id: Identifier
    model_version: Identifier
    inputs: ForecastInput
    idempotency_key: Annotated[str, Field(min_length=1, max_length=128)]


class RunView(Contract):
    run_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    model_id: str
    model_version: str
    snapshot_id: str
    error_code: str | None = None
    result: ForecastResult | None = None
