import asyncio
import os
import time

import httpx

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")

FAILURE_RATIO_QUERY = (
    'sum(rate(rootlens_order_requests_total{endpoint="/orders",status=~"5.."}[1m]))'
    ' / '
    'sum(rate(rootlens_order_requests_total{endpoint="/orders"}[1m]))'
)

P95_QUERY = (
    'histogram_quantile(0.95, sum by (le) '
    '(rate(rootlens_order_request_duration_seconds_bucket'
    '{endpoint="/orders"}[1m])))'
)

RATE_QUERY = 'sum(rate(rootlens_order_requests_total{endpoint="/orders"}[1m]))'

DEPENDENCIES = {
    "user-service": {
        "metric": "rootlens_user_requests_total",
        "endpoint": "/users/{user_id}",
    },
    "payment-service": {
        "metric": "rootlens_payment_requests_total",
        "endpoint": "/pay/{order_id}",
    },
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
        'sum(rate(rootlens_order_dependency_failures_total'
        + '{dependency="' + name + '",kind="timeout"}[1m]))'
    )


async def build_overview(user_url, payment_url):
    """Everything the dashboard needs about live system health."""
    urls = {"user-service": user_url, "payment-service": payment_url}

    async with httpx.AsyncClient(timeout=3.0) as client:
        tasks = {
            "failure_ratio": _instant(client, FAILURE_RATIO_QUERY),
            "p95": _instant(client, P95_QUERY),
            "rate": _instant(client, RATE_QUERY),
            "history_ratio": _range(client, FAILURE_RATIO_QUERY),
            "history_rate": _range(client, RATE_QUERY),
            "order:up": _instant(client, 'up{job="order-service"}'),
        }
        for name, info in DEPENDENCIES.items():
            tasks[name + ":up"] = _instant(client, 'up{job="' + name + '"}')
            tasks[name + ":err"] = _instant(client, _own_error_query(info))
            tasks[name + ":timeout"] = _instant(client, _timeout_query(name))
            tasks[name + ":version"] = _version(client, urls[name])

        keys = list(tasks.keys())
        values = await asyncio.gather(*tasks.values())
        data = dict(zip(keys, values))

    services = {"order-service": {"up": data["order:up"], "version": None}}
    for name in DEPENDENCIES:
        services[name] = {
            "up": data[name + ":up"],
            "version": data[name + ":version"],
            "error_ratio": data[name + ":err"],
            "timeout_rate": data[name + ":timeout"],
        }

    return {
        "generated_at": time.time(),
        "failure_ratio": data["failure_ratio"],
        "p95_latency_seconds": data["p95"],
        "request_rate": data["rate"],
        "services": services,
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
<link rel="icon" href="data:,">
<style>
:root {
  color-scheme: dark;
  --bg: #0f1318; --panel: #161b22; --panel2: #1a2029;
  --line: #252c36; --line2: #2f3744;
  --text: #d6dce4; --muted: #8b95a5; --dim: #5e6877;
  --green: #6dbb98; --red: #d8746c; --amber: #d2a45e; --blue: #7ba3d9; --violet: #a091d4;
  --green-bg: rgba(109,187,152,.12); --red-bg: rgba(216,116,108,.12);
  --amber-bg: rgba(210,164,94,.12); --blue-bg: rgba(123,163,217,.12);
  --grey-bg: rgba(139,149,165,.12);
}
* { box-sizing: border-box; }
html { background: var(--bg); }
body {
  margin: 0; color: var(--text); min-height: 100vh;
  font-family: Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  font-size: 14px; line-height: 1.5;
  background: radial-gradient(1100px 480px at 75% -10%, rgba(123,163,217,.07), transparent 60%), var(--bg);
}
.wrap { max-width: 1180px; margin: 0 auto; padding: 24px 18px 48px; }
h1, h2, h4 { margin: 0; }
.muted { color: var(--muted); }

/* header */
.top { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-bottom: 20px; }
.brand { display: flex; align-items: center; gap: 12px; }
.logo { width: 38px; height: 38px; border-radius: 11px; display: grid; place-items: center;
  background: linear-gradient(135deg, #34506f, #56799f); box-shadow: inset 0 0 0 1px rgba(255,255,255,.07); }
.brand h1 { font-size: 18px; font-weight: 650; letter-spacing: .2px; }
.brand p { margin: 0; color: var(--muted); font-size: 12px; }
.right { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.live { color: var(--muted); font-size: 12px; display: flex; align-items: center; gap: 7px; }
.pill { display: inline-flex; align-items: center; gap: 8px; padding: 5px 13px; border-radius: 999px;
  font-size: 12.5px; font-weight: 600; border: 1px solid transparent; }
.pill.green { color: var(--green); background: var(--green-bg); border-color: rgba(109,187,152,.25); }
.pill.red { color: var(--red); background: var(--red-bg); border-color: rgba(216,116,108,.3); }
.pill.amber { color: var(--amber); background: var(--amber-bg); border-color: rgba(210,164,94,.28); }
.pill.grey { color: var(--muted); background: var(--grey-bg); border-color: rgba(139,149,165,.2); }
.dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex: none; }
.dot.pulse { animation: pulse 1.9s ease-out infinite; }
@keyframes pulse {
  0% { box-shadow: 0 0 0 0 color-mix(in srgb, currentColor 55%, transparent); }
  70% { box-shadow: 0 0 0 7px transparent; }
  100% { box-shadow: 0 0 0 0 transparent; }
}

/* cards */
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; }
.pad { padding: 16px 18px; }
.card-h { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.card-h h2 { font-size: 14px; font-weight: 650; }
.hint { color: var(--dim); font-size: 12px; }

/* banner */
.alert { display: flex; gap: 14px; align-items: flex-start; margin-bottom: 18px; padding: 16px 18px; border-radius: 14px;
  border: 1px solid rgba(216,116,108,.32); background: linear-gradient(180deg, rgba(216,116,108,.10), rgba(216,116,108,.04)); }
.alert .a-icon { color: var(--red); margin-top: 5px; }
.alert .a-body { flex: 1; min-width: 0; }
.alert .a-title { font-weight: 650; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.alert .a-sub { color: var(--muted); font-size: 12.5px; margin-top: 2px; }
.btn { border: 1px solid var(--line2); background: var(--panel2); color: var(--text); border-radius: 9px;
  padding: 6px 12px; font-size: 12.5px; cursor: pointer; font-family: inherit; }
.btn:hover { border-color: #3d4756; background: #202733; }

/* kpis */
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 14px; }
.kpi { padding: 15px 17px; position: relative; overflow: hidden; }
.kpi-label { color: var(--muted); font-size: 11.5px; text-transform: uppercase; letter-spacing: .06em; }
.kpi-val { font-size: 30px; font-weight: 650; margin-top: 4px; letter-spacing: -.5px; }
.kpi-val.green { color: var(--green); } .kpi-val.red { color: var(--red); }
.kpi-val.amber { color: var(--amber); } .kpi-val.blue { color: var(--blue); } .kpi-val.grey { color: var(--muted); }
.kpi-sub { color: var(--dim); font-size: 12px; }
.kpi-spark { position: absolute; right: 14px; top: 16px; width: 96px; height: 34px; opacity: .9; }
.spark { width: 100%; height: 100%; }

/* layout */
.grid2 { display: grid; grid-template-columns: 5fr 7fr; gap: 14px; margin-bottom: 14px; }
.grid3 { display: grid; grid-template-columns: 1fr 340px; gap: 14px; }

/* map */
.map { width: 100%; height: auto; display: block; }
.edge { fill: none; stroke: var(--line2); stroke-width: 1.7; }
.edge.red { stroke: var(--red); stroke-dasharray: 6 5; animation: flow 1s linear infinite; }
.edge.amber { stroke: var(--amber); stroke-dasharray: 6 5; animation: flow 1.4s linear infinite; }
@keyframes flow { to { stroke-dashoffset: -11; } }
.node rect { fill: var(--panel2); stroke: var(--line2); stroke-width: 1.2; }
.node.green rect { stroke: rgba(109,187,152,.45); }
.node.amber rect { stroke: rgba(210,164,94,.55); }
.node.red rect { stroke: rgba(216,116,108,.6); }
.node.culprit rect { stroke: var(--red); stroke-width: 2; filter: drop-shadow(0 0 9px rgba(216,116,108,.4)); }
.node .ndot { fill: var(--muted); }
.node.green .ndot { fill: var(--green); } .node.amber .ndot { fill: var(--amber); } .node.red .ndot { fill: var(--red); }
.nname { fill: var(--text); font-size: 13px; font-weight: 600; }
.nsub { fill: var(--muted); font-size: 11.5px; }
.legend { display: flex; gap: 16px; flex-wrap: wrap; color: var(--dim); font-size: 12px; margin-top: 8px; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px; vertical-align: -1px; }

/* chart */
.chartwrap { position: relative; }
.chart { width: 100%; height: auto; display: block; }
.chart .grid { stroke: #222a34; stroke-width: 1; }
.chart .axis { fill: var(--dim); font-size: 11px; }
.chart .line { fill: none; stroke: var(--blue); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.chart .area { fill: rgba(123,163,217,.12); stroke: none; }
.chart .band { fill: rgba(216,116,108,.12); }
.chart .thr { stroke: var(--amber); stroke-width: 1; stroke-dasharray: 5 5; opacity: .75; }
.chart .thr-t { fill: var(--amber); font-size: 10.5px; opacity: .85; }
.chart .dep { stroke: var(--violet); stroke-width: 1.2; stroke-dasharray: 3 4; opacity: .9; }
.chart .dep-t { fill: var(--violet); font-size: 10.5px; }
.cross { position: absolute; top: 0; bottom: 0; width: 1px; background: rgba(255,255,255,.22); display: none; pointer-events: none; }
.tip { position: absolute; display: none; pointer-events: none; background: #0c1015; border: 1px solid var(--line2);
  padding: 4px 9px; border-radius: 8px; font-size: 12px; white-space: nowrap; }

/* incidents */
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip-btn { border: 1px solid var(--line); background: transparent; color: var(--muted); border-radius: 999px;
  padding: 3px 11px; font-size: 12px; cursor: pointer; font-family: inherit; }
.chip-btn:hover { border-color: var(--line2); color: var(--text); }
.chip-btn.on { background: var(--blue-bg); border-color: rgba(123,163,217,.35); color: var(--blue); }
.inc { border: 1px solid var(--line); border-radius: 12px; background: var(--panel2); margin-bottom: 10px; overflow: hidden; transition: border-color .2s; }
.inc:hover { border-color: var(--line2); }
.inc.active { border-color: rgba(216,116,108,.35); }
.inc-row { display: flex; align-items: center; gap: 12px; padding: 12px 14px; cursor: pointer; }
.inc-main { flex: 1; min-width: 0; }
.inc-title { font-weight: 600; font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta { color: var(--muted); font-size: 12px; }
.badge { font-size: 11.5px; font-weight: 650; padding: 2px 10px; border-radius: 999px; white-space: nowrap; }
.badge.red { background: var(--red-bg); color: var(--red); }
.badge.green { background: var(--green-bg); color: var(--green); }
.badge.grey { background: var(--grey-bg); color: var(--muted); }
.cause-chip { font-size: 11.5px; color: var(--muted); background: rgba(255,255,255,.04); border: 1px solid var(--line);
  border-radius: 8px; padding: 2px 8px; max-width: 210px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chev { color: var(--dim); font-size: 11px; }
.detail { border-top: 1px solid var(--line); padding: 16px 16px 18px; display: grid; gap: 20px; }
.detail h4 { font-size: 11.5px; text-transform: uppercase; letter-spacing: .07em; color: var(--muted); margin-bottom: 10px; }
.diag-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.diag-cause { font-size: 16px; font-weight: 650; }
.conf { font-size: 11.5px; font-weight: 650; padding: 2px 10px; border-radius: 999px; }
.conf.high { background: var(--green-bg); color: var(--green); }
.conf.medium { background: var(--amber-bg); color: var(--amber); }
.conf.low, .conf.limited { background: var(--red-bg); color: var(--red); }
.callout { border-radius: 10px; padding: 9px 12px; margin-top: 10px; font-size: 13px; border: 1px solid transparent; }
.callout.blue { background: var(--blue-bg); border-color: rgba(123,163,217,.22); color: #c4d5ec; }
.callout.amber { background: var(--amber-bg); border-color: rgba(210,164,94,.25); color: #e3c996; }
.callout.green { background: var(--green-bg); border-color: rgba(109,187,152,.25); color: #b7dccb; }
.rc { margin-top: 14px; }
.rc-head { display: flex; justify-content: space-between; font-weight: 600; font-size: 13px; }
.bar { height: 6px; border-radius: 99px; background: #232b36; overflow: hidden; margin: 6px 0 4px; }
.bar > i { display: block; height: 100%; border-radius: 99px; background: var(--dim); }
.bar > i.top { background: var(--blue); }
.rc ul { margin: 4px 0 0; padding: 0; list-style: none; font-size: 12.5px; color: var(--muted); }
.rc li { padding-left: 18px; position: relative; }
.rc li.yes::before { content: "\2713"; position: absolute; left: 0; color: var(--green); }
.rc li.no::before { content: "\2717"; position: absolute; left: 0; color: var(--red); }
.tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.tbl th { text-align: left; font-weight: 600; color: var(--dim); font-size: 11px; text-transform: uppercase; letter-spacing: .05em; padding: 4px 8px 6px 0; }
.tbl td { padding: 7px 8px 7px 0; border-top: 1px solid var(--line); }
.tbl td.k { color: var(--muted); }
.ok { color: var(--green); } .bad { color: var(--red); }
.empty { text-align: center; color: var(--muted); padding: 34px 10px; font-size: 13px; }

/* robot decisions */
.aud { border-top: 1px solid var(--line); padding: 10px 0; }
.aud:first-child { border-top: none; }
.aud-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 3px; }
.aud-act { font-weight: 600; font-size: 13px; }
.dbadge { font-size: 11px; font-weight: 650; padding: 1px 9px; border-radius: 999px; }
.dbadge.act { background: var(--blue-bg); color: var(--blue); }
.dbadge.wait { background: var(--amber-bg); color: var(--amber); }
.dbadge.escalate { background: var(--red-bg); color: var(--red); }
.mode { font-size: 11px; color: var(--muted); border: 1px solid var(--line2); border-radius: 6px; padding: 0 6px; }
.linkbtn { background: none; border: none; color: var(--blue); cursor: pointer; font: inherit; font-size: 12px; padding: 0; }
.linkbtn:hover { text-decoration: underline; }
.robot-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 28px; }

/* deployments */
.running { display: grid; gap: 8px; margin-bottom: 16px; }
.run-item { display: flex; justify-content: space-between; align-items: center; background: var(--panel2);
  border: 1px solid var(--line); border-radius: 10px; padding: 8px 12px; font-size: 13px; }
.chip { font-size: 12px; font-weight: 600; padding: 1px 9px; border-radius: 8px; background: var(--blue-bg); color: var(--blue); }
.sub-h { font-size: 11.5px; text-transform: uppercase; letter-spacing: .07em; color: var(--muted); margin: 4px 0 10px; }
.dep-item { display: flex; gap: 12px; padding: 10px 0; border-top: 1px solid var(--line); }
.dep-item:first-child { border-top: none; }
.dep-dot { width: 9px; height: 9px; border-radius: 50%; margin-top: 6px; flex: none; }
.dep-dot.release { background: var(--blue); } .dep-dot.rollback { background: var(--amber); }
.dep-title { font-weight: 600; font-size: 13px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.tag { font-size: 11px; padding: 0 7px; border-radius: 6px; font-weight: 600; }
.tag.release { background: var(--blue-bg); color: var(--blue); } .tag.rollback { background: var(--amber-bg); color: var(--amber); }

.foot { text-align: center; color: var(--dim); font-size: 12px; margin-top: 26px; }

@media (max-width: 980px) {
  .kpis { grid-template-columns: repeat(2, 1fr); }
  .grid2, .grid3 { grid-template-columns: 1fr; }
  .robot-grid { grid-template-columns: 1fr; }
}
@media (max-width: 520px) { .kpis { grid-template-columns: 1fr; } }
@media (prefers-reduced-motion: reduce) { .dot.pulse, .edge.red, .edge.amber { animation: none; } }
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div class="brand">
      <div class="logo">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#dbe7f5" stroke-width="2" stroke-linecap="round">
          <circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>
        </svg>
      </div>
      <div><h1>RootLens</h1><p>Incident detection, diagnosis and recovery</p></div>
    </div>
    <div class="right"><div id="status"></div><div class="live" id="live"></div></div>
  </header>

  <div id="banner"></div>
  <section class="kpis" id="kpis"></section>

  <section class="grid2">
    <div class="card pad">
      <div class="card-h"><h2>Service map</h2><span class="hint">Order Service calls both dependencies</span></div>
      <div id="map"></div>
    </div>
    <div class="card pad">
      <div class="card-h"><h2>Order failure ratio</h2><span class="hint">Last 15 minutes, hover for values</span></div>
      <div id="chart"></div>
    </div>
  </section>

  <section class="grid3">
    <div class="card pad">
      <div class="card-h"><h2>Incidents</h2><div class="chips" id="chips"></div></div>
      <div id="incidents"></div>
    </div>
    <div class="card pad">
      <div class="card-h"><h2>Deployments</h2><span class="hint">Release history</span></div>
      <div id="running"></div>
      <div class="sub-h">Recent changes</div>
      <div id="deployments"></div>
    </div>
  </section>

  <section class="card pad" style="margin-top:14px">
    <div class="card-h"><h2>Robot decisions</h2><span class="hint">What the controller decided and why</span></div>
    <div id="robot"></div>
  </section>

  <div class="foot">RootLens &middot; refreshes every 5 seconds</div>
</div>

<script>
var state = { overview: null, incidents: [], deployments: [], audit: [], diag: {}, openId: null, filter: "all", updated: 0, error: false };
var chartState = null;
var COLORS = { green: "var(--green)", red: "var(--red)", amber: "var(--amber)", blue: "var(--blue)", grey: "var(--muted)" };

/* ---------- helpers ---------- */
function $(id) { return document.getElementById(id); }
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}
function setHtml(id, html) {
  var el = $(id);
  if (el.__html !== html) { el.innerHTML = html; el.__html = html; }
}
function getJson(url) {
  return fetch(url, { cache: "no-store" }).then(function (r) {
    if (!r.ok) throw new Error(url + " " + r.status);
    return r.json();
  });
}
function isActive(i) { return i.status === "open" && !!i.fingerprint; }
function isLegacy(i) { return i.status === "open" && !i.fingerprint; }
function cap(s) { s = String(s || ""); return s.charAt(0).toUpperCase() + s.slice(1); }
function pad2(n) { return n < 10 ? "0" + n : "" + n; }
function hhmm(ms) { var d = new Date(ms); return pad2(d.getHours()) + ":" + pad2(d.getMinutes()); }
function hhmmss(ms) { var d = new Date(ms); return hhmm(ms) + ":" + pad2(d.getSeconds()); }
function secs(n) { n = Math.round(n); if (n < 90) return n + "s"; return Math.floor(n / 60) + "m " + (n % 60) + "s"; }
function ago(iso) {
  var s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return s + "s ago";
  var m = Math.floor(s / 60);
  if (m < 60) return m + "m ago";
  var h = Math.floor(m / 60);
  if (h < 24) return h + "h ago";
  return Math.floor(h / 24) + "d ago";
}
function dur(a, b) {
  var s = Math.max(0, Math.round(((b ? new Date(b).getTime() : Date.now()) - new Date(a).getTime()) / 1000));
  if (s < 60) return s + "s";
  var m = Math.floor(s / 60);
  if (m < 60) return m + "m " + (s % 60) + "s";
  return Math.floor(m / 60) + "h " + (m % 60) + "m";
}
function fmtPct(v) {
  if (v == null) return "--";
  if (v === 0) return "0%";
  if (v < 0.1) return (v * 100).toFixed(1) + "%";
  return Math.round(v * 100) + "%";
}
function fmtLatency(v) {
  if (v == null) return "--";
  if (v < 1) return Math.round(v * 1000) + " ms";
  return v.toFixed(2) + " s";
}
function fmtRate(v) {
  if (v == null) return "--";
  return (v < 10 ? v.toFixed(1) : Math.round(v)) + "/s";
}
function fmtNum(v) { return v == null ? "--" : String(Math.round(v * 1000) / 1000); }
function getEvidence(inc) {
  if (inc.evidence) return inc.evidence;
  if (inc.evidence_json) { try { return JSON.parse(inc.evidence_json); } catch (e) { return null; } }
  return null;
}
function triggerText(t) {
  var when = secs(t.seconds_before_detection) + " before detection (" + t.strength + " correlation)";
  if (t.kind === "rollback") {
    return t.service + " was rolled back to " + t.version + " (from " + (t.previous_version || "?") + ") " + when;
  }
  return t.service + " " + t.version + " was deployed " + when;
}
function actionText(e) {
  if (e.decision === "wait") return "Waiting before the next step";
  if (e.decision !== "act") return "Escalated to a human";
  var dry = e.mode === "dry-run";
  var verbs = {
    rollback_deployment: dry ? "Would roll back" : "Rolled back",
    restart_service: dry ? "Would restart" : "Restarted"
  };
  var verb = verbs[e.action] || (dry ? "Would run " + e.action + " on" : "Ran " + e.action + " on");
  return verb + " " + (e.service || "") + (e.target_version ? " to " + e.target_version : "");
}

/* ---------- small graphics ---------- */
function spark(points, color, minMax) {
  var vals = points.filter(function (p) { return p[1] != null; });
  if (vals.length < 2) return "";
  var W = 120, H = 34;
  var t0 = points[0][0], t1 = points[points.length - 1][0];
  var max = Math.max(minMax, Math.max.apply(null, vals.map(function (p) { return p[1]; })), 0.0001);
  var d = "", pen = false;
  points.forEach(function (p) {
    if (p[1] == null) { pen = false; return; }
    var x = ((p[0] - t0) / (t1 - t0)) * W;
    var y = H - 3 - (p[1] / max) * (H - 6);
    d += (pen ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1) + " ";
    pen = true;
  });
  return '<svg class="spark" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none">' +
    '<path d="' + d + '" fill="none" style="stroke:' + color + '" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/></svg>';
}

/* ---------- health logic ---------- */
function svcHealth(name) {
  var ov = state.overview;
  var grey = { cls: "grey", label: "no data" };
  if (!ov || !ov.services || !ov.services[name]) return grey;
  var s = ov.services[name];
  if (name === "order-service") {
    var r = ov.failure_ratio;
    if (r == null) return { cls: "grey", label: "no traffic" };
    if (r >= 0.3) return { cls: "red", label: "failing " + fmtPct(r) };
    if (r >= 0.1) return { cls: "amber", label: "degraded " + fmtPct(r) };
    return { cls: "green", label: "healthy" };
  }
  if (s.up === 0) return { cls: "red", label: "down" };
  if (s.up == null) return grey;
  if ((s.timeout_rate || 0) > 0.05) return { cls: "red", label: "timing out" };
  if ((s.error_ratio || 0) >= 0.2) return { cls: "red", label: "errors " + fmtPct(s.error_ratio) };
  if ((s.error_ratio || 0) >= 0.05) return { cls: "amber", label: "errors " + fmtPct(s.error_ratio) };
  return { cls: "green", label: "healthy" };
}
function edgeCls(h) { return h.cls === "red" ? "red" : (h.cls === "amber" ? "amber" : "ok"); }

/* ---------- sections ---------- */
function renderStatus() {
  var ov = state.overview;
  var active = state.incidents.filter(isActive);
  var cls, label, pulse = false;
  if (state.error && !state.updated) { cls = "grey"; label = "Cannot reach RootLens"; }
  else if (active.length) { cls = "red"; label = "Incident in progress"; pulse = true; }
  else if (ov && ov.failure_ratio != null && ov.failure_ratio >= 0.1) { cls = "amber"; label = "Degraded"; }
  else if (!ov) { cls = "grey"; label = "Connecting"; }
  else { cls = "green"; label = "All systems operational"; }
  setHtml("status", '<span class="pill ' + cls + '"><span class="dot' + (pulse ? " pulse" : "") + '"></span>' + label + "</span>");
}

function renderLive() {
  var el = $("live");
  var txt;
  if (state.error) txt = "Connection lost, retrying";
  else if (!state.updated) txt = "Loading";
  else txt = "Live &middot; updated " + Math.max(0, Math.round((Date.now() - state.updated) / 1000)) + "s ago";
  var color = state.error ? "var(--amber)" : "var(--green)";
  var html = '<span class="dot pulse" style="color:' + color + '"></span>' + txt;
  if (el.__html !== html) { el.innerHTML = html; el.__html = html; }
}

function renderBanner() {
  var active = state.incidents.filter(isActive)[0];
  if (!active) { setHtml("banner", ""); return; }
  var d = state.diag[active.incident_id];
  var body;
  if (d) {
    body = '<div class="a-title"><span>' + esc(d.likely_cause) + '</span><span class="conf ' + esc(d.confidence) + '">' +
      esc(d.confidence) + " confidence</span></div>";
    if (d.trigger) body += '<div class="a-sub">Likely trigger: ' + esc(triggerText(d.trigger)) + "</div>";
    if (d.recommended_next_step) body += '<div class="a-sub"><b>Next step:</b> ' + esc(d.recommended_next_step) + "</div>";
    var robot = state.audit.filter(function (e) { return e.incident_id === active.incident_id; })[0];
    if (robot) body += '<div class="a-sub"><b>Robot (' + esc(robot.mode) + '):</b> ' + esc(actionText(robot)) + "</div>";
  } else {
    body = '<div class="a-title">Analyzing the evidence...</div>';
  }
  setHtml("banner",
    '<div class="alert"><span class="a-icon"><span class="dot pulse" style="color:var(--red)"></span></span>' +
    '<div class="a-body"><div class="a-sub" style="margin:0 0 4px">Incident in progress &middot; ongoing for ' + esc(dur(active.detected_at)) + "</div>" + body + "</div>" +
    '<button class="btn" data-open="' + esc(active.incident_id) + '">View details</button></div>');
}

function kpi(label, value, cls, sub, sparkHtml) {
  return '<div class="card kpi"><div class="kpi-label">' + label + '</div><div class="kpi-val ' + cls + '">' + value +
    '</div><div class="kpi-sub">' + sub + '</div><div class="kpi-spark">' + (sparkHtml || "") + "</div></div>";
}

function renderKpis() {
  var ov = state.overview || {};
  var h = ov.history || {};
  var fr = ov.failure_ratio;
  var frc = fr == null ? "grey" : (fr >= 0.3 ? "red" : (fr >= 0.1 ? "amber" : "green"));
  var p = ov.p95_latency_seconds;
  var pc = p == null ? "grey" : (p >= 2.4 ? "red" : (p >= 1 ? "amber" : "green"));
  var active = state.incidents.filter(isActive).length;
  var resolved = state.incidents.filter(function (i) { return i.status === "resolved"; }).length;
  setHtml("kpis",
    kpi("Order failure ratio", fmtPct(fr), frc, "Incident opens above 30%", spark(h.failure_ratio || [], COLORS[frc], 0.4)) +
    kpi("Order latency (p95)", fmtLatency(p), pc, "Slowest 5% of orders", "") +
    kpi("Throughput", fmtRate(ov.request_rate), "blue", "Orders handled per second", spark(h.request_rate || [], COLORS.blue, 1)) +
    kpi("Active incidents", String(active), active ? "red" : "green", resolved + " resolved so far", ""));
}

function nodeSvg(x, y, name, sub, h, culprit) {
  var w = 184, hh = 58;
  return '<g class="node ' + h.cls + (culprit ? " culprit" : "") + '" transform="translate(' + (x - w / 2) + "," + (y - hh / 2) + ')">' +
    '<rect width="' + w + '" height="' + hh + '" rx="12"/><circle cx="18" cy="21" r="5" class="ndot"/>' +
    '<text x="32" y="25" class="nname">' + esc(name) + '</text><text x="18" y="44" class="nsub">' + esc(sub) + "</text></g>";
}

function renderMap() {
  var ov = state.overview;
  var act = state.incidents.filter(isActive)[0];
  var diag = act ? state.diag[act.incident_id] : null;
  var culprit = diag && diag.root_cause_service ? diag.root_cause_service : null;
  var ho = svcHealth("order-service"), hu = svcHealth("user-service"), hp = svcHealth("payment-service");
  function ver(n) {
    var s = ov && ov.services && ov.services[n];
    return s && s.version ? s.version + " \u00b7 " : "";
  }
  var s = '<svg viewBox="0 0 640 230" class="map" role="img" aria-label="Service dependency map">';
  s += '<path class="edge ' + edgeCls(hu) + '" d="M320 82 C320 120 140 112 140 156"/>';
  s += '<path class="edge ' + edgeCls(hp) + '" d="M320 82 C320 120 500 112 500 156"/>';
  s += nodeSvg(320, 52, "order-service", ver("order-service") + ho.label, ho, false);
  s += nodeSvg(140, 186, "user-service", ver("user-service") + hu.label, hu, culprit === "user-service");
  s += nodeSvg(500, 186, "payment-service", ver("payment-service") + hp.label, hp, culprit === "payment-service");
  s += "</svg>";
  s += '<div class="legend"><span><i style="background:var(--green)"></i>healthy</span><span><i style="background:var(--amber)"></i>degraded</span>' +
    '<span><i style="background:var(--red)"></i>failing</span><span>Dashed lines: calls that are failing</span></div>';
  setHtml("map", s);
}

function chartHtml() {
  var ov = state.overview;
  var pts = (ov && ov.history && ov.history.failure_ratio) || [];
  var valid = pts.filter(function (p) { return p[1] !== null; });
  if (pts.length < 2 || valid.length < 1) {
    chartState = null;
    return '<div class="empty">Waiting for traffic data. Start traffic.py to see live metrics here.</div>';
  }
  var W = 820, H = 250, L = 46, R = 14, T = 16, B = 28, iw = W - L - R, ih = H - T - B;
  var t0 = pts[0][0] * 1000, t1 = pts[pts.length - 1][0] * 1000;
  var maxV = Math.max.apply(null, valid.map(function (p) { return p[1]; }));
  var ymax = Math.min(1, Math.max(0.4, maxV * 1.2));
  function X(t) { return L + ((t - t0) / (t1 - t0)) * iw; }
  function Y(v) { return T + ih - (v / ymax) * ih; }
  chartState = { W: W, L: L, R: R, iw: iw, t0: t0, t1: t1, pts: pts };

  var s = '<div class="chartwrap" id="chartwrap"><svg viewBox="0 0 ' + W + " " + H + '" class="chart" role="img" aria-label="Order failure ratio over the last 15 minutes">';
  for (var i = 0; i <= 4; i++) {
    var v = ymax * i / 4, y = Y(v);
    s += '<line class="grid" x1="' + L + '" x2="' + (W - R) + '" y1="' + y + '" y2="' + y + '"/>' +
      '<text class="axis" x="' + (L - 8) + '" y="' + (y + 4) + '" text-anchor="end">' + Math.round(v * 100) + "%</text>";
  }
  var step = 180000, firstTick = Math.ceil(t0 / step) * step;
  for (var t = firstTick; t <= t1; t += step) {
    var x = X(t);
    s += '<line class="grid" x1="' + x + '" x2="' + x + '" y1="' + T + '" y2="' + (T + ih) + '"/>' +
      '<text class="axis" x="' + x + '" y="' + (H - 8) + '" text-anchor="middle">' + hhmm(t) + "</text>";
  }
  state.incidents.forEach(function (inc) {
    if (!inc.fingerprint) return;
    var a = new Date(inc.detected_at).getTime();
    var b = inc.resolved_at ? new Date(inc.resolved_at).getTime() : t1;
    if (b < t0 || a > t1) return;
    var xa = X(Math.max(a, t0)), xb = X(Math.min(b, t1));
    s += '<rect class="band" x="' + xa + '" y="' + T + '" width="' + Math.max(2, xb - xa) + '" height="' + ih + '"/>';
  });
  if (0.3 <= ymax) {
    var ty = Y(0.3);
    s += '<line class="thr" x1="' + L + '" x2="' + (W - R) + '" y1="' + ty + '" y2="' + ty + '"/>' +
      '<text class="thr-t" x="' + (W - R - 4) + '" y="' + (ty - 5) + '" text-anchor="end">detection threshold 30%</text>';
  }
  var seg = [], segs = [];
  pts.forEach(function (p) {
    if (p[1] === null) { if (seg.length) segs.push(seg); seg = []; } else { seg.push(p); }
  });
  if (seg.length) segs.push(seg);
  segs.forEach(function (sg) {
    var d = "";
    sg.forEach(function (p, idx) { d += (idx ? "L" : "M") + X(p[0] * 1000).toFixed(1) + " " + Y(p[1]).toFixed(1) + " "; });
    var lastP = sg[sg.length - 1], firstP = sg[0];
    var area = d + "L" + X(lastP[0] * 1000).toFixed(1) + " " + (T + ih) + " L" + X(firstP[0] * 1000).toFixed(1) + " " + (T + ih) + " Z";
    s += '<path class="area" d="' + area + '"/><path class="line" d="' + d + '"/>';
  });
  state.deployments.forEach(function (dp, idx) {
    var t = new Date(dp.deployed_at).getTime();
    if (t < t0 || t > t1) return;
    var x = X(t);
    var label = dp.service.replace("-service", "") + " " + dp.version;
    var endAnchor = x > W - 120;
    s += '<line class="dep" x1="' + x + '" x2="' + x + '" y1="' + T + '" y2="' + (T + ih) + '"/>' +
      '<text class="dep-t" x="' + (endAnchor ? x - 4 : x + 4) + '" y="' + (T + 11 + (idx % 2) * 12) + '" text-anchor="' + (endAnchor ? "end" : "start") + '">' + esc(label) + "</text>";
  });
  s += '</svg><div class="cross" id="cross"></div><div class="tip" id="tip"></div></div>';
  s += '<div class="legend"><span><i style="background:var(--blue)"></i>failure ratio</span>' +
    '<span><i style="background:rgba(216,116,108,.5)"></i>incident open</span>' +
    '<span><i style="background:var(--violet)"></i>deployment</span></div>';
  return s;
}
function renderChart() { setHtml("chart", chartHtml()); }

/* ---------- robot decisions ---------- */
function auditRow(e, withLink) {
  var link = withLink && e.incident_id
    ? '<button class="linkbtn" data-open="' + esc(e.incident_id) + '">incident ' + esc(String(e.incident_id).slice(0, 8)) + "</button>"
    : "";
  return '<div class="aud"><div class="aud-top"><span class="dbadge ' + esc(e.decision) + '">' + esc(cap(e.decision)) +
    '</span><span class="mode">' + esc(e.mode) + '</span><span class="meta">' + esc(ago(e.logged_at)) + "</span>" + link + "</div>" +
    '<div class="aud-act">' + esc(actionText(e)) + "</div>" +
    (e.reason ? '<div class="meta">' + esc(e.reason) + "</div>" : "") +
    (e.outcome ? '<div class="meta">Outcome: ' + esc(e.outcome) + "</div>" : "") + "</div>";
}

function renderRobot() {
  var list = state.audit.slice(0, 8);
  if (!list.length) {
    setHtml("robot", '<div class="empty">No decisions yet. The controller records one when an incident opens.</div>');
    return;
  }
  setHtml("robot", '<div class="robot-grid">' + list.map(function (e) { return auditRow(e, true); }).join("") + "</div>");
}

/* ---------- incidents ---------- */
function diagHtml(d) {
  if (d == null) return '<div class="muted">Analyzing the evidence...</div>';
  var conf = d.confidence || "low";
  var html = '<div class="diag-head"><div class="diag-cause">' + esc(d.likely_cause) + '</div><span class="conf ' + esc(conf) + '">' + esc(conf) + " confidence</span></div>";
  if (d.trigger) {
    html += '<div class="callout blue"><b>Likely trigger</b> &middot; ' + esc(triggerText(d.trigger)) + "</div>";
  }
  (d.notes || []).forEach(function (n) { html += '<div class="callout amber">' + esc(n) + "</div>"; });
  if (d.recommended_next_step) html += '<div class="callout green"><b>Next step</b> &middot; ' + esc(d.recommended_next_step) + "</div>";
  if (d.impacted_services && d.impacted_services.length) {
    html += '<div class="meta" style="margin-top:10px">Impacted services: ' + d.impacted_services.map(esc).join(", ") + "</div>";
  }
  var ranked = d.ranked_causes || [];
  if (ranked.length) {
    ranked.forEach(function (c, idx) {
      var pct = Math.round(c.score * 100);
      html += '<div class="rc"><div class="rc-head"><span>' + esc(c.cause) + "</span><span>" + pct + '%</span></div>' +
        '<div class="bar"><i class="' + (idx === 0 ? "top" : "") + '" style="width:' + pct + '%"></i></div>';
      var sup = c.supporting_evidence || [], con = c.contradicting_evidence || [];
      if (sup.length || con.length) {
        html += "<ul>";
        sup.forEach(function (t) { html += '<li class="yes">' + esc(t) + "</li>"; });
        con.forEach(function (t) { html += '<li class="no">' + esc(t) + "</li>"; });
        html += "</ul>";
      }
      html += "</div>";
    });
  } else if (d.observed_facts && d.observed_facts.length) {
    html += '<div class="rc"><ul>' + d.observed_facts.map(function (t) { return '<li class="yes">' + esc(t) + "</li>"; }).join("") + "</ul></div>";
  }
  return html;
}

function evidenceHtml(inc) {
  var ev = getEvidence(inc);
  if (!ev) return '<div class="muted">No evidence was saved for this incident.</div>';
  var deps = ev.dependencies;
  if (!deps && ev.user_service_up !== undefined) {
    var rates = ev.order_status_rates_per_second || {};
    deps = { "user-service": { up: ev.user_service_up, own_error_ratio: ev.user_service_error_ratio,
      failure_rates_per_second: { error: rates["503"] || 0, timeout: rates["504"] || 0 } } };
  }
  var html = "";
  var names = Object.keys(deps || {});
  if (names.length) {
    html += '<table class="tbl"><tr><th>Dependency</th><th>Running</th><th>Own error rate</th><th>Timeouts/s</th><th>Errors/s</th></tr>';
    names.forEach(function (n) {
      var d = deps[n] || {};
      var fr = d.failure_rates_per_second || {};
      var up = d.up === 1 ? '<span class="ok">yes</span>' : (d.up === 0 ? '<span class="bad">no</span>' : "--");
      html += "<tr><td>" + esc(n) + "</td><td>" + up + "</td><td>" + fmtPct(d.own_error_ratio) + "</td><td>" +
        fmtNum(fr.timeout || 0) + "</td><td>" + fmtNum((fr.error || 0) + (fr.unreachable || 0)) + "</td></tr>";
    });
    html += "</table>";
  }
  html += '<table class="tbl" style="margin-top:12px"><tr><th colspan="2">Order service at detection</th></tr>' +
    '<tr><td class="k">Failure ratio</td><td>' + fmtPct(ev.order_failure_ratio) + "</td></tr>" +
    '<tr><td class="k">p95 latency</td><td>' + fmtLatency(ev.order_p95_latency_seconds) + "</td></tr>" +
    '<tr><td class="k">Responses per second</td><td>' +
    (Object.keys(ev.order_status_rates_per_second || {}).map(function (k) { return esc(k) + ": " + esc(ev.order_status_rates_per_second[k]); }).join(" &middot; ") || "--") + "</td></tr></table>";
  var recent = ev.recent_deployments || [];
  if (recent.length) {
    html += '<table class="tbl" style="margin-top:12px"><tr><th colspan="2">Recent deployments</th></tr>';
    recent.forEach(function (d) {
      html += "<tr><td>" + esc(d.service) + " " + esc(d.previous_version || "?") + " &rarr; " + esc(d.version) + "</td><td>" +
        esc(secs(d.seconds_before_detection)) + " before detection</td></tr>";
    });
    html += "</table>";
  }
  return html;
}

function incCard(inc) {
  var id = inc.incident_id;
  var active = isActive(inc), resolved = inc.status === "resolved";
  var badge = active ? '<span class="badge red">Active</span>' :
    (resolved ? '<span class="badge green">Resolved</span>' :
    '<span class="badge grey" title="Created before incidents had fingerprints, so the detector cannot close it">Legacy</span>');
  var isOpen = state.openId === id;
  var meta = esc(inc.service) + (inc.endpoint ? " " + esc(inc.endpoint) : "") + " &middot; started " + esc(ago(inc.detected_at));
  if (active) meta += " &middot; ongoing for " + esc(dur(inc.detected_at));
  if (resolved) meta += " &middot; lasted " + esc(dur(inc.detected_at, inc.resolved_at));
  var d = state.diag[id];
  var cause = d && d.likely_cause ? '<span class="cause-chip" title="' + esc(d.likely_cause) + '">' + esc(d.likely_cause) + "</span>" : "";
  var html = '<div class="inc' + (active ? " active" : "") + '" id="inc-' + esc(id) + '"><div class="inc-row" data-toggle="' + esc(id) + '">' +
    badge + '<div class="inc-main"><div class="inc-title">' + esc(inc.message) + '</div><div class="meta">' + meta + "</div></div>" +
    cause + '<span class="chev">' + (isOpen ? "&#9650;" : "&#9660;") + "</span></div>";
  if (isOpen) {
    var mine = state.audit.filter(function (e) { return e.incident_id === id; });
    var robot = mine.length
      ? mine.map(function (e) { return auditRow(e, false); }).join("")
      : '<div class="muted">The controller has not made a decision for this incident.</div>';
    html += '<div class="detail"><div><h4>Diagnosis</h4>' + diagHtml(d) + "</div>" +
      "<div><h4>Robot decisions</h4>" + robot + "</div>" +
      "<div><h4>Evidence at the moment of detection</h4>" + evidenceHtml(inc) + "</div></div>";
  }
  return html + "</div>";
}

function renderIncidents() {
  var all = state.incidents;
  var nActive = all.filter(isActive).length;
  var nResolved = all.filter(function (i) { return i.status === "resolved"; }).length;
  function chip(key, label, n) {
    return '<button class="chip-btn' + (state.filter === key ? " on" : "") + '" data-filter="' + key + '">' + label + " " + n + "</button>";
  }
  setHtml("chips", chip("all", "All", all.length) + chip("active", "Active", nActive) + chip("resolved", "Resolved", nResolved));
  var list = all.filter(function (i) {
    if (state.filter === "active") return isActive(i);
    if (state.filter === "resolved") return i.status === "resolved";
    return true;
  });
  if (!list.length) {
    setHtml("incidents", '<div class="empty">' + (all.length ? "No incidents match this filter." :
      "No incidents yet. Release a bad version or switch on a chaos setting and watch this page.") + "</div>");
    return;
  }
  setHtml("incidents", list.map(incCard).join(""));
}

/* ---------- deployments ---------- */
function verNum(v) { var n = parseInt(String(v || "").replace(/\D/g, ""), 10); return isNaN(n) ? null : n; }

function renderDeployments() {
  var ov = state.overview;
  var running = "";
  if (ov && ov.services) {
    running = '<div class="running">' + ["user-service", "payment-service"].map(function (n) {
      var v = ov.services[n] && ov.services[n].version;
      return '<div class="run-item"><span>' + n + '</span><span class="chip">' + esc(v || "unknown") + "</span></div>";
    }).join("") + "</div>";
  }
  setHtml("running", running);
  var list = state.deployments.slice(0, 8);
  if (!list.length) {
    setHtml("deployments", '<div class="empty">No deployments recorded yet. Use deploy.ps1 to release a version.</div>');
    return;
  }
  setHtml("deployments", list.map(function (d) {
    var a = verNum(d.version), b = verNum(d.previous_version);
    var kind = (a !== null && b !== null && a < b) ? "rollback" : "release";
    return '<div class="dep-item"><span class="dep-dot ' + kind + '"></span><div>' +
      '<div class="dep-title">' + esc(d.service) + ' <span class="tag ' + kind + '">' + (kind === "rollback" ? "Rollback" : "Release") + "</span></div>" +
      '<div class="meta">' + esc(d.previous_version || "?") + " &rarr; " + esc(d.version) + " &middot; " +
      '<span title="' + esc(new Date(d.deployed_at).toLocaleString()) + '">' + esc(ago(d.deployed_at)) + "</span></div></div></div>";
  }).join(""));
}

function render() {
  renderStatus(); renderBanner(); renderKpis(); renderMap(); renderChart();
  renderIncidents(); renderDeployments(); renderRobot(); renderLive();
}

/* ---------- data ---------- */
function ensureDiag(id) {
  if (!id || state.diag[id] !== undefined) return;
  state.diag[id] = null;
  getJson("/investigate/" + id).then(function (d) { state.diag[id] = d; render(); })
    .catch(function () { delete state.diag[id]; });
}

function refresh() {
  Promise.all([
    getJson("/incidents"),
    getJson("/api/overview").catch(function () { return null; }),
    getJson("/deployments").catch(function () { return { deployments: state.deployments }; }),
    getJson("/audit?limit=100").catch(function () { return { entries: state.audit }; })
  ]).then(function (res) {
    state.incidents = res[0].incidents || [];
    if (res[1]) state.overview = res[1];
    state.deployments = res[2].deployments || [];
    state.audit = res[3].entries || [];
    state.error = false;
    state.updated = Date.now();
    state.incidents.slice(0, 8).forEach(function (i) { ensureDiag(i.incident_id); });
    render();
  }).catch(function () {
    state.error = true;
    render();
  });
}

/* ---------- events ---------- */
document.addEventListener("click", function (e) {
  var f = e.target.closest("[data-filter]");
  if (f) { state.filter = f.getAttribute("data-filter"); render(); return; }
  var o = e.target.closest("[data-open]");
  if (o) {
    var oid = o.getAttribute("data-open");
    state.openId = oid; state.filter = "all"; ensureDiag(oid); render();
    var el = $("inc-" + oid);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  var t = e.target.closest("[data-toggle]");
  if (t) {
    var id = t.getAttribute("data-toggle");
    state.openId = state.openId === id ? null : id;
    if (state.openId) ensureDiag(state.openId);
    render();
  }
});

document.addEventListener("mousemove", function (e) {
  var cross = $("cross"), tip = $("tip");
  if (!chartState || !cross || !tip) return;
  var wrap = e.target && e.target.closest ? e.target.closest("#chartwrap") : null;
  if (!wrap) { cross.style.display = "none"; tip.style.display = "none"; return; }
  var svg = wrap.querySelector("svg");
  var rect = svg.getBoundingClientRect();
  var xv = (e.clientX - rect.left) / rect.width * chartState.W;
  xv = Math.max(chartState.L, Math.min(chartState.W - chartState.R, xv));
  var t = chartState.t0 + ((xv - chartState.L) / chartState.iw) * (chartState.t1 - chartState.t0);
  var best = null;
  chartState.pts.forEach(function (p) {
    if (p[1] == null) return;
    var dist = Math.abs(p[0] * 1000 - t);
    if (best === null || dist < best.dist) best = { dist: dist, p: p };
  });
  if (!best) { cross.style.display = "none"; tip.style.display = "none"; return; }
  var vx = chartState.L + ((best.p[0] * 1000 - chartState.t0) / (chartState.t1 - chartState.t0)) * chartState.iw;
  var px = (vx / chartState.W) * rect.width + (rect.left - wrap.getBoundingClientRect().left);
  cross.style.left = px + "px"; cross.style.display = "block";
  tip.textContent = hhmmss(best.p[0] * 1000) + "  \u00b7  " + fmtPct(best.p[1]) + " failing";
  tip.style.left = Math.max(0, Math.min(px + 10, wrap.clientWidth - 150)) + "px";
  tip.style.top = "6px"; tip.style.display = "block";
});

render();
refresh();
setInterval(refresh, 5000);
setInterval(renderLive, 1000);
</script>
</body>
</html>
"""