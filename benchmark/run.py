"""The RootLens benchmark: break the running system on purpose and score RootLens.

For every scenario it:
  1. waits until the system is healthy (versions v1, no chaos, no open incident),
  2. injects one known fault (errors, slowness, a stopped container, a bad
     release, two failures at once, or nothing at all),
  3. records when RootLens detected it, what the investigator blamed, what the
     controller did, and when the incident resolved,
  4. puts everything back.

Each run is appended as one JSON line to benchmark/results/runs.jsonl, so a long
benchmark can be stopped and continued. Turn the results into a table with:

    python benchmark/report.py

Needs the stack running (docker compose up -d). The benchmark switches the
controller to enforce mode for its own run and puts .env back afterwards.

    python benchmark/run.py --list
    python benchmark/run.py --repeat 3
    python benchmark/run.py --only payment_errors,payment_bad_release
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_FILE = os.path.join(ROOT, "benchmark", "results", "runs.jsonl")
ENV_FILE = os.path.join(ROOT, ".env")

ORDER = "http://localhost:8002"
PROMETHEUS = "http://localhost:9090"
PORTS = {"user-service": 8001, "payment-service": 8003}
VERSION_KEYS = {
    "user-service": "USER_SERVICE_VERSION",
    "payment-service": "PAYMENT_SERVICE_VERSION",
}
GOOD_VERSION = "v1"

# Settings the benchmark needs, written to .env for the duration of the run.
BENCHMARK_ENV = {
    "CONTROLLER_MODE": "enforce",
    # The normal limit is 3 actions per service per hour; a benchmark runs
    # far more incidents than that on purpose.
    "CONTROLLER_MAX_ACTIONS_PER_SERVICE_PER_HOUR": "1000",
}

DETECT_TIMEOUT = 240      # give up waiting for an incident after this long
RESOLVE_TIMEOUT = 360     # give up waiting for recovery after this long
HEALTHY_TIMEOUT = 420     # longest wait for a clean starting point
CONTROL_SECONDS = 240     # how long the no-fault control run watches
SETTLE_SECONDS = 45       # after a decoy deploy, let the restart blip clear

# "or vector(0)": before the first failure there is no 5xx series at all,
# which should read as 0, not as "no data".
FAILURE_RATIO = (
    '(sum(rate(rootlens_order_requests_total{endpoint="/orders",status=~"5.."}[1m]))'
    ' or vector(0))'
    ' / sum(rate(rootlens_order_requests_total{endpoint="/orders"}[1m]))'
)


# ---------- small helpers ----------

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def seconds_between(start_iso, end_iso):
    if not start_iso or not end_iso:
        return None
    start = datetime.fromisoformat(start_iso)
    end = datetime.fromisoformat(end_iso)
    return round((end - start).total_seconds(), 1)


def log(message):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def http(method, url, body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"null")


def try_http(method, url, body=None, timeout=10):
    try:
        return http(method, url, body, timeout)
    except Exception:
        return None


def reachable(url):
    try:
        urllib.request.urlopen(url, timeout=5).read()
        return True
    except Exception:
        return False


def compose(*args):
    result = subprocess.run(
        ["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def read_env():
    if not os.path.exists(ENV_FILE):
        return None
    with open(ENV_FILE, encoding="utf-8") as file:
        return file.read()


def write_env(text):
    if text is None:
        if os.path.exists(ENV_FILE):
            os.remove(ENV_FILE)
        return
    with open(ENV_FILE, "w", encoding="utf-8", newline="\n") as file:
        file.write(text)


def set_env_values(values):
    """Set KEY=value lines in .env, keeping every other line."""
    lines = (read_env() or "").splitlines()
    kept = [line for line in lines if line.split("=", 1)[0] not in values]
    kept.extend(f"{key}={value}" for key, value in values.items())
    write_env("\n".join(kept) + "\n")


# ---------- reading the system ----------

def failure_ratio():
    query = urllib.parse.urlencode({"query": FAILURE_RATIO})
    data = try_http("GET", f"{PROMETHEUS}/api/v1/query?{query}")
    try:
        value = float(data["data"]["result"][0]["value"][1])
    except (TypeError, KeyError, IndexError, ValueError):
        return None
    return None if value != value else value


def incidents():
    data = try_http("GET", f"{ORDER}/incidents") or {}
    return [i for i in data.get("incidents", []) if i.get("fingerprint")]


def open_incidents():
    return [i for i in incidents() if i.get("status") == "open"]


def find_incident(incident_id):
    for incident in incidents():
        if incident["incident_id"] == incident_id:
            return incident
    return None


def live_version(service):
    data = try_http("GET", f"http://localhost:{PORTS[service]}/version", timeout=3)
    return (data or {}).get("version")


def audit_for(incident_id):
    query = urllib.parse.urlencode({"incident_id": incident_id, "limit": 200})
    data = try_http("GET", f"{ORDER}/audit?{query}") or {}
    return sorted(data.get("entries", []), key=lambda e: e["logged_at"])


# ---------- changing the system ----------

def chaos(service, kind, value=None):
    url = f"http://localhost:{PORTS[service]}/chaos/{kind}"
    if value is not None:
        url += "?" + urllib.parse.urlencode(
            {"rate" if kind == "errors" else "seconds": value}
        )
    http("POST", url)


def deploy(service, version, deployed_by="benchmark"):
    """Same steps as deploy.ps1: update .env, recreate, wait, record."""
    previous = live_version(service) or "none"
    if previous == version:
        return
    set_env_values({VERSION_KEYS[service]: version})
    compose("up", "-d", "--no-deps", "--force-recreate", service)
    deadline = time.time() + 30
    while time.time() < deadline and live_version(service) != version:
        time.sleep(1)
    http("POST", f"{ORDER}/deployments", {
        "service": service,
        "version": version,
        "previous_version": previous,
        "deployed_by": deployed_by,
    })


def stop(service):
    compose("stop", service)


# ---------- traffic ----------

class Traffic(threading.Thread):
    """Fake customers: about 5 orders a second, like traffic.py."""

    def __init__(self):
        super().__init__(daemon=True)
        self.running = True

    def run(self):
        while self.running:
            try:
                urllib.request.urlopen(f"{ORDER}/orders", timeout=10).read()
            except Exception:
                pass
            time.sleep(0.2)


# ---------- scenarios ----------
#
# expect_service / expect_kind: what the investigator should blame.
# expect_action: what the controller should do ("escalate" = act on nothing).
# known_gap: a fault RootLens is known not to catch yet; reported separately.

SCENARIOS = {
    "payment_errors": {
        "description": "payment-service fails 50% of requests",
        "inject": lambda: chaos("payment-service", "errors", 0.5),
        "expect_service": "payment-service", "expect_kind": "errors",
        "expect_action": "restart_service",
    },
    "payment_slow": {
        "description": "payment-service takes 4 s (Order gives up at 3 s)",
        "inject": lambda: chaos("payment-service", "latency", 4),
        "expect_service": "payment-service", "expect_kind": "slow",
        "expect_action": "restart_service",
    },
    "payment_down": {
        "description": "payment-service container is stopped",
        "inject": lambda: stop("payment-service"),
        "expect_service": "payment-service", "expect_kind": "down",
        "expect_action": "restart_service",
    },
    "user_errors": {
        "description": "user-service fails 50% of requests",
        "inject": lambda: chaos("user-service", "errors", 0.5),
        "expect_service": "user-service", "expect_kind": "errors",
        "expect_action": "restart_service",
    },
    "user_slow": {
        "description": "user-service takes 4 s (Order gives up at 3 s)",
        "inject": lambda: chaos("user-service", "latency", 4),
        "expect_service": "user-service", "expect_kind": "slow",
        "expect_action": "restart_service",
    },
    "user_down": {
        "description": "user-service container is stopped",
        "inject": lambda: stop("user-service"),
        "expect_service": "user-service", "expect_kind": "down",
        "expect_action": "restart_service",
    },
    "payment_bad_release": {
        "description": "payment-service v2 is deployed (planted 50% error bug)",
        "inject": lambda: deploy("payment-service", "v2"),
        "expect_service": "payment-service", "expect_kind": "errors",
        "expect_action": "rollback_deployment",
    },
    "user_bad_release": {
        "description": "user-service v2 is deployed (planted 4 s latency bug)",
        "inject": lambda: deploy("user-service", "v2"),
        "expect_service": "user-service", "expect_kind": "slow",
        "expect_action": "rollback_deployment",
    },
    "decoy_release": {
        "description": "a healthy user-service v3 ships, then payment-service "
                       "starts failing: the release must not be blamed",
        "prepare": lambda: deploy("user-service", "v3"),
        "inject": lambda: chaos("payment-service", "errors", 0.5),
        "expect_service": "payment-service", "expect_kind": "errors",
        "expect_action": "restart_service",
    },
    "two_failures": {
        "description": "user-service and payment-service both fail 50%: "
                       "too uncertain, must escalate",
        "inject": lambda: (chaos("user-service", "errors", 0.5),
                           chaos("payment-service", "errors", 0.5)),
        "expect_service": None, "expect_kind": None,
        "expect_action": "escalate",
    },
    "no_fault": {
        "description": "nothing is broken: no incident should open",
        "inject": lambda: None,
        "control": True,
    },
    "slow_under_timeout": {
        "description": "payment-service takes 2 s: slower, but under the 3 s "
                       "timeout",
        "inject": lambda: chaos("payment-service", "latency", 2),
        "expect_service": "payment-service", "expect_kind": "slow",
        "expect_action": "restart_service",
        "known_gap": True,
    },
}


# ---------- getting back to a clean start ----------

def reset():
    """Undo any fault: chaos off, every container running, versions v1."""
    for service in PORTS:
        set_env_values({VERSION_KEYS[service]: GOOD_VERSION})
    compose("up", "-d", "--no-deps", *PORTS)

    for service in PORTS:
        deadline = time.time() + 30
        while time.time() < deadline and live_version(service) is None:
            time.sleep(1)
        try_http("POST", f"http://localhost:{PORTS[service]}/chaos/reset")

    # If a version changed under the controller's feet, keep history honest.
    for service in PORTS:
        version = live_version(service)
        deployments = (try_http("GET", f"{ORDER}/deployments") or {}).get("deployments", [])
        recorded = next((d for d in deployments if d["service"] == service), None)
        if recorded and recorded["version"] != version and version:
            try_http("POST", f"{ORDER}/deployments", {
                "service": service, "version": version,
                "previous_version": recorded["version"],
                "deployed_by": "benchmark-cleanup",
            })


def wait_until_healthy():
    """No open incident and a low failure ratio, so the next run starts clean."""
    deadline = time.time() + HEALTHY_TIMEOUT
    while time.time() < deadline:
        ratio = failure_ratio()
        if not open_incidents() and ratio is not None and ratio < 0.02:
            return True
        time.sleep(5)
    return False


# ---------- one run ----------

def run_scenario(name, scenario, repeat):
    log(f"--- {name} (run {repeat}): {scenario['description']}")
    reset()
    if not wait_until_healthy():
        log("system did not become healthy; skipping this run")
        return None

    if scenario.get("prepare"):
        scenario["prepare"]()
        time.sleep(SETTLE_SECONDS)

    before = {i["incident_id"] for i in incidents()}
    injected_at = now_iso()
    scenario["inject"]()
    log("fault injected")

    record = {
        "scenario": name,
        "repeat": repeat,
        "injected_at": injected_at,
        "control": bool(scenario.get("control")),
        "known_gap": bool(scenario.get("known_gap")),
        "expect_service": scenario.get("expect_service"),
        "expect_kind": scenario.get("expect_kind"),
        "expect_action": scenario.get("expect_action"),
    }

    # 1. Detection.
    wait = CONTROL_SECONDS if scenario.get("control") else DETECT_TIMEOUT
    incident = None
    deadline = time.time() + wait
    while time.time() < deadline and incident is None:
        new = [i for i in incidents() if i["incident_id"] not in before]
        incident = new[0] if new else None
        if incident is None:
            time.sleep(2)

    record["detected"] = incident is not None
    if incident is None:
        log("no incident opened")
        record["finished_at"] = now_iso()
        return record

    incident_id = incident["incident_id"]
    record["incident_id"] = incident_id
    record["detected_at"] = incident["detected_at"]
    record["time_to_detect_s"] = seconds_between(injected_at, incident["detected_at"])
    log(f"incident {incident_id[:8]} opened after {record['time_to_detect_s']}s")

    # 2. Diagnosis (what the controller saw when it decided).
    diagnosis = try_http("GET", f"{ORDER}/investigate/{incident_id}") or {}
    record["diagnosed_service"] = diagnosis.get("root_cause_service")
    record["diagnosed_kind"] = diagnosis.get("cause_kind")
    record["confidence"] = diagnosis.get("confidence")
    record["likely_cause"] = diagnosis.get("likely_cause")

    # 3. Recovery, by RootLens on its own.
    deadline = time.time() + RESOLVE_TIMEOUT
    escalated = False
    while time.time() < deadline:
        current = find_incident(incident_id) or {}
        if current.get("status") == "resolved":
            record["resolved_at"] = current.get("resolved_at")
            break
        entries = audit_for(incident_id)
        executed = [e for e in entries if (e.get("outcome") or "").startswith("Executed")]
        if not executed and any(e["decision"] == "escalate" for e in entries):
            # Escalated with nothing done: give it a little longer, then stop
            # waiting, because only a human would fix it now.
            if not escalated:
                escalated = True
                deadline = min(deadline, time.time() + 60)
        time.sleep(3)

    # 4. What the controller actually did.
    entries = audit_for(incident_id)
    executed = [e for e in entries if (e.get("outcome") or "").startswith("Executed")]
    record["decisions"] = [
        {"decision": e["decision"], "action": e.get("action"),
         "service": e.get("service"), "outcome": e.get("outcome"),
         "logged_at": e["logged_at"]}
        for e in entries
    ]
    record["actions"] = [
        {"action": e["action"], "service": e["service"],
         "target_version": e.get("target_version")}
        for e in executed
    ]
    record["escalated"] = any(e["decision"] == "escalate" for e in entries)
    if executed:
        record["time_to_first_action_s"] = seconds_between(
            injected_at, executed[0]["logged_at"]
        )
    record["auto_resolved"] = bool(record.get("resolved_at"))
    record["time_to_recover_s"] = seconds_between(injected_at, record.get("resolved_at"))
    record["finished_at"] = now_iso()

    log(
        f"actions={[(a['action'], a['service']) for a in record['actions']]} "
        f"escalated={record['escalated']} "
        f"recovered_in={record['time_to_recover_s']}s"
    )
    return record


def save(record):
    os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
    with open(RESULTS_FILE, "a", encoding="utf-8") as file:
        file.write(json.dumps(record) + "\n")


def check_stack():
    if not reachable(f"{ORDER}/health"):
        sys.exit("Order Service is not answering on localhost:8002. "
                 "Start the stack first: docker compose up -d")
    if not reachable(f"{PROMETHEUS}/-/ready"):
        sys.exit("Prometheus is not answering on localhost:9090.")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repeat", type=int, default=3,
                        help="runs per scenario (default 3)")
    parser.add_argument("--only", default="",
                        help="comma-separated scenario names")
    parser.add_argument("--list", action="store_true",
                        help="list the scenarios and exit")
    args = parser.parse_args(argv)

    if args.list:
        for name, scenario in SCENARIOS.items():
            print(f"{name:22} {scenario['description']}")
        return 0

    names = [n for n in args.only.split(",") if n] or list(SCENARIOS)
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        sys.exit(f"Unknown scenario(s): {', '.join(unknown)}")

    check_stack()

    original_env = read_env()
    set_env_values(BENCHMARK_ENV)
    compose("up", "-d", "controller")
    log("controller switched to enforce mode for the benchmark")

    traffic = Traffic()
    traffic.start()

    try:
        # Round-robin, so each scenario runs after a different one each time.
        for repeat in range(1, args.repeat + 1):
            for name in names:
                record = run_scenario(name, SCENARIOS[name], repeat)
                if record:
                    save(record)
    except KeyboardInterrupt:
        log("stopped; finished runs are saved")
    finally:
        traffic.running = False
        reset()
        write_env(original_env)
        set_env_values({VERSION_KEYS[s]: GOOD_VERSION for s in PORTS})
        compose("up", "-d", "controller")
        log(f"done. Results: {os.path.relpath(RESULTS_FILE, ROOT)}. "
            "Run python benchmark/report.py for the table.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
