"""Local administration and a standalone durable-queue worker."""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .api import build_service
from .contracts import ForecastInput, ModelPrediction
from .registry import accept_package


def main():
    parser = argparse.ArgumentParser(prog="price-forecast")
    parser.add_argument("--state-dir", type=Path, default=Path("state"))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check-package", "register"):
        cmd = commands.add_parser(name)
        cmd.add_argument("package", type=Path)
        cmd.add_argument("--input", type=Path, required=True)
        cmd.add_argument("--expected", type=Path, required=True)
    submit = commands.add_parser("submit")
    submit.add_argument("request", type=Path)
    schemas = commands.add_parser("export-schemas")
    schemas.add_argument("directory", type=Path)
    worker = commands.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        if args.command in {"check-package", "register"}:
            fixture = ForecastInput.model_validate_json(args.input.read_bytes())
            expected = ModelPrediction.model_validate_json(args.expected.read_bytes())
            report = accept_package(args.package, fixture, expected)
            if args.command == "register":
                registry = build_service(args.state_dir).registry
                manifest = registry.register(args.package, fixture, expected)
                report["registered"] = f"{manifest.model_id}/{manifest.version}"
            print(json.dumps(report, indent=2))
        elif args.command == "submit":
            from .contracts import ForecastRequest

            request = ForecastRequest.model_validate_json(args.request.read_bytes())
            print(build_service(args.state_dir).submit(request).model_dump_json(indent=2))
        elif args.command == "export-schemas":
            from .contracts import ForecastRequest, ForecastResult, ModelManifest

            args.directory.mkdir(parents=True, exist_ok=True)
            for contract in (ForecastInput, ForecastRequest, ForecastResult, ModelManifest):
                (args.directory / f"{contract.__name__}.json").write_text(
                    json.dumps(contract.model_json_schema(), indent=2) + "\n"
                )
            print(args.directory)
        else:
            service = build_service(args.state_dir)
            while True:
                run_id = service.work_once()
                if args.once:
                    print(json.dumps({"run_id": run_id}))
                    break
                if run_id is None:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        return
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
