// Input: 浏览器 UI 事件 + /admin/* API + 实时采样数据
// Output: 管理台渲染（健康/进程/吞吐/延迟/数据流/日志视图）
// Pos: 管理台前端逻辑（变更时同步更新以上注释与所属目录 FOLDER.md）

function qs(id) {
  return document.getElementById(id);
}

const RT_MAX_POINTS = 60;
const rt = {
  ts: [],
  raw: [],
  ver: [],
  ana: [],
  lag: [],
  js_pending: [],
};

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" } });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!res.ok) throw new Error((data && (data.error || data.detail)) || `HTTP ${res.status}`);
  return data;
}

function renderCard(title, lines, ok) {
  const card = document.createElement("div");
  card.className = "card";
  const t = document.createElement("div");
  t.className = "title";
  t.textContent = title;
  const s = document.createElement("div");
  s.className = "small";
  s.innerHTML = lines.map((x) => `<div>${escapeHtml(x)}</div>`).join("");
  if (ok === false) {
    t.style.color = "#fca5a5";
  } else if (ok === true) {
    t.style.color = "#86efac";
  }
  card.appendChild(t);
  card.appendChild(s);
  return card;
}

function fmt(n) {
  if (n == null) return "-";
  const x = Number(n);
  if (!Number.isFinite(x)) return String(n);
  if (x >= 1e9) return (x / 1e9).toFixed(2) + "B";
  if (x >= 1e6) return (x / 1e6).toFixed(2) + "M";
  if (x >= 1e3) return (x / 1e3).toFixed(1) + "K";
  return String(Math.round(x * 10) / 10);
}

function sparkline(values, color) {
  const w = 120;
  const h = 28;
  const vs = (values || []).slice(-RT_MAX_POINTS);
  if (vs.length < 2) return `<svg width="${w}" height="${h}"></svg>`;
  const min = Math.min(...vs);
  const max = Math.max(...vs);
  const span = max - min || 1;
  const pts = vs
    .map((v, i) => {
      const x = (i / (vs.length - 1)) * (w - 2) + 1;
      const y = h - 2 - ((v - min) / span) * (h - 4);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}">
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></polyline>
  </svg>`;
}

function pushSeries(arr, v) {
  arr.push(v == null ? 0 : Number(v) || 0);
  if (arr.length > RT_MAX_POINTS) arr.shift();
}

function pushTick(ts) {
  rt.ts.push(ts);
  if (rt.ts.length > RT_MAX_POINTS) rt.ts.shift();
}

function renderRealtime(st, pipe) {
  const deps = (st && st.dependencies) || {};
  const ok = Object.values(deps).every((x) => x && x.ok);
  qs("rtStatus").className = "badge " + (ok ? "badge-ok" : "badge-warn");
  qs("rtStatus").textContent = ok ? "OK" : "DEGRADED";

  const js = st.nats_jetstream || {};
  const counts = (pipe && pipe.counts) || {};
  const lag = pipe.lag_seconds_raw_minus_analysis;
  const pending = js.num_pending != null ? js.num_pending : 0;

  pushTick(Date.now());
  pushSeries(rt.raw, counts.raw_documents || 0);
  pushSeries(rt.ver, counts.article_versions || 0);
  pushSeries(rt.ana, counts.analyses || 0);
  pushSeries(rt.lag, lag || 0);
  pushSeries(rt.js_pending, pending || 0);

  const metrics = [
    {
      k: "raw / window",
      v: fmt(counts.raw_documents),
      s: sparkline(rt.raw, "#93c5fd"),
      hint: "窗口内 raw_docs 总数",
    },
    {
      k: "versions / window",
      v: fmt(counts.article_versions),
      s: sparkline(rt.ver, "#a7f3d0"),
      hint: "窗口内 versions 总数",
    },
    {
      k: "analyses / window",
      v: fmt(counts.analyses),
      s: sparkline(rt.ana, "#fcd34d"),
      hint: "窗口内 analyses 总数",
    },
    {
      k: "lag (s)",
      v: lag == null ? "-" : Number(lag).toFixed(1),
      s: sparkline(rt.lag, "#fca5a5"),
      hint: "raw 最新时间 - analysis 最新时间",
    },
    {
      k: "js pending",
      v: fmt(pending),
      s: sparkline(rt.js_pending, "#c4b5fd"),
      hint: "JetStream consumer.num_pending",
    },
  ];

  const rtEl = qs("rtMetrics");
  rtEl.innerHTML = metrics
    .map(
      (m) => `<div class="metric">
        <div class="metric-k">${escapeHtml(m.k)}</div>
        <div class="metric-v">${escapeHtml(m.v)}</div>
        <div class="metric-s">${m.s}</div>
        <div class="metric-h">${escapeHtml(m.hint || "")}</div>
      </div>`
    )
    .join("");

  const flow = qs("rtFlow");
  const d = st.dependencies || {};
  const node = (name, ok2, extra) => {
    const cls = ok2 ? "node ok" : "node bad";
    const ex = extra ? `<div class="node-sub">${escapeHtml(extra)}</div>` : "";
    return `<div class="${cls}"><div class="node-title">${escapeHtml(name)}</div>${ex}</div>`;
  };
  const qdrantInfo = st.qdrant || {};
  const s3Info = st.s3 || {};
  flow.innerHTML = `
    <div class="flow-row">
      ${node("Collector", true, "feeds NATS")}
      <div class="arrow">→</div>
      ${node("NATS", !!(d.nats && d.nats.ok), `pending=${fmt(pending)}`)}
      <div class="arrow">→</div>
      ${node("Worker", true, "Celery tasks")}
      <div class="arrow">→</div>
      ${node("Postgres", !!(d.postgres && d.postgres.ok), `articles=${fmt((st.counts||{}).articles)}`)}
      ${node("Qdrant", !!(d.qdrant && d.qdrant.ok), `collection=${escapeHtml(qdrantInfo.collection||"-")}`)}
      ${node("MinIO", !!(d.minio && d.minio.ok), `bucket=${escapeHtml(s3Info.bucket||"-")}`)}
    </div>
  `;
}

async function refreshDeps() {
  const el = qs("deps");
  el.innerHTML = "";
  const st = await api("/admin/status");
  const deps = st.dependencies || {};
  for (const [name, v] of Object.entries(deps)) {
    const ok = !!(v && v.ok);
    const lines = ok ? ["ok"] : [`error: ${(v && v.error) || "-"}`];
    el.appendChild(renderCard(name, lines, ok));
  }

  const js = st.nats_jetstream || {};
  const jsLines = [];
  if (js.stream_error) jsLines.push(`stream_error: ${js.stream_error}`);
  if (js.consumer_error) jsLines.push(`consumer_error: ${js.consumer_error}`);
  if (js.messages != null) jsLines.push(`stream.messages: ${js.messages}`);
  if (js.num_pending != null) jsLines.push(`consumer.num_pending: ${js.num_pending}`);
  if (js.num_ack_pending != null) jsLines.push(`consumer.num_ack_pending: ${js.num_ack_pending}`);
  if (js.num_redelivered != null) jsLines.push(`consumer.num_redelivered: ${js.num_redelivered}`);
  el.appendChild(renderCard("nats_jetstream", jsLines.length ? jsLines : ["-"], !js.stream_error && !js.consumer_error));
}

async function refreshProcs() {
  const el = qs("procs");
  el.innerHTML = "";
  const data = await api("/admin/processes");
  const procs = data.processes || [];
  if (procs.length === 0) {
    el.appendChild(renderCard("processes", ["未发现 .run/*.pid（若你用 scripts/start.sh 启动会自动生成）"], false));
    return;
  }
  for (const p of procs) {
    const ok = !!p.alive;
    const lines = [
      `pid: ${p.pid ?? "-"}`,
      `pidfile: ${p.pidfile ?? "-"}`,
      p.cmdline ? `cmd: ${p.cmdline}` : "",
    ].filter(Boolean);
    el.appendChild(renderCard(p.name, lines, ok));
  }
}

async function refreshPipeline() {
  const minutes = parseInt(qs("window").value, 10) || 60;
  const data = await api(`/admin/pipeline?minutes=${minutes}`);
  const c = data.counts || {};
  qs("mRaw").textContent = c.raw_documents ?? "-";
  qs("mVer").textContent = c.article_versions ?? "-";
  qs("mAna").textContent = c.analyses ?? "-";
  qs("mLag").textContent =
    data.lag_seconds_raw_minus_analysis == null ? "-" : data.lag_seconds_raw_minus_analysis.toFixed(1);

  const latestEl = qs("latest");
  latestEl.innerHTML = "";
  const latest = data.latest || {};

  if (latest.raw_document) {
    latestEl.appendChild(
      renderCard("latest.raw_document", [
        `source_id: ${latest.raw_document.source_id || "-"}`,
        `fetched_at: ${latest.raw_document.fetched_at || "-"}`,
        `url: ${latest.raw_document.url || "-"}`,
      ])
    );
  }
  if (latest.article_version) {
    latestEl.appendChild(
      renderCard("latest.article_version", [
        `canonical_id: ${latest.article_version.canonical_id || "-"}`,
        `time: ${latest.article_version.time || "-"}`,
        `url: ${latest.article_version.url || "-"}`,
      ])
    );
  }
  if (latest.analysis) {
    latestEl.appendChild(
      renderCard("latest.analysis", [
        `canonical_id: ${latest.analysis.canonical_id || "-"}`,
        `event_type: ${latest.analysis.event_type || "-"}`,
        `created_at: ${latest.analysis.created_at || "-"}`,
      ])
    );
  }

  const kinds = c.signals_by_kind || {};
  const signalLines = Object.entries(kinds).map(([k, v]) => `${k}: ${v}`);
  latestEl.appendChild(renderCard("signals_by_kind", signalLines.length ? signalLines : ["-"]));
}

async function refreshTushare() {
  const el = qs("tushare");
  el.innerHTML = "";
  const data = await api("/admin/masterdata");
  const c = data.stock_basic_cache;
  if (!c) {
    el.appendChild(renderCard("stock_basic_cache", ["未生成缓存文件（首次 sync_tushare 成功后会创建）"], false));
    return;
  }
  const ok = !c.error;
  el.appendChild(
    renderCard(
      "stock_basic.json",
      [
        `path: ${c.path}`,
        `source: ${c.source || "-"}`,
        `fetched_at: ${c.fetched_at || "-"}`,
        `rows: ${c.rows ?? "-"}`,
      ],
      ok
    )
  );
}

function parseLogLine(line) {
  const m = String(line || "").match(/^\[?\\d{4}-\\d{2}-\\d{2}[^\\]]*\\]?[: ]\\s*(DEBUG|INFO|WARNING|ERROR|CRITICAL)\\b\\s*(.*)$/);
  if (m) return { level: m[1], rest: m[2] || "" };
  const m2 = String(line || "").match(/\\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\\b/);
  return { level: m2 ? m2[1] : "INFO", rest: String(line || "") };
}

function levelRank(level) {
  const x = String(level || "INFO").toUpperCase();
  return { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50 }[x] || 20;
}

function renderLogs(text) {
  const follow = !!qs("logFollow").checked;
  const wrap = !!qs("logWrap").checked;
  const level = qs("logLevel").value || "ALL";
  const search = String(qs("logSearch").value || "").trim().toLowerCase();

  const minRank = level === "ALL" ? 0 : levelRank(level);
  const lines = String(text || "").split("\\n");
  const el = qs("logTail");
  el.className = "log-view" + (wrap ? " wrap" : "");

  const out = [];
  for (const line of lines) {
    if (!line) continue;
    const p = parseLogLine(line);
    const r = levelRank(p.level);
    if (r < minRank) continue;
    if (search && !String(line).toLowerCase().includes(search)) continue;
    out.push(
      `<div class="log-line lvl-${escapeHtml(p.level)}"><span class="lv">${escapeHtml(p.level)}</span><span class="tx">${escapeHtml(line)}</span></div>`
    );
  }
  el.innerHTML = out.join("");
  if (follow) el.scrollTop = el.scrollHeight;
}

async function refreshLog() {
  const name = qs("logName").value || "api";
  try {
    const data = await api(`/admin/logs/${encodeURIComponent(name)}?n=200`);
    renderLogs(data.tail || "");
  } catch (e) {
    renderLogs(`ERROR ${e.message || e}`);
  }
}

async function refreshRealtime() {
  const minutes = parseInt(qs("window").value, 10) || 60;
  const [st, pipe] = await Promise.all([api("/admin/status"), api(`/admin/pipeline?minutes=${minutes}`)]);
  renderRealtime(st, pipe);
}

async function refreshAll() {
  await Promise.all([refreshRealtime(), refreshDeps(), refreshProcs(), refreshPipeline(), refreshTushare(), refreshLog()]);
}

function bootstrap() {
  qs("btnRefresh").addEventListener("click", refreshAll);
  qs("window").addEventListener("change", refreshPipeline);
  qs("logName").addEventListener("change", refreshLog);
  qs("logFollow").addEventListener("change", refreshLog);
  qs("logWrap").addEventListener("change", refreshLog);
  qs("logLevel").addEventListener("change", refreshLog);
  qs("logSearch").addEventListener("input", refreshLog);
  refreshAll();
  setInterval(refreshRealtime, 3000);
  setInterval(refreshDeps, 12000);
  setInterval(refreshProcs, 12000);
  setInterval(refreshPipeline, 8000);
  setInterval(refreshTushare, 30000);
  setInterval(() => {
    if (qs("logFollow").checked) refreshLog();
  }, 4000);
}

bootstrap();
