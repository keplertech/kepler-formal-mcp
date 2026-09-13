"""Fail-closed interpretation of Kepler's SEC result records."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field


class Coverage(BaseModel):
    checked_outputs: int
    existing_outputs: int
    checked_percent: float
    proved_outputs: int | None = None
    skipped_outputs: int
    unproved_outputs: int | None = None


class VerificationResult(BaseModel):
    status: Literal["proved", "partial", "inconclusive", "counterexample", "error"] = "error"
    verification: Literal["sec"] = "sec"
    equivalent: bool | None = None
    blocking: bool = True
    coverage: Coverage | None = None
    engine: str | None = None
    encoding: str | None = None
    bound: int | None = None
    message: str = ""
    warnings: list[str] = Field(default_factory=list)
    exit_code: int | None = None
    timed_out: bool = False
    seconds: float = 0.0
    run_directory: str | None = None
    yaml_config: str | None = None
    generated_log_file: str | None = None
    stdout_log: str | None = None
    stderr_log: str | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    command: list[str] = Field(default_factory=list)
    input_hashes: dict[str, str] = Field(default_factory=dict)
    skipped_reports: dict[str, str] = Field(default_factory=dict)
    result_file: str | None = None


def parse_sec_result(log: str, exit_code: int) -> VerificationResult:
    """Require explicit verdicts and coherent counts, never exit status alone."""
    result = VerificationResult(exit_code=exit_code)
    for field, pattern in (("engine", r"SEC engine: (\w+)"),
                           ("encoding", r"SEC encoding: (\w+)")):
        matches = re.findall(pattern, log)
        if len(set(matches)) == 1:
            setattr(result, field, matches[0])

    def error(message: str) -> VerificationResult:
        result.message = message
        return result

    if re.search(r"SEC cannot run|\[critical\]|\[error\]|proof not run", log, re.I):
        return error("Kepler reported an execution/extraction error or did not run a proof.")

    full = re.findall(r"SEC proved equivalence(?: under the dual-rail steady-state abstraction)? at k = (\d+)\.", log)
    partial = re.findall(r"SEC partially proved equivalence at k = (\d+): (\d+)/(\d+) outputs proved", log)
    different = re.findall(r"SEC found a counterexample at k = (\d+)\.", log)
    inconclusive = re.findall(r"SEC was inconclusive (?:up to|before completing) max_k = (\d+):", log)
    if sum(map(len, (full, partial, different, inconclusive))) != 1:
        return error("Missing, duplicate or contradictory SEC verdicts.")

    counts = re.findall(r"SEC checked-output coverage:\s*([0-9.]+)%\s*\((\d+)/(\d+) covered/existing outputs\)", log)
    if len(counts) != 1:
        return error("Missing or ambiguous SEC checked-output coverage.")
    percent_text, checked_text, total_text = counts[0]
    checked, total = int(checked_text), int(total_text)
    try:
        percent = float(percent_text)
    except ValueError:
        return error("Invalid SEC coverage percentage.")
    if not 0 < checked <= total or abs(percent - 100 * checked / total) > 0.011:
        return error("Invalid or inconsistent SEC coverage counts.")
    result.coverage = Coverage(checked_outputs=checked, existing_outputs=total,
                               checked_percent=percent, skipped_outputs=total - checked)

    expected_code = 0 if full else 1 if partial else 3 if different else 2
    if exit_code != expected_code:
        return error("SEC verdict contradicts the process exit code.")

    progress = re.findall(r"SEC .+? proven outputs: (\d+)/(\d+)", log)
    proved = checked if full else int(partial[0][1]) if partial else None
    if partial and (int(partial[0][2]) != total or not 0 < proved <= checked or proved >= total):
        return error("Invalid partial-proof output counts.")
    if progress:
        unique = set(progress)
        if len(unique) != 1:
            return error("Conflicting SEC proof-progress records.")
        progress_proved, progress_total = map(int, progress[0])
        if (progress_total != total or not 0 <= progress_proved <= checked
                or (proved is not None and progress_proved != proved)):
            return error("SEC proof-progress counts contradict the verdict or coverage.")
        proved = progress_proved
    result.coverage.proved_outputs = proved
    result.coverage.unproved_outputs = total - proved if proved is not None else None
    result.bound = int(full[0] if full else partial[0][0] if partial else different[0] if different else inconclusive[0])
    if different:
        result.status = "counterexample"
        result.equivalent = False
        result.message = "SEC found a definitive behavioral difference. Reject the candidate."
    elif full and checked == total:
        if re.search(r"SEC verification did not prove|SEC skipped observed outputs", log):
            return error("Full-proof verdict conflicts with warnings or skipped-output records.")
        result.status = "proved"
        result.equivalent = True
        result.blocking = False
        result.message = "All observed outputs proved equivalent under the reported SEC assumptions."
    else:
        result.status = "partial" if full or partial else "inconclusive"
        result.blocking = False
        result.message = "No counterexample reported, but full equivalence is not established."
        result.warnings.append("Unproved or skipped outputs remain; this is not a full equivalence proof.")
    if result.encoding == "dual_rail_steady":
        result.warnings.append("Dual-rail steady-state proof compares binary-defined outputs; it does not prove that outputs become defined.")
    return result
