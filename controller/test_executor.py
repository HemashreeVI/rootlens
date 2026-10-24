import os
import subprocess
import tempfile

import executor
import safety


class FakeRunner:
    """Pretends to be docker compose and remembers what it was asked to run."""

    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.commands = []

    def __call__(self, command, project_dir):
        self.commands.append(command)
        return subprocess.CompletedProcess(command, self.returncode, "", self.stderr)


class FakeService:
    """Pretends to be a service's /version endpoint."""

    def __init__(self, before, after=None):
        self.answers = [before, after if after is not None else before]
        self.calls = 0

    def __call__(self, service):
        self.calls += 1
        return self.answers[0] if self.calls == 1 else self.answers[1]


class FakeRecorder:
    def __init__(self):
        self.records = []

    def __call__(self, service, version, previous):
        self.records.append((service, version, previous))


def project(env_text=None):
    folder = tempfile.mkdtemp()
    if env_text is not None:
        with open(os.path.join(folder, ".env"), "w", newline="") as file:
            file.write(env_text)
    return folder


def read_env(folder):
    with open(os.path.join(folder, ".env")) as file:
        return file.read()


def rollback(target="v1", service="payment-service"):
    return {"action": "rollback_deployment", "service": service, "target_version": target}


def restart(service="payment-service"):
    return {"action": "restart_service", "service": service, "target_version": None}


def run(decision, folder, runner=None, probe=None, recorder=None):
    return executor.execute(
        decision,
        project_dir=folder,
        runner=runner or FakeRunner(),
        probe=probe or FakeService("v2", "v1"),
        recorder=recorder or FakeRecorder(),
        ready_timeout=0,
    )


# ---------- refusals: nothing may run ----------

def test_order_service_is_refused():
    runner = FakeRunner()
    result = run(restart("order-service"), project(), runner=runner)
    assert not result["ok"]
    assert result["outcome"].startswith("Refused")
    assert runner.commands == []


def test_unknown_action_is_refused():
    runner = FakeRunner()
    decision = {"action": "delete_database", "service": "payment-service"}
    result = run(decision, project(), runner=runner)
    assert result["outcome"].startswith("Refused")
    assert runner.commands == []


def test_bad_version_text_is_refused():
    runner = FakeRunner()
    result = run(rollback("v1; rm -rf /"), project(), runner=runner)
    assert result["outcome"].startswith("Refused")
    assert runner.commands == []


def test_missing_version_is_refused():
    result = run(rollback(None), project())
    assert result["outcome"].startswith("Refused")


def test_already_on_target_is_refused():
    runner = FakeRunner()
    result = run(rollback("v1"), project(), runner=runner, probe=FakeService("v1"))
    assert result["outcome"].startswith("Refused")
    assert runner.commands == []


def test_refusals_do_not_count_as_attempts():
    result = run(restart("order-service"), project())
    assert not safety.counts_as_attempt(result["outcome"])


# ---------- rollback ----------

def test_rollback_runs_the_right_command():
    runner = FakeRunner()
    run(rollback(), project(), runner=runner)
    assert runner.commands == [[
        "docker", "compose", "up", "-d", "--no-deps", "--force-recreate",
        "payment-service",
    ]]


def test_rollback_updates_env_and_keeps_other_lines():
    folder = project("TELEGRAM_BOT_TOKEN=abc\r\nPAYMENT_SERVICE_VERSION=v2\r\n")
    run(rollback(), folder)
    assert read_env(folder) == "TELEGRAM_BOT_TOKEN=abc\nPAYMENT_SERVICE_VERSION=v1\n"


def test_rollback_creates_env_if_missing():
    folder = project()
    run(rollback(), folder)
    assert read_env(folder) == "PAYMENT_SERVICE_VERSION=v1\n"


def test_rollback_records_the_deployment():
    recorder = FakeRecorder()
    run(rollback(), project(), recorder=recorder)
    assert recorder.records == [("payment-service", "v1", "v2")]


def test_successful_rollback_counts_as_an_attempt():
    result = run(rollback(), project())
    assert result["ok"]
    assert result["outcome"].startswith("Executed")
    assert safety.counts_as_attempt(result["outcome"])


def test_failed_rollback_puts_env_back():
    folder = project("PAYMENT_SERVICE_VERSION=v2\n")
    runner = FakeRunner(returncode=1, stderr="something\nno such service\n")
    result = run(rollback(), folder, runner=runner)
    assert not result["ok"]
    assert result["outcome"].startswith("Execution failed")
    assert "no such service" in result["outcome"]
    assert read_env(folder) == "PAYMENT_SERVICE_VERSION=v2\n"
    assert safety.counts_as_attempt(result["outcome"])


def test_failed_rollback_is_not_recorded():
    recorder = FakeRecorder()
    run(rollback(), project(), runner=FakeRunner(returncode=1), recorder=recorder)
    assert recorder.records == []


def test_slow_service_still_counts_but_warns():
    result = run(rollback(), project(), probe=FakeService("v2", "v2"))
    assert result["outcome"].startswith("Executed")
    assert "warning" in result["outcome"]


# ---------- restart ----------

def test_restart_runs_the_right_command():
    runner = FakeRunner()
    result = run(restart(), project(), runner=runner)
    assert runner.commands == [["docker", "compose", "restart", "payment-service"]]
    assert result["outcome"].startswith("Executed")


def test_restart_does_not_touch_env():
    folder = project("PAYMENT_SERVICE_VERSION=v2\n")
    run(restart(), folder)
    assert read_env(folder) == "PAYMENT_SERVICE_VERSION=v2\n"


def test_failed_restart_is_reported():
    result = run(restart(), project(), runner=FakeRunner(returncode=1, stderr="boom"))
    assert result["outcome"] == "Execution failed: docker compose exited with code 1: boom"


def test_dotted_version_is_accepted():
    runner = FakeRunner()
    result = run(rollback("v1.10.2"), project(), runner=runner)
    assert not result["outcome"].startswith("Refused"), result["outcome"]
    assert runner.commands


def test_restart_by_container_name_for_services_outside_compose():
    assert executor.build_command("restart_service", "payment", "payment") == [
        "docker", "restart", "payment",
    ]
    assert executor.build_command("restart_service", "payment-service") == [
        "docker", "compose", "restart", "payment-service",
    ]


if __name__ == "__main__":
    tests = [
        value for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"{len(tests)} tests passed.")