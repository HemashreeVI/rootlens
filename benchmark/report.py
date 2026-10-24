"""Turn benchmark/results/runs.jsonl into a results table.

    python benchmark/report.py            print the table
    python benchmark/report.py --write    also save it to benchmark/RESULTS.md
"""
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_FILE = os.path.join(HERE, "results", "runs.jsonl")
REPORT_FILE = os.path.join(HERE, "RESULTS.md")


def load(path=RESULTS_FILE):
    with open(path, encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


# ---------- scoring one run ----------

def diagnosis_correct(run):
    """Did the investigator blame the right service, for the right reason?"""
    if not run.get("detected") or run.get("expect_service") is None:
        return None
    return (
        run.get("diagnosed_service") == run["expect_service"]
        and run.get("diagnosed_kind") == run["expect_kind"]
    )


def action_correct(run):
    """Did the controller do what a careful on-call engineer would?"""
    if not run.get("detected") or run.get("control"):
        return None
    actions = run.get("actions") or []
    if run["expect_action"] == "escalate":
        return not actions and bool(run.get("escalated"))
    if not actions:
        return False
    first = actions[0]
    return (
        first["action"] == run["expect_action"]
        and first["service"] == run["expect_service"]
    )


def unsafe_actions(run):
    """Actions that should never have happened: wrong service, or any action
    when the right answer was to hand over to a human."""
    actions = run.get("actions") or []
    if run.get("control") or run.get("expect_action") == "escalate":
        return len(actions)
    return sum(1 for a in actions if a["service"] != run.get("expect_service"))


# ---------- formatting ----------

def ratio(values):
    """'5/6' for a list of True/False/None (None is not counted)."""
    counted = [v for v in values if v is not None]
    if not counted:
        return "—"
    return f"{sum(counted)}/{len(counted)}"


def percent(values):
    counted = [v for v in values if v is not None]
    if not counted:
        return "—"
    return f"{100 * sum(counted) / len(counted):.0f}%"


def median(values):
    numbers = [v for v in values if v is not None]
    if not numbers:
        return "—"
    return f"{statistics.median(numbers):.0f}s"


def scenario_rows(runs):
    names = []
    for run in runs:
        if run["scenario"] not in names:
            names.append(run["scenario"])

    rows = []
    for name in names:
        group = [r for r in runs if r["scenario"] == name]
        first = group[0]
        if first.get("control"):
            expected = "no incident"
        elif first["expect_action"] == "escalate":
            expected = "escalate to a human"
        else:
            expected = (
                f"{first['expect_service']} {first['expect_kind']} → "
                f"{first['expect_action']}"
            )
        rows.append([
            name + (" ⚠" if first.get("known_gap") else ""),
            expected,
            str(len(group)),
            ratio([not r["detected"] for r in group]) if first.get("control")
            else ratio([r["detected"] for r in group]),
            ratio([diagnosis_correct(r) for r in group]),
            ratio([action_correct(r) for r in group]),
            ratio([r.get("auto_resolved") for r in group
                   if r.get("detected") and r.get("expect_action") != "escalate"
                   and not r.get("control")]),
            median([r.get("time_to_detect_s") for r in group]),
            median([r.get("time_to_recover_s") for r in group
                    if r.get("expect_action") != "escalate"]),
        ])
    return rows


def table(header, rows):
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def build_report(runs):
    faults = [r for r in runs if not r.get("control") and not r.get("known_gap")]
    controls = [r for r in runs if r.get("control")]
    gaps = [r for r in runs if r.get("known_gap")]
    recoverable = [r for r in faults if r.get("expect_action") != "escalate"]

    summary = [
        ["Fault runs (excluding known gaps)", str(len(faults))],
        ["Detected", f"{ratio([r['detected'] for r in faults])} "
                     f"({percent([r['detected'] for r in faults])})"],
        ["Right root cause (service + kind)",
         f"{ratio([diagnosis_correct(r) for r in faults])} "
         f"({percent([diagnosis_correct(r) for r in faults])})"],
        ["Right action (incl. escalating when unsure)",
         f"{ratio([action_correct(r) for r in faults])} "
         f"({percent([action_correct(r) for r in faults])})"],
        ["Recovered with no human",
         f"{ratio([r.get('auto_resolved') for r in recoverable])} "
         f"({percent([r.get('auto_resolved') for r in recoverable])})"],
        ["Median time to detect", median([r.get("time_to_detect_s") for r in faults])],
        ["Median time to first action",
         median([r.get("time_to_first_action_s") for r in recoverable])],
        ["Median time to recover (fault → resolved)",
         median([r.get("time_to_recover_s") for r in recoverable])],
        ["Unsafe actions (wrong service, or acting when it should escalate)",
         str(sum(unsafe_actions(r) for r in runs))],
        ["False alarms in no-fault runs",
         f"{sum(1 for r in controls if r['detected'])} of {len(controls)}"],
    ]
    if gaps:
        summary.append([
            "Known gaps caught",
            f"{sum(1 for r in gaps if r['detected'])} of {len(gaps)}",
        ])

    header = ["Scenario", "Expected", "Runs", "Detected", "Right cause",
              "Right action", "Auto-recovered", "Median detect",
              "Median recover"]

    started = min(r["injected_at"] for r in runs)[:16].replace("T", " ")
    finished = max(r.get("finished_at") or r["injected_at"] for r in runs)[:16].replace("T", " ")

    misses = []
    for r in faults:
        problems = []
        if not r["detected"]:
            problems.append("not detected")
        if diagnosis_correct(r) is False:
            problems.append(
                f"blamed {r.get('diagnosed_service')} {r.get('diagnosed_kind')} "
                f"({r.get('confidence')} confidence)"
            )
        if action_correct(r) is False:
            did = ", ".join(f"{a['action']} {a['service']}" for a in r.get("actions") or [])
            problems.append(f"did: {did or 'nothing'}"
                            + (" (escalated)" if r.get("escalated") else ""))
        if problems:
            misses.append(f"- `{r['scenario']}` run {r['repeat']}: " + "; ".join(problems))

    parts = [
        "# RootLens benchmark results",
        "",
        f"{len(runs)} runs, {started} to {finished} UTC. "
        "Generated by `python benchmark/report.py --write` from "
        "`benchmark/results/runs.jsonl`.",
        "",
        "## Summary",
        "",
        table(["Measure", "Result"], summary),
        "",
        "## By scenario",
        "",
        table(header, scenario_rows(runs)),
        "",
        "⚠ = a known gap: RootLens is not expected to catch this yet. "
        "It is listed so the number is visible, and left out of the summary.",
        "",
        "Time to recover is measured from the moment the fault was injected "
        "until the detector closed the incident, so it includes detection "
        "(Prometheus uses a 1-minute window) and 3 healthy checks after the fix.",
        "",
        "## Runs that went wrong",
        "",
        "\n".join(misses) if misses else "None.",
        "",
    ]
    return "\n".join(parts)


def main(argv):
    if not os.path.exists(RESULTS_FILE):
        sys.exit("No results yet. Run python benchmark/run.py first.")
    report = build_report(load())
    print(report)
    if "--write" in argv:
        with open(REPORT_FILE, "w", encoding="utf-8", newline="\n") as file:
            file.write(report)
        print(f"Saved {os.path.relpath(REPORT_FILE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
