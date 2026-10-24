"""Run every RootLens test file. No Docker or network needed.

    python run_tests.py

Each file runs on its own, with common/ (the shared services.yaml reader) on
the import path, exactly as it is inside the containers.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

TEST_FILES = [
    "common/test_topology.py",
    "detector/test_detector.py",
    "order-service/test_investigator.py",
    "controller/test_policy.py",
    "controller/test_safety.py",
    "controller/test_executor.py",
    "controller/test_controller.py",
    "controller/test_notify.py",
    "benchmark/test_report.py",
]


def main():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in [os.path.join(ROOT, "common"), env.get("PYTHONPATH")] if p
    )

    failed = []
    for path in TEST_FILES:
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, path)],
            env=env, capture_output=True, text=True,
        )
        lines = (result.stdout or "").strip().splitlines()
        summary = lines[-1] if lines else ""
        if result.returncode == 0:
            print(f"ok    {path:38} {summary}")
        else:
            failed.append(path)
            print(f"FAIL  {path}")
            print(result.stdout + result.stderr)

    print(f"\n{len(TEST_FILES) - len(failed)}/{len(TEST_FILES)} test files passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
