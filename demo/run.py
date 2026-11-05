"""RootLens hosted demo: the whole lab in one container, for a free web host.

Docker Compose runs eight containers. A free host gives you one container and
one public port, so this supervisor runs the same programs as plain processes:

    prometheus       scrapes the three services every 5 s     (127.0.0.1:9090)
    user-service     v1                                       (127.0.0.1:8001)
    payment-service  version from the release manifest (.env) (127.0.0.1:8003)
    order-service    the public port: shop API + dashboard    (0.0.0.0:$PORT)
    detector         opens and resolves incidents
    controller       robot mode ENFORCE: decides, acts, verifies
    traffic          about 5 customer orders per second

plus a release train that ships the deliberately buggy payment-service v2
about every 20 minutes (the rulebook allows 3 automatic actions per service
per hour). RootLens then detects the incident, blames v2 and rolls it back on
its own: the executor's "process" runtime asks this supervisor (demo/ctl.py)
to recreate payment-service, which comes back on the version now in .env.

If a child process dies, the supervisor starts it again. Nothing here is used
by the normal Docker setup.

Run locally:  PROMETHEUS_BIN=/path/to/prometheus python demo/run.py
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.getenv("DEMO_STATE", "/tmp/rootlens-demo")
PORT = int(os.getenv("PORT", "8002"))
PROMETHEUS_BIN = os.getenv("PROMETHEUS_BIN", "prometheus")
RELEASE_EVERY = int(os.getenv("RELEASE_EVERY_SECONDS", "1200"))   # quiet time between releases
FIRST_RELEASE = int(os.getenv("FIRST_RELEASE_SECONDS", "90"))      # after a (re)start
SAFETY_NET = int(os.getenv("SAFETY_NET_SECONDS", "600"))           # if RootLens does not fix it

ORDER = f"http://127.0.0.1:{PORT}"
ENV_FILE = os.path.join(STATE, ".env")
PIDS = os.path.join(STATE, "pids")

procs = {}
stopping = threading.Event()


def log(msg):
    print(f"[demo] {time.strftime('%H:%M:%S')} {msg}", flush=True)


# ---------- the release manifest (.env), shared with the executor ----------

def read_versions():
    versions = {"USER_SERVICE_VERSION": "v1", "PAYMENT_SERVICE_VERSION": "v1"}
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    versions[k] = v
    except OSError:
        pass
    return versions


def write_version(key, version):
    versions = read_versions()
    versions[key] = version
    with open(ENV_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(f"{k}={v}\n" for k, v in versions.items()))


# ---------- processes ----------

def base_env():
    env = dict(os.environ)
    env.update({
        "PYTHONUNBUFFERED": "1",
        "OTEL_SDK_DISABLED": "true",
        "DATABASE_PATH": os.path.join(STATE, "incidents.db"),
        "PROMETHEUS_URL": "http://127.0.0.1:9090",
        "USER_SERVICE_URL": "http://127.0.0.1:8001",
        "PAYMENT_SERVICE_URL": "http://127.0.0.1:8003",
        "ORDER_SERVICE_URL": ORDER,
        "TRAFFIC_URL": ORDER + "/orders",
        "CONTROLLER_MODE": "enforce",
        "EXECUTOR_RUNTIME": "process",
        "PROCESS_CTL": os.path.join(ROOT, "demo", "ctl.py"),
        "PROJECT_DIR": STATE,
        "DEMO_STATE": STATE,
        "DASHBOARD_URL": os.getenv("RENDER_EXTERNAL_URL", "") + "/dashboard",
    })
    for secret in ("TELEGRAM_BOT_TOKEN",):  # no notifications from the public demo
        env.pop(secret, None)
    return env


def uvicorn(folder, port, host="127.0.0.1", extra=None):
    def spec():
        env = base_env()
        env.update(extra() if callable(extra) else (extra or {}))
        cmd = [sys.executable, "-m", "uvicorn", "app:app", "--host", host, "--port", str(port), "--log-level", "warning"]
        return cmd, os.path.join(ROOT, folder), env
    return spec


def script(folder, name):
    def spec():
        return [sys.executable, "-u", name], os.path.join(ROOT, folder), base_env()
    return spec


def prometheus():
    config = os.path.join(STATE, "prometheus.yml")
    jobs = {"user-service": 8001, "order-service": PORT, "payment-service": 8003}
    with open(config, "w", encoding="utf-8") as f:
        f.write("global:\n  scrape_interval: 5s\nscrape_configs:\n")
        for job, port in jobs.items():
            f.write(f'  - job_name: "{job}"\n    static_configs: [{{ targets: ["127.0.0.1:{port}"] }}]\n')

    def spec():
        cmd = [PROMETHEUS_BIN, f"--config.file={config}", f"--storage.tsdb.path={os.path.join(STATE, 'tsdb')}",
               "--storage.tsdb.retention.time=2h", "--web.listen-address=127.0.0.1:9090", "--log.level=warn"]
        return cmd, STATE, base_env()
    return spec


SPECS = {}


def start(name):
    cmd, cwd, env = SPECS[name]()
    procs[name] = subprocess.Popen(cmd, cwd=cwd, env=env)
    with open(os.path.join(PIDS, name + ".pid"), "w", encoding="utf-8") as f:
        f.write(str(procs[name].pid))
    if name == "payment-service":
        log(f"payment-service started on {env['SERVICE_VERSION']}")


def supervise():
    while not stopping.is_set():
        for name, proc in list(procs.items()):
            if proc.poll() is not None and not stopping.is_set():
                start(name)
        time.sleep(0.3)


# ---------- the release train ----------

def get_json(path):
    with urllib.request.urlopen(ORDER + path, timeout=5) as r:
        return json.load(r)


def post_json(path, body):
    req = urllib.request.Request(ORDER + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def open_incidents():
    return [i for i in get_json("/incidents")["incidents"] if i.get("status") == "open"]


def release(version, previous, by):
    write_version("PAYMENT_SERVICE_VERSION", version)
    subprocess.run([sys.executable, os.path.join(ROOT, "demo", "ctl.py"), "recreate", "payment-service"],
                   env=base_env(), check=False)
    post_json("/deployments", {"service": "payment-service", "version": version,
                               "previous_version": previous, "deployed_by": by})
    log(f"{by}: payment-service {previous} -> {version}")


def release_train():
    while not stopping.is_set():  # wait for the shop
        try:
            get_json("/health")
            break
        except Exception:
            time.sleep(1)
    wait = FIRST_RELEASE
    while not stopping.is_set():
        stopping.wait(wait)
        try:
            if open_incidents() or read_versions()["PAYMENT_SERVICE_VERSION"] != "v1":
                wait = 30
                continue
            release("v2", "v1", "release-train (demo)")
            shipped = time.time()
            while not stopping.is_set() and read_versions()["PAYMENT_SERVICE_VERSION"] == "v2":
                if time.time() - shipped > SAFETY_NET:
                    log("RootLens did not roll back in time; the demo's safety net does it")
                    release("v1", "v2", "release-train (safety net)")
                    break
                time.sleep(5)
            while not stopping.is_set() and open_incidents():  # let the incident resolve
                time.sleep(5)
            wait = RELEASE_EVERY
        except Exception as error:  # keep the train running whatever happens
            log(f"release train: {error}")
            wait = 30


def main():
    os.makedirs(PIDS, exist_ok=True)
    write_version("PAYMENT_SERVICE_VERSION", "v1")
    write_version("USER_SERVICE_VERSION", "v1")

    SPECS.update({
        "prometheus": prometheus(),
        "user-service": uvicorn("user-service", 8001, extra=lambda: {"SERVICE_VERSION": read_versions()["USER_SERVICE_VERSION"]}),
        "payment-service": uvicorn("payment-service", 8003, extra=lambda: {"SERVICE_VERSION": read_versions()["PAYMENT_SERVICE_VERSION"]}),
        "order-service": uvicorn("order-service", PORT, host="0.0.0.0", extra={"HOSTED_DEMO": "1"}),
        "detector": script("detector", "detector.py"),
        "controller": script("controller", "controller.py"),
        "traffic": script(".", "traffic.py"),
    })
    for name in SPECS:
        start(name)
    log(f"RootLens demo on port {PORT}: open /dashboard")

    def shutdown(*_):
        stopping.set()
        for proc in procs.values():
            proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    threading.Thread(target=release_train, daemon=True).start()
    supervise()


if __name__ == "__main__":
    main()
