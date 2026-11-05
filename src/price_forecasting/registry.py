"""Filesystem registry with immutable versions and verified artifact hashes."""

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .contracts import ForecastInput, ModelManifest, ModelPrediction
from .models import ADAPTERS, ForecastModel
from .validation import validate_input, validate_output


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_file(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name or not relative.parts:
        raise ValueError("Artifact paths must be safe relative POSIX paths")
    path = root.joinpath(*relative.parts)
    if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
        raise ValueError("Symlink artifacts are unsupported")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Missing or unsafe artifact: {name}")
    return path


def inspect_package(path: Path) -> tuple[ModelManifest, str]:
    manifest = ModelManifest.model_validate_json(checked_file(path, "manifest.json").read_bytes())
    if manifest.adapter not in ADAPTERS:
        raise ValueError("Adapter is not installed in the trusted runtime allowlist")
    if any(
        name in manifest.artifacts
        for name in ("manifest.json", ".package_digest", ".acceptance.json")
    ):
        raise ValueError("Reserved artifact name")
    for name, expected in manifest.artifacts.items():
        if digest(checked_file(path, name).read_bytes()) != expected:
            raise ValueError(f"Artifact hash mismatch: {name}")
    canonical = manifest.model_dump_json().encode()
    return manifest, digest(canonical)


@dataclass(frozen=True)
class LoadedModel:
    manifest: ModelManifest
    package_digest: str
    model: ForecastModel

    def predict(self, inputs: ForecastInput) -> ModelPrediction:
        validate_input(self.manifest, inputs)
        # Revalidate even when an adapter bypasses Pydantic constructors.
        output = ModelPrediction.model_validate(
            self.model.predict(
                ForecastInput.model_validate_json(inputs.model_dump_json())
            ).model_dump()
        )
        validate_output(self.manifest, inputs, output)
        return output


class LocalRegistry:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def register(
        self, source: Path, fixture: ForecastInput, expected: ModelPrediction
    ) -> ModelManifest:
        manifest, _ = inspect_package(source)
        parent = self.root / manifest.model_id
        parent.mkdir(exist_ok=True)
        target = parent / manifest.version
        if target.exists():
            raise FileExistsError("Model versions are immutable; register a new version")
        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=parent))
        try:
            shutil.copyfile(source / "manifest.json", stage / "manifest.json")
            for name in manifest.artifacts:
                destination = stage / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(checked_file(source, name), destination)
            _copied_manifest, package_digest = inspect_package(stage)
            # Acceptance runs against the copied bytes that will actually be published.
            report = accept_package(stage, fixture, expected)
            (stage / ".acceptance.json").write_text(json.dumps(report, indent=2))
            (stage / ".package_digest").write_text(package_digest)
            os.rename(stage, target)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        return manifest

    def list_models(self) -> list[dict]:
        records = []
        for package in sorted(self.root.glob("*/*/manifest.json")):
            if package.parent.name.startswith("."):
                continue
            try:
                loaded = self.load(package.parent.parent.name, package.parent.name)
                records.append(
                    {
                        "model_id": loaded.manifest.model_id,
                        "version": loaded.manifest.version,
                        "package_digest": loaded.package_digest,
                        "description": loaded.manifest.description,
                    }
                )
            except (ValueError, OSError):
                records.append(
                    {
                        "model_id": package.parent.parent.name,
                        "version": package.parent.name,
                        "status": "invalid",
                    }
                )
        return records

    def load(self, model_id: str, version: str) -> LoadedModel:
        # Validate identifiers even for direct Python/CLI callers.
        from pydantic import TypeAdapter

        from .contracts import Identifier

        for value in (model_id, version):
            TypeAdapter(Identifier).validate_python(value)
        path = self.root / model_id / version
        if not path.is_dir():
            raise FileNotFoundError("Model version not found")
        manifest, package_digest = inspect_package(path)
        if (manifest.model_id, manifest.version) != (model_id, version):
            raise ValueError("Manifest identity differs from registry path")
        if checked_file(path, ".package_digest").read_text() != package_digest:
            raise ValueError("Package manifest changed after registration")
        return LoadedModel(
            manifest, package_digest, ADAPTERS[manifest.adapter].load(path, manifest)
        )


def accept_package(path: Path, fixture: ForecastInput, expected: ModelPrediction) -> dict:
    """Run handover golden-case, output contract and reload checks before registration."""
    manifest, package_digest = inspect_package(path)
    first = LoadedModel(manifest, package_digest, ADAPTERS[manifest.adapter].load(path, manifest))
    actual = first.predict(fixture)
    second = ADAPTERS[manifest.adapter].load(path, manifest)
    again = LoadedModel(manifest, package_digest, second).predict(fixture)
    # Exact comparison is intentional for the deterministic reference fixture.
    # Stochastic adapters must supply controlled seeds or an explicit tolerance policy.
    if actual != expected or actual != again:
        raise ValueError("Golden-case or fresh-load reproducibility check failed")
    return {
        "model_id": manifest.model_id,
        "version": manifest.version,
        "package_digest": package_digest,
        "status": "passed",
    }
