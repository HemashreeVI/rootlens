<p align="center">
  <img src="logo.svg" alt="RootLens logo" width="150" />
</p>

<p align="center">
  <img src="banner.svg" alt="RootLens: production reliability, with a human in the loop" width="100%" />
</p>

<p align="center">
  <a href="https://github.com/HemashreeVI/RootLens/actions/workflows/ci.yml"><img src="https://github.com/HemashreeVI/RootLens/actions/workflows/ci.yml/badge.svg" alt="CI status" /></a>
  <img src="https://img.shields.io/badge/python-FastAPI-009688?logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white" alt="Docker Compose" />
  <img src="https://img.shields.io/badge/prometheus-metrics-E6522C?logo=prometheus&logoColor=white" alt="Prometheus" />
  <img src="https://img.shields.io/badge/grafana-dashboards-F46800?logo=grafana&logoColor=white" alt="Grafana" />
  <img src="https://img.shields.io/badge/opentelemetry-traces-425CC7?logo=opentelemetry&logoColor=white" alt="OpenTelemetry" />
  <img src="https://img.shields.io/badge/tests-118%20passing-2E9B63" alt="118 tests" />
  <img src="https://img.shields.io/badge/status-working%20end%20to%20end-2E9B63" alt="Status" />
</p>

<p align="center">
  <b>RootLens watches a small microservice app, works out why it broke, and fixes it only within strict limits.</b>
</p>

<p align="center">
  <a href="docs/launch-video.mp4"><img src="docs/launch-video.jpg" alt="Watch the 20-second RootLens launch video" width="82%" /></a>
  <br />
  <sub>▶ <a href="docs/launch-video.mp4"><b>Watch the 20-second launch video</b></a> (opens in GitHub's video player)</sub>
</p>

---

## What it does

When a service fails, RootLens follows the same steps an on-call engineer would:

1. **Detect** the failure from live metrics.
2. **Collect evidence** (error rates, latency, which dependency is down or slow, and whether a release went out just before).
3. **Rank root causes** with a confidence level, instead of guessing one answer.
4. **Estimate impact** across the services that depend on the broken one.
5. **Pick an approved fix** (rollback or restart) through a restricted controller.
6. **Verify recovery**, and record every decision.
7. **Escalate to a human** when the evidence is not strong enough.

> Every stage works end to end. In a live enforce-mode test, RootLens detected a bad `payment-service` release, traced it to that deploy, rolled it back on its own, and confirmed the failures stopped.

## Architecture

<p align="center">
  <img src="architecture.svg" alt="RootLens architecture diagram" width="100%" />
</p>

| Component | Port | Role |
|---|---|---|
| `user-service` | 8001 | FastAPI. Chaos switches for errors and latency. Release `v2` has a planted 4 s latency bug. |
| `order-service` | 8002 | FastAPI. Calls user then payment (3 s timeout each). Also hosts incidents, deployments, the investigator and the dashboard. |
| `payment-service` | 8003 | FastAPI. Same pattern. Release `v2` has a planted 50% error bug. |
| `detector` | none | Polls Prometheus every 10 s. Opens an incident after 3 checks at or above 0.3 failure ratio, resolves after 3 checks below 0.1. |
| `controller` | none | The "robot". Reads each open incident, asks the rulebook what to do, and in enforce mode acts, verifies and records. Sends Telegram notices to the service owner. |
| `prometheus` | 9090 | Scrapes all three services every 5 s. |
| `grafana` | 3000 | "RootLens Observability Dashboard". |
| `jaeger` | 16686 | Distributed tracing. The three services send OpenTelemetry traces, so one slow order can be followed across every service. |
| SQLite | volume | `/data/incidents.db` in the `rootlens-data` volume. Holds incidents, deployments and the audit log. |

## How the investigator decides

For every dependency that is failing, the investigator scores three possible causes and reports them ranked, with the evidence for and against each.

| Cause | Main signals |
|---|---|
| **Down** | The `up` metric is 0, no metrics reported, most failures are "unreachable". |
| **Returning errors** | Service is up, its own error ratio is high, most failures are errors, p95 latency is normal. |
| **Too slow** | Service is up, most failures are timeouts, p95 latency is close to the caller's timeout. |

From Order's point of view, a dead dependency and a slow one both look like timeouts. The `up` metric is what tells them apart.

**Bad releases:** if the failing service was deployed shortly before the failures started, the investigator links the incident to that release and names the earlier version to go back to.

Confidence is **high** when the top score is at least 0.75 and leads the runner-up by 0.3 or more, **medium** when the top score is at least 0.5, and **low** otherwise. Below 0.4 the answer is "insufficient evidence". The output also names the root-cause service and the services it impacts.

## Incident lifecycle

```mermaid
stateDiagram-v2
    [*] --> Healthy
    Healthy --> Open: 3 checks at or above 0.3
    Open --> Investigated: evidence stored, causes ranked
    Investigated --> Escalated: weak evidence or not allowed
    Investigated --> Acting: policy says rollback or restart
    Acting --> Resolved: verified, 3 checks below 0.1
    Acting --> Escalated: did not recover
    Investigated --> Resolved: 3 checks below 0.1
    Resolved --> Healthy
    Open --> Open: same fingerprint, no duplicate
```

## Quick start

You need Docker Desktop (WSL2), Git, Python 3 and PowerShell.

```powershell
git clone https://github.com/HemashreeVI/RootLens.git
cd RootLens
docker compose up -d
docker compose ps
```

You should see 8 containers running. Then open:

| What | URL |
|---|---|
| Live incident dashboard | http://localhost:8002/dashboard |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |
| Jaeger (traces) | http://localhost:16686 |

> Never run `docker compose down -v`. It deletes the incident database volume.

## Try it: the failure lab

Use three terminals from the project root.

```powershell
# Terminal 1: watch the detector
docker compose logs -f detector

# Terminal 2: send fake customer traffic (about 5 requests per second)
python .\traffic.py

# Terminal 3: break something
Invoke-RestMethod -Method Post "http://localhost:8003/chaos/errors?rate=0.5"
```

Watch the dashboard. An incident opens after about 30 seconds. Then reset:

```powershell
Invoke-RestMethod -Method Post "http://localhost:8003/chaos/reset"
```

The detector log prints `RESOLVE incident` once traffic has been healthy for 3 checks. Wait about a minute before the next test so the 1-minute metrics window clears.

## Try it: bad release and rollback

Each service reads `SERVICE_VERSION` and exposes `/version`. Only `v2` contains a bug.

```powershell
.\deploy.ps1 payment-service v2   # bad release: 50% errors, incident opens
.\deploy.ps1 payment-service v1   # rollback: incident resolves
```

`deploy.ps1` updates `.env`, recreates only that container, waits for `/version`, then records the deployment through `POST /deployments`. If PowerShell blocks the script:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy.ps1 payment-service v2
```

## Try it: let the robot fix it

The controller starts in `dry-run`: it decides and records, but never acts. To let it act, set the mode and recreate it:

```powershell
# in .env
CONTROLLER_MODE=enforce

docker compose up -d controller
.\deploy.ps1 payment-service v2      # ship the bad release
docker compose logs -f controller    # watch it decide, roll back to v1 and verify
```

Every step appears in the dashboard's audit log, including the times it chose **not** to act. Set it back to `dry-run` when you are done.

## Tests and CI

```powershell
python .\order-service\test_investigator.py
python .\controller\test_policy.py
python .\controller\test_safety.py
```

118 unit tests across six files cover the investigator, the rulebook, the safety checks, the executor, the controller loop and notifications. The rulebook and safety checks are pure functions with no Docker or network access, so every rule can be tested on its own. GitHub Actions runs two jobs on every push: `test` runs all six test files, and `build` validates the compose file and builds every image.

## Project layout

```
RootLens/
├── logo.svg, banner.svg, architecture.svg   images used by this README
├── user-service/            FastAPI service with chaos switches
├── payment-service/         FastAPI service with chaos switches
├── order-service/
│   ├── app.py               API, incidents, deployments
│   ├── database.py          SQLite access
│   ├── investigator.py      root-cause ranking
│   ├── dashboard.py         live HTML dashboard
│   └── test_investigator.py unit tests
├── detector/                polls Prometheus, opens and resolves incidents
├── controller/
│   ├── policy.py            the rulebook: act or escalate (decides only)
│   ├── safety.py            pre-flight checks and recovery verification
│   ├── executor.py          the only code that changes the running system
│   ├── controller.py        the loop: decide, check, act, verify, record
│   ├── notify.py            Telegram notices to service owners
│   └── test_*.py            unit tests
├── prometheus/prometheus.yml
├── compose.yaml
├── traffic.py               fake customers
├── deploy.ps1               versioned deploy and rollback
└── .github/workflows/ci.yml
```

## Roadmap

- [x] Three FastAPI services with Docker Compose
- [x] Prometheus and Grafana
- [x] Incident database with open and resolve lifecycle
- [x] Failure lab (chaos switches)
- [x] Continuous detector with evidence collection
- [x] Multi-service root-cause ranking with blast radius
- [x] Live dashboard
- [x] Unit tests and GitHub Actions CI
- [x] Versioned releases, `deploy.ps1`, deployment records
- [x] Change correlation: link a failure to a release that went out just before it
- [x] Policy engine: a unit-tested rulebook that decides rollback, restart, or escalate
- [x] Remediation controller: off, dry-run and enforce, with pre-flight checks and recovery verification
- [x] Dashboard: per-dependency evidence, deployments, escalations, audit log
- [x] Telegram notices to each service's owner
- [x] OpenTelemetry tracing with Jaeger
- [ ] Centralized logs
- [ ] Kubernetes
- [ ] Later: LLM-written explanations grounded in evidence, PostgreSQL, API authentication, Grafana persistence, a latency-based detector, an experiment results table

## Safety design

The controller has Docker access, which is powerful. These limits keep it contained:

- **Allowlist:** only `rollback_deployment` and `restart_service`, on `user-service` and `payment-service`.
- **Rate limits:** at most 2 actions per incident, 60 s apart, and 3 per service per hour.
- **Dry-run first:** the controller can be switched between `off`, `dry-run` and `enforce`. Any unknown setting means `off`.
- **Pre-flight checks:** before acting it confirms the incident is under 30 minutes old, the service is still failing, and the rollback target really is an earlier version.
- **Verify, then trust:** after acting, it needs 3 healthy checks in a row within 2 minutes, and escalates if not.
- **Audit log:** every decision is recorded, including "chose not to act".
- **Human escalation:** medium or low confidence, or more than one dependency failing, means no action.

## Honest notes

- The rollback is real release mechanics, but the bug in `v2` is deliberately planted. This is a simulated release pipeline on one machine.
- `POST /deployments` and the other write endpoints are not authenticated yet.
- The controller mounts the Docker socket. That is fine on one laptop, but a real system would use a narrower permission.
- Slowness that stays under the 3 s timeout is not detected yet.

## Author

Built by **Hemashree** ([@HemashreeVI](https://github.com/HemashreeVI)) as a hands-on project in production reliability and AI-assisted operations.
