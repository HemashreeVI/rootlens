"""Tests for the controller's enforce flow, with every outside call faked."""
import time
from datetime import datetime, timedelta, timezone

import controller
import executor
import notify


def iso(seconds_ago):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def bad_release_diagnosis():
    return {
        "likely_cause": "payment-service is returning errors",
        "cause_kind": "errors",
        "confidence": "high",
        "root_cause_service": "payment-service",
        "ranked_causes": [{
            "cause": "payment-service is returning errors",
            "service": "payment-service", "score": 1.0,
        }],
        "trigger": {
            "service": "payment-service", "version": "v2",
            "previous_version": "v1", "seconds_before_detection": 42,
            "strength": "strong", "kind": "release",
        },
    }


class World:
    """Fakes for order-service, the live numbers, the executor and Telegram."""

    def __init__(self, mode="enforce", ratio=0.5, payment_version="v2", audit=None):
        self.ratio = ratio
        self.payment_version = payment_version
        self.audit = list(audit or [])
        self.executed = []
        self.notices = []
        self.recorded = []

        controller.MODE = mode
        controller.get_diagnosis = lambda incident_id: bad_release_diagnosis()
        controller.get_audit_entries = lambda: list(self.audit)
        controller.live_state = lambda: {
            "failure_ratio": self.ratio,
            "versions": {"payment-service": self.payment_version, "user-service": "v1"},
        }
        controller.record = self.record
        executor.execute = self.execute
        notify.notify = lambda kind, info, environ=None: self.notices.append(kind) or 0

    def record(self, incident_id, decision, outcome):
        self.recorded.append(outcome)
        self.audit.append({
            "incident_id": incident_id, "mode": controller.MODE,
            "decision": decision["decision"], "action": decision["action"],
            "service": decision["service"], "outcome": outcome,
            "logged_at": iso(0),
        })

    def execute(self, decision):
        self.executed.append((decision["action"], decision["target_version"]))
        return {"ok": True, "outcome": f"Executed: {decision['action']}"}


def incident(seconds_ago=60):
    return {"incident_id": "inc-12345678", "detected_at": iso(seconds_ago)}


# ---------- modes ----------

def test_known_modes_are_kept():
    assert controller.resolve_mode("enforce") == "enforce"
    assert controller.resolve_mode(" Dry-Run ") == "dry-run"


def test_unknown_mode_means_off():
    assert controller.resolve_mode("yolo") == "off"
    assert controller.resolve_mode("") == "off"


# ---------- dry-run ----------

def test_dry_run_never_executes():
    world = World(mode="dry-run")
    controller.process(incident(), {})
    assert world.executed == []
    assert world.recorded == ["Not executed (dry-run)"]


# ---------- enforce: acting ----------

def test_enforce_rolls_back_a_bad_release():
    world = World()
    tracked = {}
    controller.process(incident(), tracked)
    assert world.executed == [("rollback_deployment", "v1")]
    assert world.recorded == ["Executed: rollback_deployment"]
    assert tracked["inc-12345678"]["verify"] is not None
    assert "acted" in world.notices


def test_enforce_refuses_when_already_recovered():
    world = World(ratio=0.05)
    controller.process(incident(), {})
    assert world.executed == []
    assert world.recorded[0].startswith("Refused: The live failure ratio")


def test_enforce_refuses_when_version_changed():
    world = World(payment_version="v1")
    controller.process(incident(), {})
    assert world.executed == []
    assert "situation has changed" in world.recorded[0]


def test_repeated_refusals_are_recorded_once():
    world = World(ratio=0.05)
    tracked = {}
    controller.process(incident(), tracked)
    world.ratio = 0.04  # reason text changes, but it is the same refusal
    controller.process(incident(), tracked)
    assert len(world.recorded) == 1


def test_nothing_new_happens_while_verifying():
    world = World()
    tracked = {}
    controller.process(incident(), tracked)
    controller.process(incident(), tracked)
    assert len(world.executed) == 1
    assert len(world.recorded) == 1


# ---------- enforce: verifying ----------

def test_recovery_ends_verification_quietly():
    world = World()
    tracked = {}
    controller.process(incident(), tracked)
    world.ratio = 0.0
    for _ in range(3):
        controller.process(incident(), tracked)
    assert tracked["inc-12345678"]["verify"] is None
    assert "fix_failed" not in world.notices


def test_failed_fix_then_restart_then_human():
    world = World()
    tracked = {}

    # 1. Roll back.
    controller.process(incident(), tracked)
    assert world.executed == [("rollback_deployment", "v1")]

    # 2. Still failing when the verification time is up.
    entry = tracked["inc-12345678"]
    entry["verify"]["started"] = time.time() - 130
    controller.process(incident(), tracked)
    assert entry["verify"] is None
    assert "fix_failed" in world.notices

    # 3. Next check, after the cooldown: restart. (Pretend the rollback was
    #    2 minutes ago, and the service is now on v1 as the rollback set it.)
    world.audit[-1]["logged_at"] = iso(120)
    world.payment_version = "v1"
    controller.process(incident(), tracked)
    assert world.executed[-1] == ("restart_service", None)

    # 4. Restart did not help either: hand over to a human.
    entry["verify"]["started"] = time.time() - 130
    controller.process(incident(), tracked)
    world.audit[-1]["logged_at"] = iso(120)
    controller.process(incident(), tracked)
    assert len(world.executed) == 2
    assert world.recorded[-1] == "Needs a human"


if __name__ == "__main__":
    tests = [
        value for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"{len(tests)} tests passed.")