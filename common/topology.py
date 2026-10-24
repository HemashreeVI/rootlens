"""Reads services.yaml: which app RootLens watches.

Shared by the detector, the investigator, the controller and the dashboard, so
none of them hard-codes service names, metrics or who calls whom.

The file is found at $ROOTLENS_CONFIG, else next to this file (inside a
container), else at the project root (when running from a checkout).
"""
import os

import yaml

# Actions RootLens has code for. services.yaml can only choose from these,
# so editing the config can narrow what the robot may do, never widen it.
KNOWN_ACTIONS = {"rollback_deployment", "restart_service"}

ENTRY_KEYS = ("service", "requests_metric", "endpoint", "latency_metric",
              "dependency_failures_metric", "timeout_seconds")
SERVICE_KEYS = ("requests_metric", "endpoint")

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

    known = set(services) | {entry["service"]}
    dependencies = raw.get("dependencies") or {}
    for caller, callees in dependencies.items():
        for name in [caller, *callees]:
            if name not in known:
                raise ConfigError(f"dependencies mention unknown service {name}.")

    return {"entry": entry, "services": services, "dependencies": dependencies}


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
