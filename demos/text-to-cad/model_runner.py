#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import time
import traceback
from pathlib import Path


def load_build(model_path: Path):
    spec = importlib.util.spec_from_file_location("cad_model", model_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {model_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    build = getattr(module, "build", None)
    if not callable(build):
        raise AttributeError("module defines no callable build(output_dir)")
    return build


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: model_runner.py <model.py> <output_dir>", file=sys.stderr)
        return 2

    model_path = Path(sys.argv[1]).resolve()
    output_dir = Path(sys.argv[2]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    record = {
        "model_path": str(model_path),
        "output_dir": str(output_dir),
        "ok": False,
        "secs": 0.0,
        "result": None,
        "error": None,
        "traceback": None,
    }

    try:
        build = load_build(model_path)
        result = build(output_dir)
        record["ok"] = True
        record["result"] = result
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        record["traceback"] = traceback.format_exc(limit=20)
    finally:
        record["secs"] = round(time.time() - started, 3)

    print("##CAD_RESULT## " + json.dumps(record, ensure_ascii=False), flush=True)
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
