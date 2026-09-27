"""The CLI. ``argparse`` only — no framework, and no second code path.

It calls the same ``run_task`` the HTTP service calls, so the two cannot drift into
running different things.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from runtime.config import DEFAULT_CONFIG_DIR, available_configs, load_env_file
from runtime.factory import DEFAULT_NOTES_ROOT, DEFAULT_TRACE_DIR, run_task
from runtime.replay import replay
from runtime.schemas import RunOutput, RunRequest
from runtime.status import RunStatus

#: Statuses a caller can treat as a success at the shell level.
EXIT_OK: frozenset[RunStatus] = frozenset({RunStatus.OK, RunStatus.DEGRADED})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-runtime",
        description="Run one task through the agent runtime.",
    )
    parser.add_argument("--config-root", default=str(DEFAULT_CONFIG_DIR))
    parser.add_argument("--trace-dir", default=str(DEFAULT_TRACE_DIR))
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="run one task")
    run_parser.add_argument("--task", required=True)
    run_parser.add_argument("--config", default="default")
    run_parser.add_argument(
        "--confirmation-token",
        default=None,
        help="Authorises side-effecting tools. The model never sees it.",
    )
    run_parser.add_argument("--notes-root", default=str(DEFAULT_NOTES_ROOT))
    run_parser.add_argument("--quiet", action="store_true", help="print the status only")

    subparsers.add_parser("configs", help="list the worked-example configurations")

    replay_parser = subparsers.add_parser("replay", help="re-run a recorded trace")
    replay_parser.add_argument("trace", help="path to a .jsonl trace")
    replay_parser.add_argument("--out-dir", default=".traces/replay")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_env_file()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.error("a command is required: run | configs | replay")

    if args.command == "configs":
        for name in available_configs(args.config_root):
            print(name)
        return 0

    if args.command == "replay":
        output = replay(args.trace, trace_dir=args.out_dir)
        return _emit(output, quiet=False, trace_dir=args.out_dir)

    request = RunRequest(
        task=args.task,
        config=args.config,
        confirmation_token=args.confirmation_token,
    )
    output = run_task(
        request,
        config_root=args.config_root,
        trace_dir=args.trace_dir,
        notes_root=args.notes_root,
    )
    return _emit(output, quiet=args.quiet, trace_dir=args.trace_dir)


def _emit(output: RunOutput, *, quiet: bool, trace_dir: str) -> int:
    if quiet:
        print(f"{output.status}\t{output.trace_id}\t{output.reason or ''}")
    else:
        print(json.dumps(output.model_dump(mode="json"), indent=2, ensure_ascii=False))
    print(f"# trace: {Path(trace_dir) / (output.trace_id + '.jsonl')}", file=sys.stderr)
    return 0 if output.status in EXIT_OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
