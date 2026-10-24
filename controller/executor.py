"""The executor: the only part of RootLens that changes the running system.

It can do exactly two things, and only to allowlisted services:
  - rollback_deployment: switch a service back to an earlier version
  - restart_service:     restart a service's container

It does not decide anything. policy.py decides, safety.py checks, this file acts.

Every result has an "outcome" that starts with one of:
  "Refused"           nothing was run (does NOT count as an attempt)
  "Executed"          the action ran (counts as an attempt)
  "Execution failed"  the action was tried but broke (counts as an attempt)

Manual use, from inside the controller container:
  python executor.py rollback payment-service v1
  python executor.py restart payment-service
"""
import os
import subprocess
import sys
import time

import httpx

import topology
from policy import is_allowed, parse_version

PROJECT_DIR = os.getenv("PROJECT_DIR", "/project")
ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://order-service:8000")

COMMAND_TIMEOUT_SECONDS = 120
READY_TIMEOUT_SECONDS = 30

# The .env key that pins each service's release, from services.yaml.
VERSION_KEYS = {
    name: info["version_env"]
    for name, info in topology.services().items()
    if info.get("version_env")
}


# ---------- pure helpers (easy to test) ----------

def check_request(action, service, target_version=None):
    """Return None if the request is acceptable, otherwise the reason it is not."""
    if not is_allowed(action, service):
        return f"{action} on {service} is not allowed."
    if action == "rollback_deployment":
        if not target_version or parse_version(target_version) is None:
            return f"{target_version!r} is not a valid version."
    return None


def build_command(action, service):
    """The exact command to run. A list, never a shell string."""
    if action == "rollback_deployment":
        return ["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", service]
    if action == "restart_service":
        return ["docker", "compose", "restart", service]
    raise ValueError(f"Unknown action: {action}")


def update_env_text(text, key, version):
    """Set KEY=version in the .env text and keep every other line as it was."""
    lines = [line.rstrip("\r") for line in text.splitlines()]
    kept = [line for line in lines if not line.startswith(f"{key}=")]
    kept.append(f"{key}={version}")
    return "\n".join(kept) + "\n"


def _refused(reason):
    return {"ok": False, "outcome": f"Refused: {reason}"}


def _failed(reason):
    return {"ok": False, "outcome": f"Execution failed: {reason}"}


def _executed(summary):
    return {"ok": True, "outcome": f"Executed: {summary}"}


def _last_line(text):
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return lines[-1].strip() if lines else "no error output"


# ---------- talking to the live system ----------

def read_version(service):
    """The version a service reports right now, or None if it cannot be reached."""
    try:
        response = httpx.get(f"http://{service}:8000/version", timeout=3.0)
        return response.json().get("version")
    except Exception:
        return None


def record_deployment(service, version, previous_version):
    httpx.post(
        f"{ORDER_SERVICE_URL}/deployments",
        json={
            "service": service,
            "version": version,
            "previous_version": previous_version,
            "deployed_by": "rootlens-controller",
        },
        timeout=5.0,
    )


def run_command(command, project_dir):
    return subprocess.run(
        command,
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )


def _wait(probe, service, wanted, timeout):
    """Wait until probe(service) returns `wanted` (or anything, if wanted is None)."""
    deadline = time.time() + timeout
    while True:
        seen = probe(service)
        if (wanted is None and seen is not None) or (wanted is not None and seen == wanted):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(1)


# ---------- the actions ----------

def _rollback(service, target, project_dir, runner, probe, recorder, ready_timeout):
    previous = probe(service)
    if previous == target:
        return _refused(f"{service} is already running {target}.")

    env_path = os.path.join(project_dir, ".env")
    key = VERSION_KEYS[service]

    try:
        old_text = ""
        if os.path.exists(env_path):
            with open(env_path, encoding="utf-8") as file:
                old_text = file.read()
        with open(env_path, "w", encoding="utf-8", newline="\n") as file:
            file.write(update_env_text(old_text, key, target))
    except OSError as error:
        return _refused(f"could not update the release manifest (.env): {error}")

    command = build_command("rollback_deployment", service)
    try:
        result = runner(command, project_dir)
    except Exception as error:
        result = None
        problem = str(error)
    else:
        problem = None if result.returncode == 0 else (
            f"docker compose exited with code {result.returncode}: "
            f"{_last_line(result.stderr)}"
        )

    if problem:
        # Put the manifest back so it still matches what is running.
        with open(env_path, "w", encoding="utf-8", newline="\n") as file:
            file.write(old_text)
        return _failed(problem)

    summary = f"rolled back {service} from {previous or 'unknown'} to {target}"

    if not _wait(probe, service, target, ready_timeout):
        summary += f" (warning: it did not report {target} within {ready_timeout}s)"

    try:
        recorder(service, target, previous or "none")
    except Exception as error:
        summary += f" (warning: the deployment was not recorded: {error})"

    return _executed(summary)


def _restart(service, project_dir, runner, probe, ready_timeout):
    command = build_command("restart_service", service)
    try:
        result = runner(command, project_dir)
    except Exception as error:
        return _failed(str(error))

    if result.returncode != 0:
        return _failed(
            f"docker compose exited with code {result.returncode}: "
            f"{_last_line(result.stderr)}"
        )

    summary = f"restarted {service}"
    if not _wait(probe, service, None, ready_timeout):
        summary += f" (warning: it did not answer within {ready_timeout}s)"
    return _executed(summary)


def execute(
    decision,
    project_dir=PROJECT_DIR,
    runner=run_command,
    probe=read_version,
    recorder=record_deployment,
    ready_timeout=READY_TIMEOUT_SECONDS,
):
    """Carry out one decision from policy.decide. Returns {"ok", "outcome"}."""
    action = decision.get("action")
    service = decision.get("service")
    target = decision.get("target_version")

    # Check the allowlist again here, even though the policy already did.
    problem = check_request(action, service, target)
    if problem:
        return _refused(problem)

    if action == "rollback_deployment":
        return _rollback(service, target, project_dir, runner, probe, recorder, ready_timeout)
    return _restart(service, project_dir, runner, probe, ready_timeout)


# ---------- manual use ----------

def _main(args):
    names = {"rollback": "rollback_deployment", "restart": "restart_service"}
    if len(args) < 2 or args[0] not in names:
        print("Usage: python executor.py rollback <service> <version>")
        print("       python executor.py restart <service>")
        return 2

    decision = {
        "action": names[args[0]],
        "service": args[1],
        "target_version": args[2] if len(args) > 2 else None,
    }
    print(f"Running {decision['action']} on {decision['service']}...", flush=True)
    result = execute(decision)
    print(result["outcome"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))