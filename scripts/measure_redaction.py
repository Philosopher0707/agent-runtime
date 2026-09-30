"""Audit what the redaction patterns actually catch in real traces.

The set is deliberately aggressive — a false positive costs a digit, a false negative leaks
data — so the question is not *"does it over-match"* (it is meant to) but **"does it over-match
enough to make a trace useless"**. That is measurable: apply the set to every string in every
real trace and look at what it caught, and at how much content it removed.

**What this does and does not measure.** The corpus is real *runs*, but the content is
synthetic: `example.com` addresses, invented ticket references, generated log entries. So this
is a **false-positive** audit — it says whether the patterns fire on ordinary text, and
ordinary text here carries a lot of numbers and identifiers, which is what a loose pattern
would trip over. It is not a detection test: there is no real personal data in the corpus, so
nothing here measures the false *negative* half of the trade.

    uv run python scripts/measure_redaction.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from runtime.config import load_env_file
from runtime.redact import PATTERNS, Redactor

REPO_ROOT = Path(__file__).resolve().parent.parent
LIVE_TRACES = REPO_ROOT / ".traces" / "live"
FIXTURES = REPO_ROOT / "evals" / "fixtures"

#: How many distinct matches to show per pattern. Enough to judge a pattern by, few enough to read.
SAMPLE_SIZE = 5


def trace_paths() -> list[Path]:
    return sorted(LIVE_TRACES.glob("*.jsonl")) + sorted(FIXTURES.glob("*.jsonl"))


def strings_in(node: Any):
    """Every string in a JSON-shaped payload, keys included — the redactor walks keys too."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from strings_in(value)
    elif isinstance(node, list):
        for value in node:
            yield from strings_in(value)


def main() -> int:
    load_env_file()
    paths = trace_paths()
    if not paths:
        print("no traces found — run `make live` first, or check .traces/live and evals/fixtures")
        return 1

    redactor = Redactor.from_names([pattern.name for pattern in PATTERNS])
    events = changed = 0
    before = after = 0
    hits: Counter[str] = Counter()
    samples: dict[str, Counter[str]] = {pattern.name: Counter() for pattern in PATTERNS}

    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            events += 1
            payload = json.loads(line)["payload"]

            # Counted on the raw payload so the samples are the real matches, not the markers.
            raw_text = json.dumps(payload, ensure_ascii=False)
            for pattern in PATTERNS:
                for match in pattern.pattern.findall(raw_text):
                    hits[pattern.name] += 1
                    samples[pattern.name][str(match)[:60]] += 1

            before += len("".join(strings_in(payload)))
            redacted, counts = redactor.value(payload)
            after += len("".join(strings_in(redacted)))
            if any(counts.as_dict().values()):
                changed += 1

    print(f"traces:                          {len(paths)}")
    print(f"events:                          {events}")
    print(f"events redaction would change:   {changed}  ({changed * 100 // max(events, 1)}%)")
    delta = after - before
    direction = "LONGER" if delta > 0 else "shorter"
    print(f"characters:                      {before} -> {after}")
    print(f"                                 {abs(delta) * 100 / max(before, 1):.3f}% {direction}")
    print("                                 (a marker can exceed the value it replaces)")
    print()
    print("per-pattern matches — a pattern that never fires is either precise or asleep:")
    for pattern in PATTERNS:
        print(f"  {pattern.name:12s} {hits[pattern.name]}")
    print()
    for name, counter in samples.items():
        if not counter:
            continue
        print(f"what `{name}` matched:")
        for value, count in counter.most_common(SAMPLE_SIZE):
            print(f"   x{count:<4d} {value!r}")
    print()
    print("Judge the samples, not the count: a pattern matching ordinary identifiers is the")
    print("over-redaction this audit exists to find, and it is visible only in the matches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
