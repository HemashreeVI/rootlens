DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RootLens Dashboard</title>
<style>
  :root {
    --bg: #f6f7f9; --card: #ffffff; --text: #1c2330; --muted: #667085;
    --line: #e4e7ec; --red: #d92d20; --redbg: #fef3f2; --green: #067647;
    --greenbg: #ecfdf3; --amber: #b54708; --amberbg: #fffaeb; --blue: #175cd3;
    --bar: #d0d5dd;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0f141b; --card: #171d27; --text: #e6e9ef; --muted: #8d98aa;
      --line: #273040; --red: #f97066; --redbg: #3a1a18; --green: #47cd89;
      --greenbg: #12301f; --amber: #fdb022; --amberbg: #3a2a0a; --blue: #84adff;
      --bar: #364154;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    line-height: 1.45;
  }
  .wrap { max-width: 920px; margin: 0 auto; padding: 24px 16px 64px; }
  header { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 8px; }
  h1 { font-size: 22px; margin: 0; }
  .sub { color: var(--muted); font-size: 13px; }
  .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 20px 0; }
  .stat { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; }
  .stat .n { font-size: 28px; font-weight: 650; }
  .stat .l { color: var(--muted); font-size: 13px; }
  .stat.open .n { color: var(--red); }
  .stat.resolved .n { color: var(--green); }
  .banner { background: var(--amberbg); color: var(--amber); border-radius: 10px; padding: 10px 14px; margin-bottom: 14px; font-size: 14px; display: none; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 12px; margin-bottom: 10px; overflow: hidden; }
  .row { display: flex; align-items: center; gap: 12px; padding: 14px 16px; cursor: pointer; }
  .row:hover { background: color-mix(in srgb, var(--text) 4%, transparent); }
  .badge { font-size: 12px; font-weight: 600; padding: 3px 10px; border-radius: 999px; white-space: nowrap; }
  .badge.open { background: var(--redbg); color: var(--red); }
  .badge.resolved { background: var(--greenbg); color: var(--green); }
  .badge.high { background: var(--greenbg); color: var(--green); }
  .badge.medium { background: var(--amberbg); color: var(--amber); }
  .badge.low, .badge.limited { background: var(--redbg); color: var(--red); }
  .main { flex: 1; min-width: 0; }
  .title { font-weight: 600; font-size: 15px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .meta { color: var(--muted); font-size: 12.5px; }
  .chev { color: var(--muted); font-size: 12px; }
  .detail { border-top: 1px solid var(--line); padding: 16px; display: grid; gap: 18px; }
  h3 { font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); margin: 0 0 8px; }
  .cause-line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 16px; font-weight: 600; }
  table { border-collapse: collapse; width: 100%; font-size: 14px; }
  td { padding: 6px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
  td:first-child { color: var(--muted); width: 55%; padding-right: 12px; }
  tr:last-child td { border-bottom: none; }
  .cause { margin-bottom: 14px; }
  .cause-head { display: flex; justify-content: space-between; font-size: 14px; font-weight: 600; margin-bottom: 4px; }
  .bar { height: 6px; border-radius: 4px; background: var(--bar); overflow: hidden; }
  .bar > div { height: 100%; background: var(--blue); }
  ul { margin: 6px 0 0; padding-left: 18px; font-size: 13px; }
  li.yes::marker { color: var(--green); content: "\2713  "; }
  li.no::marker { color: var(--red); content: "\2717  "; }
  .empty { text-align: center; color: var(--muted); padding: 40px 0; }
  @media (max-width: 560px) { .stats { grid-template-columns: 1fr 1fr 1fr; gap: 8px; } .stat .n { font-size: 22px; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>RootLens</h1>
    <div class="sub" id="updated">Loading...</div>
  </header>

  <div class="stats">
    <div class="stat open"><div class="n" id="s-open">0</div><div class="l">Open</div></div>
    <div class="stat resolved"><div class="n" id="s-resolved">0</div><div class="l">Resolved</div></div>
    <div class="stat"><div class="n" id="s-total">0</div><div class="l">Total</div></div>
  </div>

  <div class="banner" id="banner">Cannot reach the Order Service. Retrying...</div>
  <div id="list"></div>
</div>

<script>
  var openId = null;
  var lastIncidents = [];
  var diagCache = {};

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function fmtTime(t) {
    return t ? new Date(t).toLocaleString() : "-";
  }

  function duration(start, end) {
    var s = Math.max(0, Math.round(((end ? new Date(end) : new Date()) - new Date(start)) / 1000));
    if (s < 60) return s + "s";
    var m = Math.floor(s / 60);
    if (m < 60) return m + "m " + (s % 60) + "s";
    return Math.floor(m / 60) + "h " + (m % 60) + "m";
  }

  function getEvidence(inc) {
    if (inc.evidence) return inc.evidence;
    if (inc.evidence_json) {
      try { return JSON.parse(inc.evidence_json); } catch (e) { return null; }
    }
    return null;
  }

  var LABELS = {
    user_service_up: "User Service reachable (1 = yes, 0 = no)",
    order_failure_ratio: "Order failure ratio",
    user_service_error_ratio: "User Service error ratio",
    order_p95_latency_seconds: "Order p95 latency (seconds)",
    order_status_rates_per_second: "Order responses per second, by status"
  };

  function fmtValue(v) {
    if (v === null || v === undefined) return "no data";
    if (typeof v === "number") return String(Math.round(v * 1000) / 1000);
    if (typeof v === "object") {
      var keys = Object.keys(v);
      if (!keys.length) return "none";
      return keys.map(function (k) { return k + ": " + v[k]; }).join(", ");
    }
    return String(v);
  }

  function evidenceHtml(ev) {
    if (!ev) return '<div class="meta">No evidence was saved for this incident.</div>';
    var keys = Object.keys(LABELS).filter(function (k) { return k in ev; });
    var rows = keys.map(function (k) {
      return "<tr><td>" + esc(LABELS[k]) + "</td><td>" + esc(fmtValue(ev[k])) + "</td></tr>";
    }).join("");
    return "<table>" + rows + "</table>";
  }

  function diagnosisHtml(d) {
    if (!d) return '<div class="meta">Loading diagnosis...</div>';
    var html = '<div class="cause-line">' + esc(d.likely_cause) +
      ' <span class="badge ' + esc(d.confidence) + '">' + esc(d.confidence) + " confidence</span></div>";

    var ranked = d.ranked_causes || [];
    if (ranked.length) {
      html += '<div style="margin-top:14px">';
      ranked.forEach(function (c) {
        html += '<div class="cause"><div class="cause-head"><span>' + esc(c.cause) +
          "</span><span>" + Math.round(c.score * 100) + '%</span></div>' +
          '<div class="bar"><div style="width:' + Math.round(c.score * 100) + '%"></div></div>';
        var sup = c.supporting_evidence || [];
        var con = c.contradicting_evidence || [];
        if (sup.length || con.length) {
          html += "<ul>";
          sup.forEach(function (t) { html += '<li class="yes">' + esc(t) + "</li>"; });
          con.forEach(function (t) { html += '<li class="no">' + esc(t) + "</li>"; });
          html += "</ul>";
        }
        html += "</div>";
      });
      html += "</div>";
    } else if (d.observed_facts && d.observed_facts.length) {
      html += "<ul>" + d.observed_facts.map(function (t) { return "<li>" + esc(t) + "</li>"; }).join("") + "</ul>";
    }
    return html;
  }

  function render(incidents) {
    var open = incidents.filter(function (i) { return i.status === "open"; }).length;
    document.getElementById("s-open").textContent = open;
    document.getElementById("s-resolved").textContent = incidents.length - open;
    document.getElementById("s-total").textContent = incidents.length;

    var list = document.getElementById("list");
    if (!incidents.length) {
      list.innerHTML = '<div class="empty">No incidents yet. Break something and watch this page.</div>';
      return;
    }

    list.innerHTML = incidents.map(function (inc) {
      var isOpen = inc.id === openId || inc.incident_id === openId;
      var status = inc.status || "open";
      var html = '<div class="card"><div class="row" data-id="' + esc(inc.incident_id) + '">' +
        '<span class="badge ' + esc(status) + '">' + esc(status) + "</span>" +
        '<div class="main"><div class="title">' + esc(inc.message) + "</div>" +
        '<div class="meta">' + esc(inc.service) + (inc.endpoint ? " " + esc(inc.endpoint) : "") +
        " &middot; " + esc(fmtTime(inc.detected_at)) +
        " &middot; " + (status === "open" ? "ongoing for " : "lasted ") +
        esc(duration(inc.detected_at, inc.resolved_at)) + "</div></div>" +
        '<span class="chev">' + (isOpen ? "&#9650;" : "&#9660;") + "</span></div>";

      if (isOpen) {
        html += '<div class="detail"><div><h3>Diagnosis</h3>' +
          diagnosisHtml(diagCache[inc.incident_id]) + "</div>" +
          "<div><h3>Evidence at the moment of detection</h3>" +
          evidenceHtml(getEvidence(inc)) + "</div></div>";
      }
      return html + "</div>";
    }).join("");
  }

  function fetchDiagnosis(id) {
    fetch("/investigate/" + id)
      .then(function (r) { return r.json(); })
      .then(function (d) { diagCache[id] = d; render(lastIncidents); })
      .catch(function () {});
  }

  document.getElementById("list").addEventListener("click", function (e) {
    var row = e.target.closest("[data-id]");
    if (!row) return;
    var id = row.getAttribute("data-id");
    openId = openId === id ? null : id;
    if (openId && !diagCache[openId]) fetchDiagnosis(openId);
    render(lastIncidents);
  });

  function load() {
    fetch("/incidents")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        lastIncidents = data.incidents || [];
        document.getElementById("banner").style.display = "none";
        document.getElementById("updated").textContent =
          "Updated " + new Date().toLocaleTimeString() + " - refreshes every 5s";
        render(lastIncidents);
      })
      .catch(function () {
        document.getElementById("banner").style.display = "block";
      });
  }

  load();
  setInterval(load, 5000);
</script>
</body>
</html>
"""