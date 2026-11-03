"""Run LAB's unchanged rubric scorer with a native subscription CLI transport.

Executed in LAB's dependency environment. Neither the SDK Judge constructor nor
the scorer's hidden Anthropic API fallback is used.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

from .subscription import check_auth, invoke


class CliJudge:
    def __init__(self, channel: str, model: str, audit: Path):
        self.channel, self.model, self.audit = channel, model, audit

    def ask(self, prompt: str) -> dict:
        from evaluation.judge import Judge

        key = hashlib.sha256((self.channel + self.model + prompt).encode()).hexdigest()
        audit = self.audit / key
        with tempfile.TemporaryDirectory(prefix="harness2-judge-") as td:
            result = invoke(
                self.channel,
                self.model,
                "Evaluate only the supplied data. Do not use tools.\n\n" + prompt,
                workspace=Path(td),
                audit=audit,
                effort="medium",
                timeout=600,
                edit=False,
            )
        parsed = Judge._parse_json(result["result"])
        if not isinstance(parsed, dict):
            raise ValueError("Judge response must be an object")
        return parsed

    def evaluate_from_file(self, prompt_name: str, variables: dict) -> dict:
        from evaluation.judge import PROMPTS_DIR

        prompt = (PROMPTS_DIR / f"{prompt_name}.txt").read_text().format(**variables)
        result = self.ask(prompt)
        if result.get("verdict") not in ("pass", "fail") or not isinstance(
            result.get("reasoning"), str
        ):
            raise ValueError("Malformed verdict; this run must remain ungraded")
        return result

    def match_deliverables(self, unresolved, available_files, output_dir):
        from evaluation.scoring import _read_file_as_text

        # Same inputs and matching request as LAB's _llm_match_deliverables,
        # using this explicitly configured CLI instead of a hidden API client.
        descriptions = "\n".join(
            f"Deliverable key: {k}\nExpected filename: {v}" for k, v in unresolved.items()
        )
        previews = []
        for filename in available_files:
            path = output_dir / filename
            try:
                content = _read_file_as_text(path)[:500] if path.exists() else "(file not found)"
            except Exception:
                content = "(could not read file)"
            previews.append(f"Filename: {filename}\nPreview: {content}\n")
        prompt = (
            "Match each unresolved deliverable to the most likely output file.\n\n"
            f"## Unresolved Deliverables\n{descriptions}\n\n## Available Output Files\n"
            + "\n".join(previews)
            + "\nReturn a JSON object with every deliverable key mapped to an available filename or null."
        )
        result = self.ask(prompt)
        if set(result) != set(unresolved) or any(
            v is not None and v not in available_files for v in result.values()
        ):
            raise ValueError("Invalid deliverable mapping; refusing to guess")
        return result


def main() -> None:
    from evaluation import scoring
    from evaluation.run_eval import evaluate_run

    run_dir, task, channel, model = sys.argv[1:]
    check_auth(channel)
    judge = CliJudge(channel, model, Path(run_dir) / "judge-calls")
    # This process handles exactly one run; the override cannot affect the improver.
    scoring._llm_match_deliverables = judge.match_deliverables
    scores = evaluate_run(run_dir, task, judge, parallel=2)
    print(json.dumps({k: scores[k] for k in ("n_passed", "n_criteria", "all_pass")}))


if __name__ == "__main__":
    main()
