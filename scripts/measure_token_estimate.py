"""Measure the token estimate against what a real endpoint actually counts.

`chars / 4` decides when context is summarised or dropped
([decisions/0005](../../docs/decisions/0005-token-estimation.md)), and the stub cannot check it:
`StubProvider` computes `prompt_tokens` with the **same** `estimate_tokens` the assembler uses, so
estimate == actual by construction and the golden set is self-consistent by definition. Only a real
endpoint gives an independent count.

Two measurements, because they answer different halves:

* **corpus** — pairs each step's `context.estimated_tokens` with the following
  `model_call.prompt_tokens`, over every real trace on disk. Free, no key, and it measures the
  estimate *as it is actually used*: prose plus tool schemas plus JSON envelopes.
* **scripts** — sends the same content in English, Devanagari and Chinese at two lengths each and
  reads what the API counted. Two lengths so the fixed per-message overhead cancels in the
  difference. Needs `AGENT_API_KEY`.

**The direction matters more than the size.** `actual / estimate > 1` means the runtime
*under*-estimates, so the soft threshold fires late and the hard ceiling can be passed before
anything notices. `< 1` means it over-estimates, which summarises earlier than strictly needed —
wasteful, not dangerous.

    uv run python scripts/measure_token_estimate.py            # corpus only, no key needed
    uv run python scripts/measure_token_estimate.py --scripts  # adds the live script probe
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIVE_TRACES = REPO_ROOT / ".traces" / "live"
FIXTURES = REPO_ROOT / "evals" / "fixtures"

#: Fixed per-message overhead is cancelled by taking a difference, so these only need to be
#: different lengths of the same content — not round numbers.
SHORT_CHARS = 600
LONG_CHARS = 1200

ENGLISH = [
    "The support team replies within twenty-four hours on business days.",
    "A request opened on Friday is answered on the following Monday.",
    "The service targets an availability of ninety-nine point nine percent.",
    "Failed requests are retried automatically up to three times.",
]
DEVANAGARI = [
    "सहायता टीम कार्य दिवसों में चौबीस घंटे के भीतर उत्तर देती है।",
    "शुक्रवार को खोला गया अनुरोध अगले सोमवार को उत्तरित होता है।",
    "सेवा उन्नीस सौ निन्यानवे दशमलव नौ प्रतिशत उपलब्धता का लक्ष्य रखती है।",
    "विफल अनुरोधों को स्वचालित रूप से तीन बार तक पुनः प्रयास किया जाता है।",
]
CHINESE = [
    "支持团队在工作日内二十四小时之内回复。",
    "周五提交的请求将在下周一得到答复。",
    "该服务的目标可用性为百分之九十九点九。",
    "失败的请求会自动重试最多三次。",
]


def trace_paths() -> list[Path]:
    return sorted(LIVE_TRACES.glob("*.jsonl")) + sorted(FIXTURES.glob("*.jsonl"))


def paired_calls() -> list[tuple[str, int, int]]:
    """`(trace, estimated_tokens, prompt_tokens)` for every step in every real trace."""
    pairs: list[tuple[str, int, int]] = []
    for path in trace_paths():
        pending: tuple[int, int] | None = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            payload = event["payload"]
            if event.get("event") == "context":
                pending = (payload.get("step"), payload.get("estimated_tokens"))
            elif event.get("event") == "model_call" and pending is not None:
                step, estimate = pending
                if step == payload.get("step"):
                    pairs.append((path.name, estimate, payload.get("prompt_tokens")))
                pending = None
    return pairs


def report_corpus() -> None:
    pairs = paired_calls()
    if not pairs:
        print("no paired calls found — is there anything in .traces/live or evals/fixtures?")
        return
    ratios = [actual / estimate for _, estimate, actual in pairs if estimate]
    under = [row for row in pairs if row[2] > row[1]]
    print(f"traces:                {len(trace_paths())}")
    print(f"paired calls:          {len(pairs)}")
    print(
        f"actual/estimate:       min={min(ratios):.2f}  median={statistics.median(ratios):.2f}  "
        f"max={max(ratios):.2f}"
    )
    print(f"under-estimated:       {len(under)} of {len(pairs)}  (the dangerous direction)")
    largest = max(pairs, key=lambda row: row[2])
    print(f"largest prompt:        estimate={largest[1]} actual={largest[2]}  ({largest[0]})")


def repeat_to(lines: list[str], target: int) -> str:
    out, index = "", 0
    while len(out) < target:
        out += lines[index % len(lines)]
        index += 1
    return out


def report_scripts() -> int:
    from runtime.config import load_config_by_name, load_env_file, read_api_key
    from runtime.factory import build_provider

    load_env_file()
    config = load_config_by_name("openai_compat", root=REPO_ROOT / "configs")
    if not read_api_key(config.provider):
        print(
            f"\nscript probe skipped: {config.provider.api_key_env} is not set. "
            f"Put it in .env, or run without --scripts.",
            file=sys.stderr,
        )
        return 1
    provider = build_provider(config.provider)

    print(f"\n{'script':12s} {'chars/token':>11s} {'actual vs /4':>13s}   meaning")
    for name, lines in (("english", ENGLISH), ("devanagari", DEVANAGARI), ("chinese", CHINESE)):
        small = repeat_to(lines, SHORT_CHARS)
        large = repeat_to(lines, LONG_CHARS)
        try:
            small_tokens = provider.complete([{"role": "user", "content": small}], []).prompt_tokens
            large_tokens = provider.complete([{"role": "user", "content": large}], []).prompt_tokens
        except Exception as exc:
            print(f"{name:12s} FAILED: {type(exc).__name__}: {exc}")
            continue
        delta_chars = len(large) - len(small)
        delta_tokens = large_tokens - small_tokens
        per_token = delta_chars / delta_tokens if delta_tokens else float("nan")
        ratio = delta_tokens / (delta_chars / 4) if delta_chars else float("nan")
        verdict = "UNDER-estimates" if ratio > 1 else "over-estimates"
        print(f"{name:12s} {per_token:11.2f} {ratio:13.2f}   {verdict}")
        time.sleep(0.5)
    print("\n> 1 under-estimates (the soft threshold fires late, the hard ceiling can be passed);")
    print("< 1 over-estimates (summarises earlier than needed).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure the token estimate against a real endpoint."
    )
    parser.add_argument(
        "--scripts", action="store_true", help="also probe non-Latin scripts (needs a key)"
    )
    args = parser.parse_args(argv)

    print("=== corpus: the estimate as it is actually used ===")
    report_corpus()
    if args.scripts:
        return report_scripts()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
