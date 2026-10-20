#!/usr/bin/env python3
"""Expose an installed upstream thisthat runtime to benchmark_decisions.py."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn

from decision_benchmark.this_that_bridge import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="flock-io/this-that-model-1.2")
    parser.add_argument("--device", default="auto", choices=("auto", "mlx", "cuda", "mps", "cpu"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--api-key-env", help="Optional environment variable for a required Bearer key"
    )
    parser.add_argument("--max-state-tokens", type=int, default=8192)
    args = parser.parse_args()
    app = create_app(
        args.model,
        device=args.device,
        api_key_env=args.api_key_env,
        max_state_tokens=args.max_state_tokens,
    )
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
