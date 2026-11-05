import json

import pytest

from price_forecasting.registry import LocalRegistry, accept_package


def test_handover_and_reload(package, inputs, expected):
    assert accept_package(package, inputs, expected)["status"] == "passed"


def test_package_rejects_corruption(package, inputs, expected):
    (package / "model.json").write_text('{"seasonal_period": 99}')
    with pytest.raises(ValueError, match="hash mismatch"):
        accept_package(package, inputs, expected)


def test_registry_is_immutable(service, package, inputs, expected):
    with pytest.raises(FileExistsError, match="immutable"):
        service.registry.register(package, inputs, expected)


def test_post_registration_manifest_tampering(service):
    path = service.registry.root / "seasonal-naive/1.0.0/manifest.json"
    data = json.loads(path.read_text())
    data["max_horizon"] = 999
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="changed"):
        service.registry.load("seasonal-naive", "1.0.0")


def test_traversal_rejected(package, inputs, expected):
    path = package / "manifest.json"
    data = json.loads(path.read_text())
    data["artifacts"] = {"../outside": "0" * 64}
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="safe relative"):
        accept_package(package, inputs, expected)


def test_unknown_adapter_not_imported(package, inputs, expected):
    path = package / "manifest.json"
    data = json.loads(path.read_text())
    data["adapter"] = "arbitrary-code"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="allowlist"):
        accept_package(package, inputs, expected)


def test_failed_acceptance_does_not_publish(tmp_path, package, inputs, expected):
    bad = expected.model_copy(update={"points": expected.points[::-1]})
    registry = LocalRegistry(tmp_path / "registry")
    with pytest.raises(ValueError, match="Golden-case"):
        registry.register(package, inputs, bad)
    assert not (registry.root / "seasonal-naive/1.0.0").exists()


def test_identifier_cannot_escape_registry(service):
    with pytest.raises(ValueError):
        service.registry.load("../outside", "1.0.0")
