"""Ask the hosted-demo supervisor (demo/run.py) to recreate or restart a service.

The executor runs this instead of `docker compose` when EXECUTOR_RUNTIME=process:
    python demo/ctl.py recreate payment-service   (picks up the version in .env)
    python demo/ctl.py restart  payment-service

It stops the service's process; the supervisor starts it again straight away,
reading the version from the release manifest (.env). Exit code 0 once the new
process is running.
"""
import os
import signal
import sys
import time

STATE = os.getenv("DEMO_STATE", "/tmp/rootlens-demo")
ALLOWED = {"user-service", "payment-service"}


def read_pid(service):
    try:
        with open(os.path.join(STATE, "pids", service + ".pid"), encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def main(args):
    if len(args) != 2 or args[0] not in ("recreate", "restart") or args[1] not in ALLOWED:
        print("usage: ctl.py recreate|restart user-service|payment-service", file=sys.stderr)
        return 2
    service = args[1]
    old = read_pid(service)
    if old is None:
        print(f"{service} is not running under the supervisor", file=sys.stderr)
        return 1
    try:
        os.kill(old, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.time() + 30
    while time.time() < deadline:
        new = read_pid(service)
        if new and new != old:
            print(f"{args[0]}d {service} (pid {old} -> {new})")
            return 0
        time.sleep(0.5)
    print(f"{service} did not come back within 30 s", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
