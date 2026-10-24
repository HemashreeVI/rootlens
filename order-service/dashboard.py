import asyncio
import os
import time

import httpx

import topology

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")

# Which app to show comes from services.yaml (see common/topology.py).
ENTRY = topology.entry()
_ENTRY_SELECTOR = 'endpoint="' + ENTRY["endpoint"] + '"'

FAILURE_RATIO_QUERY = (
    "sum(rate(" + ENTRY["requests_metric"] + "{" + _ENTRY_SELECTOR
    + ',status=~"5.."}[1m]))'
    + " / "
    + "sum(rate(" + ENTRY["requests_metric"] + "{" + _ENTRY_SELECTOR + "}[1m]))"
)

P95_QUERY = (
    "histogram_quantile(0.95, sum by (le) "
    "(rate(" + ENTRY["latency_metric"] + "_bucket"
    "{" + _ENTRY_SELECTOR + "}[1m])))"
)

RATE_QUERY = (
    "sum(rate(" + ENTRY["requests_metric"] + "{" + _ENTRY_SELECTOR + "}[1m]))"
)

DEPENDENCIES = {
    name: {"metric": info["requests_metric"], "endpoint": info["endpoint"]}
    for name, info in topology.services().items()
}


async def _instant(client, query):
    """Ask Prometheus for one number. Returns None if there is no data."""
    try:
        response = await client.get(
            f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}
        )
        result = response.json()["data"]["result"]
        if not result:
            return None
        value = float(result[0]["value"][1])
        return None if value != value else value
    except Exception:
        return None


async def _range(client, query, minutes=15, step=15):
    """Ask Prometheus for a series of [timestamp, value] points."""
    try:
        end = time.time()
        response = await client.get(
            f"{PROMETHEUS_URL}/api/v1/query_range",
            params={
                "query": query,
                "start": end - minutes * 60,
                "end": end,
                "step": step,
            },
        )
        result = response.json()["data"]["result"]
        if not result:
            return []
        points = []
        for timestamp, raw in result[0]["values"]:
            value = float(raw)
            points.append([timestamp, None if value != value else value])
        return points
    except Exception:
        return []


async def _version(client, url):
    try:
        response = await client.get(url + "/version")
        return response.json().get("version")
    except Exception:
        return None


def _own_error_query(info):
    selector = 'endpoint="' + info["endpoint"] + '"'
    metric = info["metric"]
    return (
        'sum(rate(' + metric + '{' + selector + ',status=~"5.."}[1m]))'
        + ' / '
        + 'sum(rate(' + metric + '{' + selector + '}[1m]))'
    )


def _timeout_query(name):
    return (
        "sum(rate(" + ENTRY["dependency_failures_metric"]
        + '{dependency="' + name + '",kind="timeout"}[1m]))'
    )


def _display(name, info):
    return {
        "name": info.get("display_name") or name,
        "role": info.get("role") or "",
    }


async def build_overview():
    """Everything the dashboard needs about live system health."""
    entry_name = ENTRY["service"]
    urls = {
        name: info.get("url") for name, info in topology.services().items()
    }

    async with httpx.AsyncClient(timeout=3.0) as client:
        tasks = {
            "failure_ratio": _instant(client, FAILURE_RATIO_QUERY),
            "p95": _instant(client, P95_QUERY),
            "rate": _instant(client, RATE_QUERY),
            "history_ratio": _range(client, FAILURE_RATIO_QUERY),
            "history_rate": _range(client, RATE_QUERY),
            "entry:up": _instant(client, 'up{job="' + entry_name + '"}'),
        }
        for name, info in DEPENDENCIES.items():
            tasks[name + ":up"] = _instant(client, 'up{job="' + name + '"}')
            tasks[name + ":err"] = _instant(client, _own_error_query(info))
            tasks[name + ":timeout"] = _instant(client, _timeout_query(name))
            if urls.get(name):
                tasks[name + ":version"] = _version(client, urls[name])

        keys = list(tasks.keys())
        values = await asyncio.gather(*tasks.values())
        data = dict(zip(keys, values))

    services = {entry_name: {"up": data["entry:up"], "version": None}}
    for name in DEPENDENCIES:
        services[name] = {
            "up": data[name + ":up"],
            "version": data.get(name + ":version"),
            "error_ratio": data[name + ":err"],
            "timeout_rate": data[name + ":timeout"],
        }

    return {
        "generated_at": time.time(),
        "failure_ratio": data["failure_ratio"],
        "p95_latency_seconds": data["p95"],
        "request_rate": data["rate"],
        "services": services,
        "topology": {
            "entry": entry_name,
            "dependencies": topology.dependency_graph().get(entry_name, []),
            "display": {
                entry_name: _display(entry_name, ENTRY),
                **{
                    name: _display(name, info)
                    for name, info in topology.services().items()
                },
            },
        },
        "history": {
            "failure_ratio": data["history_ratio"],
            "request_rate": data["history_rate"],
        },
    }


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RootLens</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #f4f6fa;
  --panel: #ffffff;
  --panel-2: #f7f9fc;
  --border: #e4e8ef;
  --text: #0f172a;
  --muted: #566176;
  --faint: #8792a6;
  --brand: #4f46e5;
  --brand-soft: #eef0ff;
  --ok: #047857;   --ok-soft: #e8f7f0;
  --warn: #b45309; --warn-soft: #fdf4e3;
  --bad: #c81e1e;  --bad-soft: #fdecec;
  --idle: #64748b; --idle-soft: #eef1f5;
  --side: #0b1324;
  --side-text: #aeb9cc;
  --side-hover: #16213a;
  --side-active: #1d2a47;
  --shadow: 0 1px 2px rgba(15, 23, 42, .04), 0 2px 6px rgba(15, 23, 42, .04);
  --radius: 12px;
  --mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Consolas, monospace;
}
:root[data-theme="dark"] {
  --bg: #0a0f1c;
  --panel: #111827;
  --panel-2: #0e1523;
  --border: #1f2a3c;
  --text: #e7ecf5;
  --muted: #9ba7bc;
  --faint: #6c788e;
  --brand: #8b93ff;
  --brand-soft: rgba(139, 147, 255, .13);
  --ok: #34d399;   --ok-soft: rgba(52, 211, 153, .12);
  --warn: #fbbf24; --warn-soft: rgba(251, 191, 36, .12);
  --bad: #f87171;  --bad-soft: rgba(248, 113, 113, .13);
  --idle: #94a3b8; --idle-soft: rgba(148, 163, 184, .12);
  --side: #070b15;
  --side-text: #98a4ba;
  --side-hover: #111a2c;
  --side-active: #18233b;
  --shadow: none;
}
* { box-sizing: border-box; }
html, body { margin: 0; }
body {
  font-family: Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--brand); text-decoration: none; }
a:hover { text-decoration: underline; }
code, .mono { font-family: var(--mono); font-size: 12.5px; }

/* ---------- layout ---------- */
.app { display: grid; grid-template-columns: 240px 1fr; min-height: 100vh; }
.sidebar {
  background: var(--side);
  color: var(--side-text);
  padding: 20px 14px;
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
}
.brand { display: flex; align-items: center; gap: 10px; padding: 4px 10px 22px; color: #fff; }
.brand-name { font-weight: 700; font-size: 17px; letter-spacing: -.01em; }
.brand-sub { font-size: 11.5px; color: var(--side-text); margin-top: -2px; }
.nav { display: flex; flex-direction: column; gap: 2px; }
.nav-label { font-size: 11px; text-transform: uppercase; letter-spacing: .08em; color: #5f6b82; padding: 14px 10px 6px; }
.nav a {
  display: flex; align-items: center; gap: 11px;
  padding: 9px 10px; border-radius: 8px;
  color: var(--side-text); font-weight: 500;
}
.nav a:hover { background: var(--side-hover); color: #fff; text-decoration: none; }
.nav a.active { background: var(--side-active); color: #fff; }
.nav a.active svg { color: #a5b4fc; }
.nav svg { width: 18px; height: 18px; flex: none; }
.nav .count {
  margin-left: auto; background: var(--bad); color: #fff;
  font-size: 11px; font-weight: 700; border-radius: 999px; padding: 1px 7px;
}
.side-foot { margin-top: auto; padding: 12px 10px 0; border-top: 1px solid #1a2438; font-size: 12px; }
.side-foot .row { display: flex; justify-content: space-between; align-items: center; padding: 4px 0; }
.side-foot a { color: var(--side-text); }
.side-foot a:hover { color: #fff; }

.main { min-width: 0; }
.topbar {
  display: flex; align-items: center; gap: 16px;
  padding: 18px 32px; border-bottom: 1px solid var(--border);
  background: var(--panel); position: sticky; top: 0; z-index: 5;
}
.topbar h1 { font-size: 19px; margin: 0; letter-spacing: -.01em; }
.topbar p { margin: 1px 0 0; color: var(--muted); font-size: 13px; }
.topbar .right { margin-left: auto; display: flex; align-items: center; gap: 12px; }
.live { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 12.5px; white-space: nowrap; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ok); box-shadow: 0 0 0 3px var(--ok-soft); }
.dot.err { background: var(--bad); box-shadow: 0 0 0 3px var(--bad-soft); }
.icon-btn {
  border: 1px solid var(--border); background: var(--panel); color: var(--muted);
  width: 34px; height: 34px; border-radius: 8px; cursor: pointer;
  display: grid; place-items: center;
}
.icon-btn:hover { color: var(--text); }
.content { padding: 24px 32px 48px; max-width: 1320px; }

/* ---------- building blocks ---------- */
.card {
  background: var(--panel); border: 1px solid var(--border);
  border-radius: var(--radius); box-shadow: var(--shadow);
  margin-bottom: 20px; min-width: 0;
}
.card-head { padding: 16px 20px 0; }
.card-head h2 { font-size: 15px; margin: 0; }
.card-head p { margin: 2px 0 0; color: var(--muted); font-size: 12.5px; }
.card-body { padding: 16px 20px 20px; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.grid-2 > .card { margin-bottom: 0; }
.grid-2 + * { margin-top: 20px; }
.split { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr); gap: 20px; align-items: start; }
.stack > * + * { margin-top: 20px; }
.stack > .card { margin-bottom: 0; }
.muted { color: var(--muted); }
.faint { color: var(--faint); }
.small { font-size: 12.5px; }

.pill {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 12px; font-weight: 600; padding: 3px 9px; border-radius: 999px;
  white-space: nowrap;
}
.pill::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.pill.plain::before { display: none; }
.t-ok { color: var(--ok); background: var(--ok-soft); }
.t-warn { color: var(--warn); background: var(--warn-soft); }
.t-bad { color: var(--bad); background: var(--bad-soft); }
.t-idle { color: var(--idle); background: var(--idle-soft); }
.t-brand { color: var(--brand); background: var(--brand-soft); }

.tag {
  display: inline-block; font-size: 11.5px; font-weight: 600;
  padding: 2px 8px; border-radius: 6px; border: 1px solid var(--border);
  color: var(--muted); background: var(--panel-2); white-space: nowrap;
}

/* ---------- status banner ---------- */
.banner {
  display: flex; gap: 16px; align-items: flex-start;
  padding: 18px 20px; border-radius: var(--radius); margin-bottom: 20px;
  border: 1px solid transparent;
}
.banner .b-icon { width: 40px; height: 40px; border-radius: 10px; display: grid; place-items: center; flex: none; }
.banner .b-icon svg { width: 22px; height: 22px; }
.banner h2 { margin: 0; font-size: 17px; }
.banner p { margin: 3px 0 0; color: var(--text); opacity: .85; }
.banner .b-action { margin-left: auto; align-self: center; }
.banner.ok { background: var(--ok-soft); border-color: color-mix(in srgb, var(--ok) 25%, transparent); }
.banner.ok .b-icon { background: var(--ok); color: #fff; }
.banner.warn { background: var(--warn-soft); border-color: color-mix(in srgb, var(--warn) 30%, transparent); }
.banner.warn .b-icon { background: var(--warn); color: #fff; }
.banner.bad { background: var(--bad-soft); border-color: color-mix(in srgb, var(--bad) 30%, transparent); }
.banner.bad .b-icon { background: var(--bad); color: #fff; }
.banner.idle { background: var(--idle-soft); border-color: var(--border); }
.banner.idle .b-icon { background: var(--idle); color: #fff; }
.btn {
  display: inline-flex; align-items: center; gap: 6px;
  background: var(--panel); color: var(--text); border: 1px solid var(--border);
  padding: 8px 14px; border-radius: 8px; font-weight: 600; font-size: 13px;
  white-space: nowrap;
}
.btn:hover { text-decoration: none; border-color: var(--faint); }

/* ---------- pipeline ---------- */
.pipeline { display: grid; grid-template-columns: repeat(5, 1fr); gap: 0; }
.step { position: relative; padding: 0 12px 0 0; }
.step .s-top { display: flex; align-items: center; }
.step .s-num {
  width: 30px; height: 30px; border-radius: 50%; flex: none;
  display: grid; place-items: center; font-weight: 700; font-size: 13px;
  border: 2px solid var(--border); color: var(--faint); background: var(--panel);
}
.step .s-line { height: 2px; background: var(--border); flex: 1; margin-left: 8px; }
.step:last-child .s-line { display: none; }
.step h3 { font-size: 13.5px; margin: 10px 0 2px; }
.step p { margin: 0; font-size: 12.5px; color: var(--muted); }
.step .s-state { font-size: 12px; font-weight: 600; margin-top: 6px; }
.step.done .s-num { background: var(--ok); border-color: var(--ok); color: #fff; }
.step.done .s-line { background: var(--ok); }
.step.active .s-num { border-color: var(--brand); color: var(--brand); box-shadow: 0 0 0 4px var(--brand-soft); }
.step.skip .s-num { border-color: var(--warn); color: var(--warn); }
.step.wait .s-num { border-color: var(--warn); color: var(--warn); }
.step.done .s-state { color: var(--ok); }
.step.active .s-state { color: var(--brand); }
.step.skip .s-state, .step.wait .s-state { color: var(--warn); }
.step.idle .s-state { color: var(--faint); }

/* ---------- KPI tiles ---------- */
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 20px; }
.kpi { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 18px; box-shadow: var(--shadow); }
.kpi .k-label { color: var(--muted); font-size: 12.5px; font-weight: 500; display: flex; justify-content: space-between; align-items: center; }
.kpi .k-value { font-size: 28px; font-weight: 700; letter-spacing: -.02em; margin: 6px 0 2px; font-variant-numeric: tabular-nums; }
.kpi .k-help { color: var(--faint); font-size: 12px; }
.kpi .k-dot { width: 8px; height: 8px; border-radius: 50%; }
.k-dot.ok { background: var(--ok); } .k-dot.warn { background: var(--warn); }
.k-dot.bad { background: var(--bad); } .k-dot.idle { background: var(--idle); }

/* ---------- service map ---------- */
.flow { display: grid; grid-template-columns: 110px 40px minmax(0, 1fr) 40px minmax(0, 1fr); align-items: center; gap: 0; }
.flow .col.two { display: grid; grid-template-columns: 1fr; gap: 12px; }
.flow .arrow { color: var(--faint); text-align: center; font-size: 22px; }
.flow .col { display: flex; flex-direction: column; gap: 12px; }
.node {
  border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; background: var(--panel-2);
  border-left: 4px solid var(--idle);
}
.node.ok { border-left-color: var(--ok); }
.node.warn { border-left-color: var(--warn); }
.node.bad { border-left-color: var(--bad); }
.node .n-top { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.node .n-name { font-weight: 600; }
.node .n-role { color: var(--muted); font-size: 12px; margin: 2px 0 8px; }
.node .n-meta { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; color: var(--muted); }
.node .n-meta b { color: var(--text); font-weight: 600; font-variant-numeric: tabular-nums; }
.customer {
  border: 1px dashed var(--border); border-radius: 10px; padding: 14px 10px; text-align: center;
  color: var(--muted); font-size: 12.5px;
}
.customer svg { width: 22px; height: 22px; display: block; margin: 0 auto 4px; color: var(--faint); }

/* ---------- chart ---------- */
.chart svg { width: 100%; height: auto; display: block; overflow: visible; }
.chart .grid line { stroke: var(--border); }
.chart text { fill: var(--faint); font-size: 12.5px; font-family: inherit; }
.chart .marker line { stroke: var(--warn); stroke-dasharray: 4 3; }
.chart .marker text { fill: var(--warn); font-weight: 600; }
.chart .empty { fill: var(--faint); font-size: 13px; }
.legend { display: flex; gap: 16px; font-size: 12px; color: var(--muted); margin-top: 8px; flex-wrap: wrap; }
.legend span::before { content: ""; display: inline-block; width: 12px; height: 2px; vertical-align: middle; margin-right: 6px; background: var(--c); }
.legend .dash::before { background: none; border-top: 2px dashed var(--c); height: 0; }

/* ---------- lists & tables ---------- */
.feed { list-style: none; margin: 0; padding: 0; }
.feed li { display: flex; gap: 12px; padding: 10px 0; border-top: 1px solid var(--border); }
.feed li:first-child { border-top: 0; padding-top: 0; }
.feed .f-icon { width: 28px; height: 28px; border-radius: 8px; display: grid; place-items: center; flex: none; }
.feed .f-icon svg { width: 15px; height: 15px; }
.feed .f-text { min-width: 0; }
.feed .f-when { color: var(--faint); font-size: 12px; }

.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th {
  text-align: left; font-size: 11.5px; text-transform: uppercase; letter-spacing: .05em;
  color: var(--faint); font-weight: 600; padding: 10px 14px; border-bottom: 1px solid var(--border);
  background: var(--panel-2); white-space: nowrap;
}
td { padding: 12px 14px; border-bottom: 1px solid var(--border); vertical-align: top; }
tr:last-child td { border-bottom: 0; }
tr.click { cursor: pointer; }
tr.click:hover td { background: var(--panel-2); }
td .sub { color: var(--faint); font-size: 12px; }
.nowrap { white-space: nowrap; }
.empty-state { text-align: center; padding: 36px 20px; color: var(--muted); }
.empty-state svg { width: 36px; height: 36px; color: var(--faint); margin-bottom: 8px; }
.empty-state h3 { margin: 0 0 4px; font-size: 15px; color: var(--text); }

.tabs { display: inline-flex; background: var(--panel); border: 1px solid var(--border); border-radius: 9px; padding: 3px; margin-bottom: 16px; }
.tabs a { padding: 6px 14px; border-radius: 6px; color: var(--muted); font-weight: 600; font-size: 13px; }
.tabs a:hover { text-decoration: none; color: var(--text); }
.tabs a.on { background: var(--brand-soft); color: var(--brand); }

/* ---------- incident detail ---------- */
.crumb { font-size: 13px; margin-bottom: 12px; display: inline-flex; gap: 6px; align-items: center; }
.inc-head { display: flex; gap: 16px; align-items: flex-start; flex-wrap: wrap; margin-bottom: 20px; }
.inc-head h2 { margin: 0 0 6px; font-size: 20px; letter-spacing: -.01em; }
.inc-meta { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.verdict { font-size: 18px; font-weight: 700; margin: 0 0 4px; letter-spacing: -.01em; }
.meter { display: flex; gap: 4px; margin: 10px 0 4px; }
.meter i { height: 6px; flex: 1; border-radius: 3px; background: var(--border); }
.meter.high i { background: var(--ok); }
.meter.medium i:nth-child(-n+2) { background: var(--warn); }
.meter.low i:first-child { background: var(--bad); }
.callout { border-radius: 10px; padding: 12px 14px; background: var(--panel-2); border: 1px solid var(--border); margin-top: 14px; }
.callout h4 { margin: 0 0 3px; font-size: 12px; text-transform: uppercase; letter-spacing: .05em; color: var(--faint); }
.callout p { margin: 0; }
.callout.hint { background: var(--brand-soft); border-color: transparent; }
.callout.warn { background: var(--warn-soft); border-color: transparent; }

.cause { padding: 12px 0; border-top: 1px solid var(--border); }
.cause:first-child { border-top: 0; padding-top: 0; }
.cause .c-row { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; }
.cause .c-name { font-weight: 600; }
.cause .c-score { font-variant-numeric: tabular-nums; color: var(--muted); font-size: 12.5px; white-space: nowrap; }
.bar { height: 8px; background: var(--idle-soft); border-radius: 4px; margin: 8px 0 4px; overflow: hidden; }
.bar i { display: block; height: 100%; border-radius: 4px; background: var(--brand); }
.bar.top i { background: var(--bad); }
details summary { cursor: pointer; color: var(--brand); font-size: 12.5px; font-weight: 600; list-style: none; }
details summary::-webkit-details-marker { display: none; }
details summary::before { content: "+ "; }
details[open] summary::before { content: "− "; }
.ev { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 10px; }
.ev h5 { margin: 0 0 4px; font-size: 12px; }
.ev ul { margin: 0; padding-left: 18px; color: var(--muted); font-size: 12.5px; }
.ev .for h5 { color: var(--ok); }
.ev .against h5 { color: var(--bad); }

.facts { margin: 0; padding-left: 18px; color: var(--muted); }
.facts li { margin: 3px 0; }

.timeline { list-style: none; margin: 0; padding: 0; position: relative; }
.timeline li { position: relative; padding: 0 0 18px 26px; }
.timeline li::before { content: ""; position: absolute; left: 7px; top: 18px; bottom: -2px; width: 2px; background: var(--border); }
.timeline li:last-child { padding-bottom: 0; }
.timeline li:last-child::before { display: none; }
.timeline .t-dot { position: absolute; left: 0; top: 3px; width: 16px; height: 16px; border-radius: 50%; border: 3px solid var(--panel); box-shadow: 0 0 0 1px var(--border); background: var(--idle); }
.timeline .t-dot.ok { background: var(--ok); } .timeline .t-dot.bad { background: var(--bad); }
.timeline .t-dot.warn { background: var(--warn); } .timeline .t-dot.info { background: var(--brand); }
.timeline .t-title { font-weight: 600; }
.timeline .t-when { color: var(--faint); font-size: 12px; }
.timeline .t-text { color: var(--muted); font-size: 12.5px; margin-top: 2px; }

/* ---------- automation & guide ---------- */
.modes { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.mode { border: 1px solid var(--border); border-radius: 10px; padding: 14px; background: var(--panel-2); }
.mode.on { border-color: var(--brand); background: var(--brand-soft); }
.mode h4 { margin: 0 0 4px; display: flex; justify-content: space-between; align-items: center; }
.mode p { margin: 0; color: var(--muted); font-size: 12.5px; }
.rules { list-style: none; padding: 0; margin: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 10px 20px; }
.rules li { display: flex; gap: 10px; align-items: flex-start; }
.rules svg { width: 18px; height: 18px; color: var(--ok); flex: none; margin-top: 1px; }

.loop { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
.loop .l { border: 1px solid var(--border); border-radius: 10px; padding: 14px; background: var(--panel-2); }
.loop .l b { display: block; font-size: 12px; color: var(--brand); margin-bottom: 2px; }
.loop .l h4 { margin: 0 0 4px; }
.loop .l p { margin: 0; color: var(--muted); font-size: 12.5px; }
.gloss { display: grid; grid-template-columns: 180px 1fr; gap: 10px 20px; margin: 0; }
.gloss dt { font-weight: 600; }
.gloss dd { margin: 0; color: var(--muted); }
.demo { margin: 0; padding-left: 20px; }
.demo li { margin: 8px 0; }
.demo code { background: var(--panel-2); border: 1px solid var(--border); padding: 2px 6px; border-radius: 5px; }

.error-box { background: var(--bad-soft); color: var(--bad); border-radius: 10px; padding: 12px 16px; margin-bottom: 20px; font-weight: 500; }

/* ---------- responsive ---------- */
@media (max-width: 1100px) {
  .kpis { grid-template-columns: repeat(2, 1fr); }
  .split, .grid-2 { grid-template-columns: 1fr; }
  .loop { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 860px) {
  .app { grid-template-columns: minmax(0, 1fr); }
  .sidebar { position: static; height: auto; padding: 12px; min-width: 0; }
  .nav { min-width: 0; scrollbar-width: none; }
  .topbar { flex-wrap: wrap; gap: 8px; }
  .topbar .right { margin-left: 0; }
  .brand { padding-bottom: 10px; }
  .nav { flex-direction: row; overflow-x: auto; }
  .nav-label, .side-foot { display: none; }
  .nav a { white-space: nowrap; }
  .topbar, .content { padding-left: 16px; padding-right: 16px; }
  .topbar { position: static; }
  .pipeline { grid-template-columns: 1fr; gap: 14px; }
  .step .s-line { display: none; }
  .flow { grid-template-columns: 1fr; }
  .flow .arrow { transform: rotate(90deg); }
  .customer { width: auto; }
  .modes, .rules, .ev, .loop { grid-template-columns: 1fr; }
  .gloss { grid-template-columns: 1fr; }
  .gloss dd { margin-bottom: 8px; }
}
@media (max-width: 520px) {
  .kpis { grid-template-columns: 1fr; }
  .banner { flex-wrap: wrap; }
  .banner .b-action { margin-left: 56px; }
}
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="brand">
      <svg width="32" height="32" viewBox="0 0 32 32" aria-hidden="true">
        <rect width="32" height="32" rx="9" fill="#4f46e5"/>
        <circle cx="14" cy="14" r="7" fill="none" stroke="#fff" stroke-width="2.4"/>
        <path d="M19.2 19.2 25 25" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/>
        <path d="M9.5 14h2.2l1.3-2.6 2 5 1.3-2.4h2.2" fill="none" stroke="#a5b4fc" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <div>
        <div class="brand-name">RootLens</div>
        <div class="brand-sub">Reliability control room</div>
      </div>
    </div>
    <nav class="nav" id="nav"></nav>
    <div class="side-foot">
      <div class="row"><span>Robot mode</span><span id="side-mode" class="tag">—</span></div>
      <div class="row"><a id="link-grafana" target="_blank" rel="noopener">Grafana ↗</a><a id="link-prom" target="_blank" rel="noopener">Prometheus ↗</a></div>
    </div>
  </aside>

  <div class="main">
    <header class="topbar">
      <div>
        <h1 id="page-title">Overview</h1>
        <p id="page-sub"></p>
      </div>
      <div class="right">
        <div class="live"><span class="dot" id="live-dot"></span><span id="live-text">Connecting…</span></div>
        <button class="icon-btn" id="theme-btn" title="Switch light / dark" aria-label="Switch light or dark theme">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>
        </button>
      </div>
    </header>
    <div class="content" id="view"></div>
  </div>
</div>

<script>
"use strict";

/* ===================== icons ===================== */
const ICON = {
  overview: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  incidents: '<path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4M12 17.5v.01"/>',
  deploy: '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M4 21h16"/>',
  robot: '<rect x="4" y="8" width="16" height="11" rx="3"/><path d="M12 4v4M9 13v1M15 13v1"/>',
  guide: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 1-1 1.7M12 17v.01"/>',
  check: '<path d="M5 12.5 10 17l9-10"/>',
  alert: '<path d="M12 7v6M12 16.5v.01"/><circle cx="12" cy="12" r="9"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
  up: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  shield: '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6l-8-3z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  inbox: '<path d="M3 13h5l1.5 3h5L16 13h5"/><path d="M5 5h14l2 8v6H3v-6z"/>',
};
function icon(name, extra) {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"' +
    (extra ? ' ' + extra : '') + '>' + ICON[name] + '</svg>';
}

/* ===================== pages ===================== */
const PAGES = [
  { id: "overview", label: "Overview", icon: "overview",
    title: "Overview", sub: "Is everything working right now?" },
  { id: "incidents", label: "Incidents", icon: "incidents",
    title: "Incidents", sub: "Every problem RootLens has detected, and what it found" },
  { id: "deployments", label: "Deployments", icon: "deploy",
    title: "Deployments", sub: "Every new version released, and every rollback" },
  { id: "automation", label: "Automation", icon: "robot",
    title: "Automation", sub: "What the RootLens robot decided, and why" },
  { id: "guide", label: "How it works", icon: "guide",
    title: "How RootLens works", sub: "A short guide for first-time viewers" },
];

// Fallback names; the live ones come from services.yaml via /api/overview.
const SERVICES = {
  "order-service": { name: "Order service", role: "Front door: takes customer orders" },
  "user-service": { name: "User service", role: "Looks up the customer's account" },
  "payment-service": { name: "Payment service", role: "Charges the customer's payment" },
};

/* ===================== state ===================== */
const S = {
  overview: null,
  incidents: [],
  deployments: [],
  audit: [],
  diag: {},
  updated: 0,
  error: null,
  loaded: false,
};
const openDetails = new Set();

/* ===================== helpers ===================== */
function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}
function ts(iso) {
  if (!iso) return null;
  const t = Date.parse(String(iso).replace(/(\.\d{3})\d+/, "$1"));
  return isNaN(t) ? null : t;
}
function duration(ms) {
  if (ms == null || ms < 0) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return s + " s";
  const m = Math.floor(s / 60);
  if (m < 60) return m + " min" + (s % 60 ? " " + (s % 60) + " s" : "");
  const h = Math.floor(m / 60);
  if (h < 48) return h + " h" + (m % 60 ? " " + (m % 60) + " min" : "");
  return Math.floor(h / 24) + " days";
}
function ago(iso) {
  const t = ts(iso);
  if (t == null) return "—";
  const ms = Date.now() - t;
  if (ms < 10000) return "just now";
  return duration(ms) + " ago";
}
function clock(iso) {
  const t = ts(iso);
  if (t == null) return "—";
  const d = new Date(t);
  const today = new Date().toDateString() === d.toDateString();
  return d.toLocaleString([], today
    ? { hour: "2-digit", minute: "2-digit", second: "2-digit" }
    : { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
function when(iso) {
  return '<span title="' + esc(clock(iso)) + '">' + esc(ago(iso)) + "</span>";
}
function pct(x, digits) {
  if (x == null) return "—";
  return (x * 100).toFixed(digits == null ? 0 : digits) + "%";
}
function topo() { return (S.overview && S.overview.topology) || {}; }
function svcInfo(id) {
  return (topo().display || {})[id] || SERVICES[id] || { name: id, role: "" };
}
function entryId() { return topo().entry || "order-service"; }
function dependencyIds() { return topo().dependencies || ["user-service", "payment-service"]; }
function svcName(id) { return (id && svcInfo(id).name) || id || "unknown"; }
function byTimeDesc(key) { return function (a, b) { return (ts(b[key]) || 0) - (ts(a[key]) || 0); }; }
function card(title, sub, body, extraClass) {
  return '<section class="card ' + (extraClass || "") + '"><div class="card-head"><h2>' + esc(title) + "</h2>" +
    (sub ? "<p>" + esc(sub) + "</p>" : "") + '</div><div class="card-body">' + body + "</div></section>";
}
function emptyState(iconName, title, text) {
  return '<div class="empty-state">' + icon(iconName) + "<h3>" + esc(title) + "</h3><div>" + text + "</div></div>";
}
function isLive(inc) { return !!inc.fingerprint; }
function activeIncidents() {
  return S.incidents.filter(function (i) { return i.status === "open" && isLive(i); });
}

/* ---------- plain-language translations ---------- */
function serviceState(s, kind) {
  if (!s || s.up == null) return { k: "idle", label: "No data" };
  if (s.up === 0) return { k: "bad", label: "Down" };
  if (kind === "order") {
    const fr = S.overview && S.overview.failure_ratio;
    if (fr != null && fr >= 0.1) return { k: "warn", label: "Affected" };
    return { k: "ok", label: "Healthy" };
  }
  const err = s.error_ratio || 0;
  const tmo = s.timeout_rate || 0;
  if (err >= 0.1 || tmo > 0.05) return { k: "bad", label: "Failing" };
  if (err > 0.02) return { k: "warn", label: "Degraded" };
  return { k: "ok", label: "Healthy" };
}
function confidenceText(c) {
  if (c === "high") return "High confidence: strong evidence points to this one cause.";
  if (c === "medium") return "Medium confidence: the evidence leans this way but is not conclusive.";
  if (c === "low") return "Low confidence: not enough evidence to be sure.";
  return "Confidence not available.";
}
function decisionText(a) {
  if (!a) return "";
  if (a.decision === "act") {
    if (a.action === "rollback_deployment") {
      return "Roll back " + svcName(a.service) + (a.target_version ? " to " + a.target_version : "");
    }
    if (a.action === "restart_service") return "Restart " + svcName(a.service);
    return (a.action || "Act") + " on " + svcName(a.service);
  }
  if (a.decision === "wait") return "Wait, then check again";
  return "Hand over to a human";
}
function outcomeInfo(outcome) {
  const o = outcome || "";
  if (o.startsWith("Executed")) return { k: "ok", label: "Done", text: o.replace(/^Executed:?\s*/, "") };
  if (o.startsWith("Execution failed")) return { k: "bad", label: "Failed", text: o.replace(/^Execution failed:?\s*/, "") };
  if (o.startsWith("Not executed")) return { k: "idle", label: "Suggested only", text: "Dry-run mode: the robot only suggests fixes." };
  if (o.startsWith("Needs a human")) return { k: "warn", label: "Needs a human", text: "" };
  if (o.startsWith("Refused")) return { k: "idle", label: "Refused", text: o.replace(/^Refused:?\s*/, "") };
  if (o.startsWith("No action")) return { k: "idle", label: "No action", text: "" };
  return { k: "idle", label: o || "—", text: "" };
}
function decisionPill(d) {
  if (d === "act") return '<span class="pill t-brand">Fix</span>';
  if (d === "wait") return '<span class="pill t-idle">Wait</span>';
  return '<span class="pill t-warn">Escalate</span>';
}
function robotMode() {
  const latest = S.audit.slice().sort(byTimeDesc("logged_at"))[0];
  return latest ? latest.mode : null;
}
function isRollback(dep) {
  // "v1.10.2" -> [1, 10, 2], compared part by part (same rule as investigator.py).
  const n = function (v) {
    const m = String(v || "").trim().match(/^v?(\d+(?:\.\d+)*)$/);
    return m ? m[1].split(".").map(Number) : null;
  };
  const a = n(dep.version), b = n(dep.previous_version);
  if (a == null || b == null) return false;
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const x = a[i] || 0, y = b[i] || 0;
    if (x !== y) return x < y;
  }
  return false;
}
function deployer(dep) {
  const by = dep.deployed_by || "unknown";
  if (by === "rootlens-controller") return '<span class="tag">' + "RootLens robot" + "</span>";
  if (by === "deploy.ps1") return '<span class="tag">' + "Engineer (deploy.ps1)" + "</span>";
  return '<span class="tag">' + esc(by) + "</span>";
}

/* ===================== chart ===================== */
function niceMax(v, floor) {
  const m = Math.max(v || 0, floor);
  const steps = [0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 500, 1000];
  for (let i = 0; i < steps.length; i++) if (steps[i] >= m * 1.1) return steps[i];
  return m * 1.2;
}
function lineChart(points, opts) {
  const W = 600, H = 210, L = 46, R = 12, T = 16, B = 28;
  const end = Date.now() / 1000;
  const start = end - 15 * 60;
  const pw = W - L - R, ph = H - T - B;
  const vals = points.map(function (p) { return p[1]; }).filter(function (v) { return v != null; });
  let out = '<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc(opts.label) + '">';

  if (!vals.length) {
    out += '<text class="empty" x="' + (W / 2) + '" y="' + (H / 2) + '" text-anchor="middle">No data yet. Start traffic.py to see live numbers.</text></svg>';
    return '<div class="chart">' + out + "</div>";
  }

  const max = niceMax(Math.max.apply(null, vals), opts.floor);
  const x = function (t) { return L + ((t - start) / (end - start)) * pw; };
  const y = function (v) { return T + ph - (v / max) * ph; };

  out += '<g class="grid">';
  [0, 0.5, 1].forEach(function (f) {
    const yy = T + ph - f * ph;
    out += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + yy + '" y2="' + yy + '"/>';
    out += '<text x="' + (L - 8) + '" y="' + (yy + 4) + '" text-anchor="end">' + esc(opts.format(max * f)) + "</text>";
  });
  out += "</g>";
  [15, 10, 5, 0].forEach(function (m) {
    const xx = x(end - m * 60);
    out += '<text x="' + xx + '" y="' + (H - 6) + '" text-anchor="' + (m === 0 ? "end" : m === 15 ? "start" : "middle") + '">' +
      (m === 0 ? "now" : m + " min ago") + "</text>";
  });

  (opts.markers || []).forEach(function (mk) {
    if (mk.t < start || mk.t > end) return;
    const xx = x(mk.t);
    out += '<g class="marker"><line x1="' + xx + '" x2="' + xx + '" y1="' + T + '" y2="' + (T + ph) + '"/>' +
      '<text x="' + (xx + 4) + '" y="' + (T + 10) + '">' + esc(mk.label) + "</text></g>";
  });

  // Split the line where data is missing.
  const segments = [];
  let current = [];
  points.forEach(function (p) {
    if (p[1] == null || p[0] < start) { if (current.length) segments.push(current); current = []; }
    else current.push([x(p[0]), y(Math.min(p[1], max))]);
  });
  if (current.length) segments.push(current);

  segments.forEach(function (seg) {
    const line = seg.map(function (p, i) { return (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1); }).join(" ");
    const area = line + " L" + seg[seg.length - 1][0].toFixed(1) + " " + (T + ph) + " L" + seg[0][0].toFixed(1) + " " + (T + ph) + " Z";
    out += '<path d="' + area + '" fill="' + opts.color + '" opacity="0.10"/>';
    out += '<path d="' + line + '" fill="none" stroke="' + opts.color + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>';
  });

  out += "</svg>";
  return '<div class="chart">' + out + "</div>";
}
function deploymentMarkers() {
  return S.deployments.map(function (d) {
    const t = ts(d.deployed_at);
    return { t: t == null ? 0 : t / 1000, label: (isRollback(d) ? "rollback " : "") + (d.service || "").replace("-service", "") + " " + d.version };
  });
}
function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

/* ===================== Overview ===================== */
function statusBanner() {
  const ov = S.overview || {};
  const active = activeIncidents();
  const fr = ov.failure_ratio;

  if (active.length) {
    const inc = active[0];
    const d = S.diag[inc.incident_id];
    const cause = d && d.likely_cause ? d.likely_cause.charAt(0).toUpperCase() + d.likely_cause.slice(1) + "." : esc(inc.message);
    const extra = active.length > 1 ? " (+" + (active.length - 1) + " more)" : "";
    return '<div class="banner bad"><div class="b-icon">' + icon("alert") + "</div><div>" +
      "<h2>Problem detected" + extra + "</h2><p>" + esc(cause) + " Started " + esc(ago(inc.detected_at)) +
      ". " + (fr != null ? pct(fr) + " of customer orders are failing right now." : "") + "</p></div>" +
      '<a class="btn b-action" href="#/incidents/' + esc(inc.incident_id) + '">See what RootLens found →</a></div>';
  }
  if (fr == null) {
    return '<div class="banner idle"><div class="b-icon">' + icon("clock") + "</div><div>" +
      "<h2>Waiting for traffic</h2><p>No customer orders in the last minute, so there is nothing to measure yet. " +
      "Run <code>python traffic.py</code> to send test orders.</p></div></div>";
  }
  if (fr >= 0.05) {
    return '<div class="banner warn"><div class="b-icon">' + icon("alert") + "</div><div>" +
      "<h2>Something looks wrong</h2><p>" + pct(fr) + " of orders are failing. RootLens is checking whether this is a real incident.</p></div></div>";
  }
  return '<div class="banner ok"><div class="b-icon">' + icon("check") + "</div><div>" +
    "<h2>All systems healthy</h2><p>Customer orders are going through normally. RootLens is watching and will step in if anything breaks.</p></div></div>";
}

function pipelineSteps() {
  const inc = activeIncidents()[0] || null;
  const steps = [
    { title: "Detect", text: "Notice that orders are failing" },
    { title: "Investigate", text: "Collect evidence and rank likely causes" },
    { title: "Decide", text: "Check the safety rules and pick a fix" },
    { title: "Act", text: "Roll back or restart, if allowed" },
    { title: "Verify", text: "Confirm customers are OK again" },
  ];

  if (!inc) {
    steps.forEach(function (s, i) { s.cls = i === 0 ? "active" : "idle"; s.state = i === 0 ? "Watching…" : "Standing by"; });
  } else {
    const d = S.diag[inc.incident_id];
    const latest = S.audit.filter(function (a) { return a.incident_id === inc.incident_id; }).sort(byTimeDesc("logged_at"))[0];

    steps[0].cls = "done"; steps[0].state = "Detected " + ago(inc.detected_at);
    if (d) { steps[1].cls = "done"; steps[1].state = "Likely: " + (d.likely_cause || "unclear"); }
    else { steps[1].cls = "active"; steps[1].state = "In progress…"; }

    if (!latest) {
      steps[2].cls = d ? "active" : "idle"; steps[2].state = d ? "In progress…" : "Waiting";
      steps[3].cls = "idle"; steps[3].state = "Waiting";
    } else {
      steps[2].cls = "done"; steps[2].state = decisionText(latest);
      const o = outcomeInfo(latest.outcome);
      if (o.k === "ok") { steps[3].cls = "done"; steps[3].state = "Fix applied"; }
      else if (o.k === "bad") { steps[3].cls = "wait"; steps[3].state = "Fix failed"; }
      else if (latest.decision === "escalate") { steps[3].cls = "wait"; steps[3].state = "Needs a human"; }
      else if (latest.decision === "wait") { steps[3].cls = "active"; steps[3].state = "Waiting to retry"; }
      else { steps[3].cls = "skip"; steps[3].state = "Skipped (dry-run)"; }
    }
    steps[4].cls = steps[3].cls === "done" ? "active" : "idle";
    steps[4].state = steps[3].cls === "done" ? "Checking recovery…" : "Waiting";
  }

  const html = steps.map(function (s, i) {
    return '<div class="step ' + s.cls + '"><div class="s-top"><div class="s-num">' +
      (s.cls === "done" ? "✓" : i + 1) + '</div><div class="s-line"></div></div><h3>' + esc(s.title) + "</h3><p>" +
      esc(s.text) + '</p><div class="s-state">' + esc(s.state) + "</div></div>";
  }).join("");

  return card("What RootLens is doing", inc
    ? "Following the current incident through RootLens's five steps"
    : "RootLens runs these five steps whenever something breaks",
    '<div class="pipeline">' + html + "</div>");
}

function kpiTiles() {
  const ov = S.overview || {};
  const fr = ov.failure_ratio;
  const success = fr == null ? null : 1 - fr;
  const p95 = ov.p95_latency_seconds;
  const rate = ov.request_rate;
  const open = activeIncidents().length;

  function tone(v, good, ok) { return v == null ? "idle" : v >= good ? "ok" : v >= ok ? "warn" : "bad"; }
  const tiles = [
    { label: "Orders succeeding", value: success == null ? "—" : pct(success, success >= 0.995 ? 0 : 1),
      help: "Share of orders that worked in the last minute", tone: tone(success, 0.99, 0.9) },
    { label: "Orders per second", value: rate == null ? "—" : rate.toFixed(1),
      help: "How busy the system is right now", tone: rate == null ? "idle" : "ok" },
    { label: "Response time", value: p95 == null ? "—" : p95 < 1 ? Math.round(p95 * 1000) + " ms" : p95.toFixed(2) + " s",
      help: "95% of orders finish faster than this", tone: p95 == null ? "idle" : p95 < 0.5 ? "ok" : p95 < 2 ? "warn" : "bad" },
    { label: "Open incidents", value: String(open),
      help: open ? "Problems being handled now" : "Nothing needs attention", tone: open ? "bad" : "ok" },
  ];
  return '<div class="kpis">' + tiles.map(function (t) {
    return '<div class="kpi"><div class="k-label">' + esc(t.label) + '<span class="k-dot ' + t.tone + '"></span></div>' +
      '<div class="k-value">' + esc(t.value) + '</div><div class="k-help">' + esc(t.help) + "</div></div>";
  }).join("") + "</div>";
}

function serviceNode(id, s, kind) {
  const st = serviceState(s, kind);
  let meta = "";
  if (kind !== "order") {
    meta = '<div class="n-meta"><span>Version <b>' + esc((s && s.version) || "—") + "</b></span>" +
      "<span>Errors <b>" + pct(s && s.error_ratio) + "</b></span>" +
      "<span>Timeouts <b>" + (s && s.timeout_rate != null ? s.timeout_rate.toFixed(2) + "/s" : "—") + "</b></span></div>";
  } else {
    const fr = S.overview && S.overview.failure_ratio;
    meta = '<div class="n-meta"><span>Failed orders <b>' + pct(fr) + "</b></span></div>";
  }
  return '<div class="node ' + st.k + '"><div class="n-top"><span class="n-name">' + esc(svcName(id)) +
    '</span><span class="pill t-' + st.k + '">' + esc(st.label) + '</span></div><div class="n-role">' +
    esc(svcInfo(id).role) + "</div>" + meta + "</div>";
}

function serviceMap() {
  const sv = (S.overview && S.overview.services) || {};
  const body = '<div class="flow">' +
    '<div class="customer">' + icon("user") + "Customer</div>" +
    '<div class="arrow">→</div>' +
    '<div class="col">' + serviceNode(entryId(), sv[entryId()], "order") + "</div>" +
    '<div class="arrow">→</div>' +
    '<div class="col">' + dependencyIds().map(function (id) { return serviceNode(id, sv[id]); }).join("") + "</div>" +
    "</div>" +
    '<p class="small faint" style="margin:14px 0 0">Every request to the ' + esc(svcName(entryId()).toLowerCase()) + " calls the services on the right. If any of them fails, the request fails, so the " + esc(svcName(entryId()).toLowerCase()) + " shows as <b>Affected</b>.</p>";
  return card("Services", "How a customer order moves through the system", body);
}

function activityFeed(limit) {
  const events = [];
  S.incidents.forEach(function (i) {
    events.push({ t: i.detected_at, k: "bad", ic: "alert",
      title: "Problem detected" + (isLive(i) ? "" : " (manual test)"),
      text: i.message + (i.service ? " · " + svcName(i.service) : ""), link: "#/incidents/" + i.incident_id });
    if (i.resolved_at) events.push({ t: i.resolved_at, k: "ok", ic: "check", title: "Problem resolved",
      text: "Orders are succeeding again · lasted " + duration(ts(i.resolved_at) - ts(i.detected_at)), link: "#/incidents/" + i.incident_id });
  });
  S.deployments.forEach(function (d) {
    const rb = isRollback(d);
    events.push({ t: d.deployed_at, k: rb ? "warn" : "brand", ic: rb ? "undo" : "up",
      title: (rb ? "Rolled back " : "Released ") + svcName(d.service) + " " + d.version,
      text: "From " + (d.previous_version || "none") + " · by " + (d.deployed_by === "rootlens-controller" ? "RootLens robot" : (d.deployed_by || "unknown")),
      link: "#/deployments" });
  });
  S.audit.forEach(function (a) {
    const o = outcomeInfo(a.outcome);
    events.push({ t: a.logged_at, k: "brand", ic: "robot", title: "Robot: " + decisionText(a),
      text: o.label + (a.mode ? " · " + a.mode + " mode" : ""), link: "#/automation" });
  });
  events.sort(function (a, b) { return (ts(b.t) || 0) - (ts(a.t) || 0); });
  const items = events.slice(0, limit);
  if (!items.length) return emptyState("inbox", "Nothing has happened yet", "Releases, problems and robot decisions will appear here.");
  const tone = { bad: "t-bad", ok: "t-ok", warn: "t-warn", brand: "t-brand" };
  return '<ul class="feed">' + items.map(function (e) {
    return '<li><div class="f-icon ' + tone[e.k] + '">' + icon(e.ic) + '</div><div class="f-text"><div><a href="' + esc(e.link) + '">' +
      esc(e.title) + '</a></div><div class="small muted">' + esc(e.text) + '</div><div class="f-when">' + when(e.t) + "</div></div></li>";
  }).join("") + "</ul>";
}

function viewOverview() {
  const h = (S.overview && S.overview.history) || {};
  const markers = deploymentMarkers();
  const failChart = lineChart(h.failure_ratio || [], {
    label: "Failed orders", color: cssVar("--bad"), floor: 0.05, markers: markers,
    format: function (v) { return Math.round(v * 100) + "%"; },
  });
  const rateChart = lineChart(h.request_rate || [], {
    label: "Orders per second", color: cssVar("--brand"), floor: 1, markers: markers,
    format: function (v) { return v >= 10 ? Math.round(v) : v.toFixed(1); },
  });
  const legend = function (name, color) {
    return '<div class="legend"><span style="--c:' + color + '">' + esc(name) + '</span><span class="dash" style="--c:' + cssVar("--warn") + '">A release or rollback</span></div>';
  };

  return statusBanner() + pipelineSteps() + kpiTiles() + serviceMap() +
    '<div class="grid-2">' +
      card("Failed orders", "Share of customer orders that failed, last 15 minutes", failChart + legend("Failed orders (%)", cssVar("--bad"))) +
      card("Traffic", "Customer orders per second, last 15 minutes", rateChart + legend("Orders per second", cssVar("--brand"))) +
    "</div>" +
    card("Recent activity", "The latest events, newest first", activityFeed(6));
}

/* ===================== Incidents ===================== */
function incidentStatus(i) {
  if (!isLive(i)) return '<span class="pill t-idle">Test record</span>';
  if (i.status === "open") return '<span class="pill t-bad">Open</span>';
  return '<span class="pill t-ok">Resolved</span>';
}
function incidentDuration(i) {
  const start = ts(i.detected_at);
  const end = i.resolved_at ? ts(i.resolved_at) : (i.status === "open" ? Date.now() : null);
  return start != null && end != null ? duration(end - start) : "—";
}

function viewIncidents(filter) {
  const all = S.incidents.slice().sort(byTimeDesc("detected_at"));
  const counts = {
    all: all.length,
    open: all.filter(function (i) { return isLive(i) && i.status === "open"; }).length,
    resolved: all.filter(function (i) { return isLive(i) && i.status !== "open"; }).length,
  };
  const list = all.filter(function (i) {
    if (filter === "open") return isLive(i) && i.status === "open";
    if (filter === "resolved") return isLive(i) && i.status !== "open";
    return true;
  });
  const tab = function (id, label) {
    return '<a class="' + (filter === id ? "on" : "") + '" href="#/incidents' + (id === "all" ? "" : "?" + id) + '">' + label + " (" + counts[id] + ")</a>";
  };

  let body;
  if (!list.length) {
    body = emptyState("check", filter === "open" ? "No open incidents" : "No incidents yet",
      "When orders start failing, RootLens opens an incident here automatically.");
  } else {
    body = '<div class="table-wrap"><table><thead><tr><th>Status</th><th>What happened</th><th>Service</th><th>Started</th><th>Lasted</th><th></th></tr></thead><tbody>' +
      list.map(function (i) {
        const d = S.diag[i.incident_id];
        return '<tr class="click" data-href="#/incidents/' + esc(i.incident_id) + '"><td>' + incidentStatus(i) + "</td><td><div>" +
          esc(i.message) + '</div><div class="sub">' + (d && d.likely_cause ? "Likely cause: " + esc(d.likely_cause) : "ID " + esc(String(i.incident_id).slice(0, 8))) +
          "</div></td><td>" + esc(svcName(i.service)) + '</td><td class="nowrap">' + when(i.detected_at) + '</td><td class="nowrap">' +
          esc(incidentDuration(i)) + '</td><td class="nowrap"><a href="#/incidents/' + esc(i.incident_id) + '">Details →</a></td></tr>';
      }).join("") + "</tbody></table></div>";
  }

  return '<div class="tabs">' + tab("all", "All") + tab("open", "Open") + tab("resolved", "Resolved") + "</div>" +
    '<section class="card">' + body + "</section>" +
    '<p class="small faint">“Test record” rows come from the old manual test endpoint. Live incidents are opened and closed by the detector.</p>';
}

function causeList(d) {
  const ranked = d.ranked_causes || [];
  if (!ranked.length) return '<p class="muted">RootLens could not rank any causes for this incident.</p>';
  return ranked.slice(0, 6).map(function (c, idx) {
    const key = "cause-" + d.incident_id + "-" + idx;
    const sup = c.supporting_evidence || [], ag = c.contradicting_evidence || [];
    return '<div class="cause"><div class="c-row"><span class="c-name">' + (idx === 0 ? "★ " : "") + esc(c.cause) +
      '</span><span class="c-score">score ' + Number(c.score || 0).toFixed(2) + '</span></div>' +
      '<div class="bar' + (idx === 0 ? " top" : "") + '"><i style="width:' + Math.max(2, Math.min(100, (c.score || 0) * 100)) + '%"></i></div>' +
      '<details data-key="' + esc(key) + '"' + (openDetails.has(key) ? " open" : "") + "><summary>Why? (" + sup.length + " for, " + ag.length + " against)</summary>" +
      '<div class="ev"><div class="for"><h5>Evidence for</h5>' + (sup.length ? "<ul>" + sup.map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("") + "</ul>" : '<span class="small faint">None</span>') +
      '</div><div class="against"><h5>Evidence against</h5>' + (ag.length ? "<ul>" + ag.map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("") + "</ul>" : '<span class="small faint">None</span>') +
      "</div></div></details></div>";
  }).join("");
}

function incidentTimeline(inc, d, audits) {
  const items = [];
  if (d && d.trigger && d.trigger.deployed_at) {
    const tr = d.trigger;
    items.push({ t: tr.deployed_at, k: "warn", title: (tr.kind === "rollback" ? "Rollback: " : "Release: ") + svcName(tr.service) + " " + tr.version,
      text: "Changed from " + (tr.previous_version || "none") + ", " + duration((tr.seconds_before_detection || 0) * 1000) + " before the problem was noticed." });
  }
  items.push({ t: inc.detected_at, k: "bad", title: "Problem detected", text: inc.message });
  if (d) items.push({ t: inc.detected_at, k: "info", title: "Investigation finished", text: "Most likely: " + (d.likely_cause || "unclear") + " (" + (d.confidence || "?") + " confidence)", noTime: true });
  audits.forEach(function (a) {
    const o = outcomeInfo(a.outcome);
    items.push({ t: a.logged_at, k: o.k === "ok" ? "ok" : o.k === "bad" ? "bad" : "info", title: "Robot: " + decisionText(a),
      text: o.label + (o.text ? ": " + o.text : "") });
  });
  if (inc.resolved_at) items.push({ t: inc.resolved_at, k: "ok", title: "Resolved", text: "Orders are succeeding again." });
  items.sort(function (a, b) { return (ts(a.t) || 0) - (ts(b.t) || 0); });

  return '<ul class="timeline">' + items.map(function (it) {
    return '<li><span class="t-dot ' + it.k + '"></span><div class="t-title">' + esc(it.title) + '</div><div class="t-when">' +
      (it.noTime ? "Right after detection" : esc(clock(it.t))) + '</div><div class="t-text">' + esc(it.text) + "</div></li>";
  }).join("") + "</ul>";
}

function viewIncident(id) {
  const inc = S.incidents.find(function (i) { return i.incident_id === id; });
  const back = '<a class="crumb" href="#/incidents">← All incidents</a>';
  if (!inc) {
    return back + card("Incident not found", "", S.loaded ? "<p class=muted>This incident does not exist, or the database was reset.</p>" : "<p class=muted>Loading…</p>");
  }
  const d = S.diag[id];
  const audits = S.audit.filter(function (a) { return a.incident_id === id; }).sort(function (a, b) { return (ts(a.logged_at) || 0) - (ts(b.logged_at) || 0); });

  const head = '<div class="inc-head"><div><h2>' + esc(inc.message) + '</h2><div class="inc-meta">' + incidentStatus(inc) +
    '<span class="tag">' + esc(svcName(inc.service)) + '</span><span class="small muted">Started ' + esc(clock(inc.detected_at)) +
    " · lasted " + esc(incidentDuration(inc)) + '</span><span class="small faint mono">#' + esc(String(id).slice(0, 8)) + "</span></div></div></div>";

  let diagnosis;
  if (!d) {
    diagnosis = card("Diagnosis", "", '<p class="muted">Investigating…</p>');
  } else {
    const conf = d.confidence || "low";
    let body = '<div class="small muted">Most likely cause</div><p class="verdict">' + esc(d.likely_cause || "Unclear") + "</p>" +
      '<div class="meter ' + esc(conf) + '"><i></i><i></i><i></i></div><div class="small muted">' + esc(confidenceText(conf)) + "</div>";
    if (d.trigger) {
      const tr = d.trigger;
      body += '<div class="callout warn"><h4>What changed just before</h4><p>' + esc(svcName(tr.service)) + " was " +
        (tr.kind === "rollback" ? "rolled back" : "updated") + " from <b>" + esc(tr.previous_version || "none") + "</b> to <b>" + esc(tr.version) + "</b> " +
        esc(duration((tr.seconds_before_detection || 0) * 1000)) + " before the problem started" +
        (tr.strength === "strong" ? ", so this release is a prime suspect." : tr.strength === "moderate" ? ", so it may be related." : ".") + "</p></div>";
    }
    if (d.recommended_next_step) {
      body += '<div class="callout hint"><h4>Recommended next step</h4><p>' + esc(d.recommended_next_step) + "</p></div>";
    }
    if (d.impacted_services && d.impacted_services.length) {
      body += '<p class="small muted" style="margin:12px 0 0">Also affected: ' + d.impacted_services.map(function (s) { return esc(svcName(s)); }).join(", ") + "</p>";
    }
    diagnosis = card("Diagnosis", "RootLens's conclusion, in plain words", body);
  }

  const causes = d ? card("All possible causes", "Every explanation RootLens considered, scored from 0 to 1. Open one to see the evidence.", causeList(d)) : "";
  const facts = d && d.observed_facts && d.observed_facts.length
    ? card("What RootLens measured", "The raw observations at the moment of detection",
        '<ul class="facts">' + d.observed_facts.map(function (f) { return "<li>" + esc(f) + "</li>"; }).join("") + "</ul>")
    : "";

  return back + head + '<div class="split"><div class="stack">' + diagnosis + causes + facts + "</div>" +
    '<div class="stack">' + card("Timeline", "What happened, in order", incidentTimeline(inc, d, audits)) + "</div></div>";
}

/* ===================== Deployments ===================== */
function viewDeployments() {
  const sv = (S.overview && S.overview.services) || {};
  const current = dependencyIds().map(function (id) {
    const s = sv[id] || {};
    return '<div class="kpi"><div class="k-label">' + esc(svcName(id)) + '<span class="k-dot ' + serviceState(s).k + '"></span></div>' +
      '<div class="k-value">' + esc(s.version || "—") + '</div><div class="k-help">Version running right now</div></div>';
  }).join("");
  const total = S.deployments.length;
  const rollbacks = S.deployments.filter(isRollback).length;
  const byRobot = S.deployments.filter(function (d) { return d.deployed_by === "rootlens-controller"; }).length;
  const stats = '<div class="kpi"><div class="k-label">Releases recorded</div><div class="k-value">' + total +
    '</div><div class="k-help">' + rollbacks + " were rollbacks</div></div>" +
    '<div class="kpi"><div class="k-label">Done by the robot</div><div class="k-value">' + byRobot +
    '</div><div class="k-help">Automatic rollbacks by RootLens</div></div>';

  const list = S.deployments.slice().sort(byTimeDesc("deployed_at"));
  let body;
  if (!list.length) {
    body = emptyState("deploy", "No deployments yet", "Run <code>.\\deploy.ps1 -Service payment-service -Version v2</code> to release a new version.");
  } else {
    body = '<div class="table-wrap"><table><thead><tr><th>When</th><th>Service</th><th>Change</th><th>Type</th><th>Done by</th></tr></thead><tbody>' +
      list.map(function (d) {
        const rb = isRollback(d);
        return '<tr><td class="nowrap">' + when(d.deployed_at) + '<div class="sub">' + esc(clock(d.deployed_at)) + "</div></td><td>" + esc(svcName(d.service)) +
          '</td><td class="nowrap mono">' + esc(d.previous_version || "none") + " → <b>" + esc(d.version) + "</b></td><td>" +
          (rb ? '<span class="pill t-warn">Rollback</span>' : '<span class="pill t-brand">Release</span>') + "</td><td>" + deployer(d) + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }

  return '<div class="kpis">' + current + stats + "</div>" +
    '<section class="card"><div class="card-head"><h2>Release history</h2><p>A release moves a service to a newer version. ' +
    "A rollback moves it back to an older one, usually because the new one broke something.</p></div>" +
    '<div class="card-body" style="padding:0;margin-top:14px">' + body + "</div></section>";
}

/* ===================== Automation ===================== */
function viewAutomation() {
  const mode = robotMode();
  const modes = [
    { id: "off", title: "Off", text: "The robot is switched off. It does not watch or decide anything." },
    { id: "dry-run", title: "Dry-run", text: "The robot decides what it would do and writes it down, but never touches the system. A human does the fix." },
    { id: "enforce", title: "Enforce", text: "The robot carries out approved fixes by itself, then checks that customers are OK again." },
  ];
  const modeHtml = '<div class="modes">' + modes.map(function (m) {
    return '<div class="mode' + (mode === m.id ? " on" : "") + '"><h4>' + esc(m.title) + (mode === m.id ? '<span class="pill t-brand plain">Current</span>' : "") +
      "</h4><p>" + esc(m.text) + "</p></div>";
  }).join("") + "</div>" + (mode ? "" : '<p class="small faint" style="margin:10px 0 0">The current mode appears after the robot logs its first decision.</p>');

  const rules = [
    "Only two actions exist: roll back a release, or restart a service.",
    "Only the user and payment services may be touched. The order service and its database never are.",
    "Acts only when the diagnosis has high confidence.",
    "Hands over to a human if more than one service is failing.",
    "Tries the gentlest fix first: rollback, then restart.",
    "At most 2 attempts per incident, then a human takes over.",
    "Waits 60 seconds between attempts to see if the last one worked.",
    "At most 3 automatic actions per service per hour.",
  ];
  const rulesHtml = '<ul class="rules">' + rules.map(function (r) { return "<li>" + icon("shield") + "<span>" + esc(r) + "</span></li>"; }).join("") + "</ul>";

  const list = S.audit.slice().sort(byTimeDesc("logged_at"));
  let body;
  if (!list.length) {
    body = emptyState("robot", "No decisions yet", "When an incident opens, the robot's decision and its reason will show up here.");
  } else {
    body = '<div class="table-wrap"><table><thead><tr><th>When</th><th>Decision</th><th>What and why</th><th>Result</th><th>Incident</th></tr></thead><tbody>' +
      list.map(function (a) {
        const o = outcomeInfo(a.outcome);
        return '<tr><td class="nowrap">' + when(a.logged_at) + '<div class="sub">' + esc(a.mode) + " mode</div></td><td>" + decisionPill(a.decision) +
          "</td><td><div><b>" + esc(decisionText(a)) + '</b></div><div class="sub">' + esc(a.reason) + '</div></td><td><span class="pill t-' + o.k + '">' +
          esc(o.label) + "</span>" + (o.text && o.k !== "idle" ? '<div class="sub">' + esc(o.text) + "</div>" : "") + '</td><td class="nowrap">' +
          (a.incident_id ? '<a href="#/incidents/' + esc(a.incident_id) + '">#' + esc(String(a.incident_id).slice(0, 8)) + "</a>" : "—") + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }

  return card("Robot mode", "How much the robot is allowed to do. Set with CONTROLLER_MODE in compose.yaml.", modeHtml) +
    card("Safety rules", "The robot must pass every one of these before it may act (from controller/policy.py)", rulesHtml) +
    '<section class="card"><div class="card-head"><h2>Decision log</h2><p>Every decision the robot made, with its reason. A new row appears only when a decision changes.</p></div>' +
    '<div class="card-body" style="padding:0;margin-top:14px">' + body + "</div></section>";
}

/* ===================== Guide ===================== */
function viewGuide() {
  const loop = [
    ["Detect", "Every few seconds the detector checks what share of orders are failing. Past a threshold, it opens an incident."],
    ["Investigate", "It gathers evidence (which service is down, slow or erroring, and what was recently released) and scores each possible cause."],
    ["Decide", "The robot checks the safety rules and picks the gentlest fix allowed, or hands over to a human."],
    ["Act", "In enforce mode it rolls back the bad release or restarts the service. In dry-run it only writes down what it would do."],
    ["Verify", "It keeps measuring. When orders succeed again, the incident is closed and the team is notified."],
  ];
  const gloss = [
    ["Incident", "A problem RootLens has noticed, such as orders failing. It stays open until things are healthy again."],
    ["Release / deployment", "Putting a new version of a service live, for example payment service v1 → v2."],
    ["Rollback", "Going back to the previous version because the new one broke something."],
    ["Root cause", "The real reason for the problem, as opposed to its symptoms."],
    ["Confidence", "How sure RootLens is about its diagnosis: high, medium or low."],
    ["Response time (p95)", "95% of orders finish faster than this number. It shows how slow things are for almost everyone."],
    ["Failure ratio", "The share of orders that failed in the last minute."],
    ["Dry-run", "A safe practice mode: the robot decides but does not act."],
  ];
  const host = location.hostname || "localhost";
  return card("In one sentence",
    "",
    "<p style=\"margin:0;font-size:15px\">RootLens watches a small online shop. When customer orders start failing, it works out <b>which part broke and why</b>, " +
    "and can <b>fix it safely by itself</b>, for example by undoing a bad release. Every step is recorded so people can check what happened.</p>") +
    card("The five steps", "What happens every time something breaks",
      '<div class="loop">' + loop.map(function (l, i) {
        return '<div class="l"><b>Step ' + (i + 1) + "</b><h4>" + esc(l[0]) + "</h4><p>" + esc(l[1]) + "</p></div>";
      }).join("") + "</div>") +
    '<div class="grid-2">' +
      card("The parts of the system", "", '<dl class="gloss">' +
        "<dt>Order, user and payment services</dt><dd>The small online shop being protected. Built with FastAPI.</dd>" +
        "<dt>Prometheus</dt><dd>Collects numbers (requests, errors, response times) from every service.</dd>" +
        "<dt>Detector</dt><dd>Reads those numbers and opens or closes incidents.</dd>" +
        "<dt>Investigator</dt><dd>Ranks the possible causes and links problems to recent releases.</dd>" +
        "<dt>Controller (robot)</dt><dd>Applies the safety rules, takes action and sends Telegram alerts.</dd>" +
        "<dt>This dashboard</dt><dd>Shows all of it in one place.</dd></dl>") +
      card("Words used here", "", '<dl class="gloss">' + gloss.map(function (g) { return "<dt>" + esc(g[0]) + "</dt><dd>" + esc(g[1]) + "</dd>"; }).join("") + "</dl>") +
    "</div>" +
    card("Try it yourself", "Break the shop on purpose and watch RootLens react (in PowerShell, in the project folder)",
      '<ol class="demo">' +
      "<li>Send steady test orders: <code>python traffic.py</code></li>" +
      "<li>Release a buggy version: <code>.\\deploy.ps1 -Service payment-service -Version v2</code></li>" +
      '<li>Watch the <a href="#/overview">Overview</a>: failures rise, an incident opens, and the steps fill in.</li>' +
      '<li>Open the incident to see the diagnosis and the release it blames.</li>' +
      "<li>Fix it: <code>.\\deploy.ps1 -Service payment-service -Version v1</code> (or let the robot do it in enforce mode).</li>" +
      "</ol>" +
      '<p class="small muted" style="margin:12px 0 0">Raw data: <a target="_blank" rel="noopener" href="http://' + esc(host) + ':3000">Grafana</a> · ' +
      '<a target="_blank" rel="noopener" href="http://' + esc(host) + ':9090">Prometheus</a> · <a href="/incidents" target="_blank">/incidents</a> · ' +
      '<a href="/deployments" target="_blank">/deployments</a> · <a href="/audit" target="_blank">/audit</a></p>');
}

/* ===================== routing & rendering ===================== */
function route() {
  const raw = (location.hash || "#/overview").slice(2);
  const parts = raw.split("?");
  const segs = parts[0].split("/");
  return { page: segs[0] || "overview", id: segs[1] || null, query: parts[1] || "" };
}

function renderNav(r) {
  const open = activeIncidents().length;
  document.getElementById("nav").innerHTML =
    '<div class="nav-label">Monitor</div>' +
    PAGES.map(function (p, i) {
      return (i === 3 ? '<div class="nav-label">Control</div>' : i === 4 ? '<div class="nav-label">Learn</div>' : "") +
        '<a href="#/' + p.id + '" class="' + (r.page === p.id ? "active" : "") + '">' + icon(p.icon) + "<span>" + esc(p.label) + "</span>" +
        (p.id === "incidents" && open ? '<span class="count">' + open + "</span>" : "") + "</a>";
    }).join("");
  const mode = robotMode();
  const sm = document.getElementById("side-mode");
  sm.textContent = mode || "unknown";
}

function render() {
  const r = route();
  const page = PAGES.find(function (p) { return p.id === r.page; }) || PAGES[0];
  renderNav(r);
  document.getElementById("page-title").textContent = r.page === "incidents" && r.id ? "Incident details" : page.title;
  document.getElementById("page-sub").textContent = r.page === "incidents" && r.id ? "Everything RootLens knows about this problem" : page.sub;
  document.title = page.title + " · RootLens";

  let html;
  if (r.page === "incidents" && r.id) html = viewIncident(r.id);
  else if (r.page === "incidents") html = viewIncidents(r.query || "all");
  else if (r.page === "deployments") html = viewDeployments();
  else if (r.page === "automation") html = viewAutomation();
  else if (r.page === "guide") html = viewGuide();
  else html = viewOverview();

  if (S.error) html = '<div class="error-box">Cannot reach the RootLens API (' + esc(S.error) + "). Showing the last data received.</div>" + html;
  document.getElementById("view").innerHTML = html;
  updateLive();
}

function updateLive() {
  const dot = document.getElementById("live-dot");
  const txt = document.getElementById("live-text");
  if (S.error) { dot.className = "dot err"; txt.textContent = "Connection problem"; return; }
  if (!S.updated) { dot.className = "dot"; txt.textContent = "Connecting…"; return; }
  dot.className = "dot";
  const s = Math.round((Date.now() - S.updated) / 1000);
  txt.textContent = "Live · updated " + (s < 2 ? "just now" : s + "s ago");
}

/* ===================== data ===================== */
async function getJSON(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(url + " returned " + response.status);
  return response.json();
}

async function loadDiagnoses() {
  const r = route();
  const wanted = new Set(activeIncidents().map(function (i) { return i.incident_id; }));
  if (r.page === "incidents" && r.id) wanted.add(r.id);
  // Resolved incidents only need to be investigated once.
  S.incidents.slice().sort(byTimeDesc("detected_at")).slice(0, 15).forEach(function (i) {
    if (isLive(i) && !S.diag[i.incident_id]) wanted.add(i.incident_id);
  });
  await Promise.all(Array.from(wanted).map(async function (id) {
    try {
      const d = await getJSON("/investigate/" + encodeURIComponent(id));
      if (!d.error) S.diag[id] = d;
    } catch (e) { /* keep the previous diagnosis */ }
  }));
}

let busy = false;
async function refresh() {
  if (busy) return;
  busy = true;
  try {
    const results = await Promise.all([
      getJSON("/api/overview"), getJSON("/incidents"), getJSON("/deployments"), getJSON("/audit?limit=200"),
    ]);
    S.overview = results[0];
    S.incidents = results[1].incidents || [];
    S.deployments = results[2].deployments || [];
    S.audit = results[3].entries || [];
    await loadDiagnoses();
    S.updated = Date.now();
    S.error = null;
    S.loaded = true;
  } catch (e) {
    S.error = e.message || String(e);
  } finally {
    busy = false;
  }
  render();
}

/* ===================== events ===================== */
document.addEventListener("toggle", function (e) {
  const key = e.target && e.target.getAttribute && e.target.getAttribute("data-key");
  if (!key) return;
  if (e.target.open) openDetails.add(key); else openDetails.delete(key);
}, true);

document.addEventListener("click", function (e) {
  const row = e.target.closest && e.target.closest("tr[data-href]");
  if (row && !e.target.closest("a")) location.hash = row.getAttribute("data-href");
});

window.addEventListener("hashchange", function () {
  window.scrollTo(0, 0);
  render();
  const r = route();
  if (r.page === "incidents" && r.id && !S.diag[r.id]) refresh();
});

(function setupTheme() {
  let saved = null;
  try { saved = localStorage.getItem("rootlens-theme"); } catch (e) { /* storage blocked */ }
  const dark = saved ? saved === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  document.getElementById("theme-btn").addEventListener("click", function () {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("rootlens-theme", next); } catch (e) { /* storage blocked */ }
    render();
  });
})();

(function setupLinks() {
  const host = location.hostname || "localhost";
  document.getElementById("link-grafana").href = "http://" + host + ":3000";
  document.getElementById("link-prom").href = "http://" + host + ":9090";
})();

render();
refresh();
setInterval(refresh, 5000);
setInterval(updateLive, 1000);
</script>
</body>
</html>
"""