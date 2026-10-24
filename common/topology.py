"""Reads services.yaml: which app RootLens watches.

Shared by the detector, the investigator, the controller and the dashboard, so
none of them hard-codes service names, metrics or who calls whom.

The file is found at $ROOTLENS_CONFIG, else next to this file (inside a
container), else at the project root (when running from a checkout).
"""
import os
import re

import yaml

# Actions RootLens has code for. services.yaml can only choose from these,
# so editing the config can narrow what the robot may do, never widen it.
KNOWN_ACTIONS = {"rollback_deployment", "restart_service"}

ENTRY_KEYS = ("service", "requests", "errors", "latency_buckets",
              "timeout_seconds")
SERVICE_KEYS = ("requests", "errors")
LATENCY_UNITS = {"seconds": 1, "milliseconds": 1000}

# A Docker container name, and nothing that could be read as an option.
CONTAINER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

_HERE = os.path.dirname(os.path.abspath(__file__))


class ConfigError(ValueError):
    pass


def config_path():
    candidates = [
        os.getenv("ROOTLENS_CONFIG"),
        os.path.join(_HERE, "services.yaml"),
        os.path.join(os.path.dirname(_HERE), "services.yaml"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    raise ConfigError("services.yaml not found (set ROOTLENS_CONFIG).")


def validate(raw):
    """Check the parsed file and fill in defaults. Returns the topology dict."""
    if not isinstance(raw, dict):
        raise ConfigError("services.yaml must be a mapping.")

    entry = raw.get("entry") or {}
    missing = [key for key in ENTRY_KEYS if key not in entry]
    if missing:
        raise ConfigError(f"entry is missing: {', '.join(missing)}")
    entry.setdefault("latency_unit", "seconds")
    if entry["latency_unit"] not in LATENCY_UNITS:
        raise ConfigError(
            f"latency_unit must be one of {sorted(LATENCY_UNITS)}."
        )

    services = raw.get("services") or {}
    if not services:
        raise ConfigError("services must list at least one service.")

    for name, info in services.items():
        info = services[name] = dict(info or {})
        missing = [key for key in SERVICE_KEYS if key not in info]
        if missing:
            raise ConfigError(f"{name} is missing: {', '.join(missing)}")

        actions = info.setdefault("actions", [])
        unknown = set(actions) - KNOWN_ACTIONS
        if unknown:
            raise ConfigError(f"{name} lists unknown actions: {sorted(unknown)}")
        if "rollback_deployment" in actions and not info.get("version_env"):
            raise ConfigError(f"{name} allows rollback but has no version_env.")
        container = info.get("container")
        if container is not None and not CONTAINER_NAME.match(str(container)):
            raise ConfigError(f"{name} has an invalid container name: {container!r}")

    known = set(services) | {entry["service"]}
    dependencies = raw.get("dependencies") or {}
    for caller, callees in dependencies.items():
        for name in [caller, *callees]:
            if name not in known:
                raise ConfigError(f"dependencies mention unknown service {name}.")

    return {
        "window": str(raw.get("window") or "1m"),
        "entry": entry,
        "services": services,
        "dependencies": dependencies,
    }


def load(path=None):
    with open(path or config_path(), encoding="utf-8") as file:
        return validate(yaml.safe_load(file))


_cached = None


def get():
    """The topology, read once per process."""
    global _cached
    if _cached is None:
        _cached = load()
    return _cached


# ---------- questions the other modules ask ----------

def entry(topology=None):
    return (topology or get())["entry"]


def services(topology=None):
    return (topology or get())["services"]


def dependency_graph(topology=None):
    return (topology or get())["dependencies"]


def allowed_actions(service, topology=None):
    """The actions the robot may take on this service (empty if none)."""
    info = services(topology).get(service) or {}
    return set(info.get("actions") or []) & KNOWN_ACTIONS


def controllable(topology=None):
    """Services the robot may act on at all."""
    return {name for name in services(topology) if allowed_actions(name, topology)}


# ---------- the Prometheus queries, built from the selectors ----------

def _rate(selector, window):
    return "sum(rate(" + selector + "[" + window + "]))"


def _with_label(selector, matcher):
    """Add a label matcher to a selector: m{a="1"} + b="2" -> m{a="1",b="2"}."""
    if selector.endswith("}"):
        return selector[:-1] + "," + matcher + "}"
    return selector + "{" + matcher + "}"


def entry_queries(topology=None):
    """Queries about the entry service: failure ratio, rate, p95, statuses."""
    t = topology or get()
    e, w = t["entry"], t["window"]

    p95 = (
        "histogram_quantile(0.95, sum by (le) "
        "(rate(" + e["latency_buckets"] + "[" + w + "])))"
    )
    divisor = LATENCY_UNITS[e["latency_unit"]]
    if divisor != 1:
        p95 = "(" + p95 + ") / " + str(divisor)

    queries = {
        "failure_ratio": _rate(e["errors"], w) + " / " + _rate(e["requests"], w),
        "request_rate": _rate(e["requests"], w),
        "p95_seconds": p95,
    }
    if e.get("status_label"):
        queries["status_rates"] = (
            "sum by (" + e["status_label"] + ") "
            "(rate(" + e["requests"] + "[" + w + "]))"
        )
    return queries


def service_queries(name, topology=None):
    """Queries about one service behind the entry. Optional ones may be missing."""
    t = topology or get()
    info, w = t["services"][name], t["window"]

    queries = {
        "error_ratio": _rate(info["errors"], w) + " / " + _rate(info["requests"], w),
    }
    if info.get("up"):
        queries["up"] = info["up"]

    failures = info.get("caller_failures")
    label = info.get("caller_failure_kind_label")
    if failures and label:
        queries["caller_failures_by_kind"] = (
            "sum by (" + label + ") (rate(" + failures + "[" + w + "]))"
        )
        queries["caller_timeouts"] = _rate(
            _with_label(failures, label + '="timeout"'), w
        )
    elif failures:
        # No label to split by: count every failed call as an error.
        queries["caller_errors"] = _rate(failures, w)
    return queries
