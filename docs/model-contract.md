# Model contract v1.0

A model package is callable Python inference code installed in the runtime plus a versioned artifact directory. The application does not interpret training notebooks or import arbitrary code paths from a manifest.

```python
class ForecastModel(Protocol):
    @classmethod
    def load(cls, artifact_dir: Path, manifest: ModelManifest) -> "ForecastModel": ...

    def predict(self, inputs: ForecastInput) -> ModelPrediction: ...
```

## Input semantics

| Field | Meaning / validation |
| --- | --- |
| `series` | Market, DA/RT, location, price type, currency, interval and IANA market timezone |
| `as_of` | Latest allowed publication time; all data must be available by this cutoff |
| `history` | Ordered, regular observations; timestamps and availability cannot exceed `as_of` |
| `target_times` | Unique increasing UTC instants after `as_of`, separated by the series interval |
| `future_features` | Optional rows matching every target; each row records its publication time |
| `quantiles` | Unique sorted probabilities strictly between 0 and 1, supported by the model |

All aware datetimes normalize to UTC, including native Python datetimes across DST folds. Naive datetimes, missing required values, NaN and infinity fail validation. Prices may be negative. Domain-specific price caps are not invented from sample data.

A history timestamp denotes the **interval start**, and a price is the value for that interval. `available_at` records when that value was available, independently of interval time. Each future-feature row represents one complete forecast vintage; a row is available only when every included value is available. Revision selection must happen upstream. The validator can check submitted timestamps; it cannot prove a caller supplied authentic vintage metadata.

Model manifests declare minimum history, maximum lead time, interval, markets, market types, price types, currency, feature requirements, point forecast meaning and quantile capabilities. The first target may start later than the next historical interval but must align with its grid; maximum lead time includes that gap. Continuous history is required in v1; a model that handles gaps needs an explicit future contract extension.

## Output semantics

`ModelPrediction` contains `point_kind` (`point`, `mean` or `median`) and one `ForecastPoint` per target. Times must match the request exactly. Quantile keys must match requested probabilities, and quantiles cannot cross. Values must be finite. The model cannot relabel a point prediction as a calibrated mean or probability distribution.

`ForecastResult` adds provenance on the application side. Model implementations do not write run metadata, reach into the job database or select production versions.

## Package contents

- `manifest.json`: identity, contract version, adapter key, code revision, runtime requirements, capabilities, artifact hashes.
- Declared inference artifacts: weights, preprocessing objects and configuration.
- Separate golden input and expected-output JSON supplied to registration.

Dependencies are recorded, not installed by the loader. Build a runtime image with compatible dependencies before registering the package. `code_revision` records the adapter's source revision; the package digest covers the canonical manifest and its declared artifact hashes, not the whole container image. Pin the image digest in a cloud deployment to capture the complete environment.

## Add a model

1. Implement the protocol in a trusted Python module. Include training-dependent transforms with the model.
2. Add its class under a stable key in `models.ADAPTERS`; deploy the code and dependencies to both registration and worker environments.
3. Export inference artifacts and generate SHA-256 hashes. Set an immutable model version and code revision.
4. Supply normal and edge-case fixtures. Test unsupported inputs and failed loads as well as successful inference.
5. Run `check-package`, then `register`. The reference acceptance check uses exact deterministic output equality after fresh loads. Stochastic or hardware-sensitive models need an agreed seed/tolerance policy before admission.
6. Submit forecasts with the registered version. Versions are explicit; promotion means changing the calling configuration to a tested version.

The included seasonal-naive adapter repeats an observed season with the correct target lead-time phase. It is a reference for integration and does not establish forecasting accuracy.
