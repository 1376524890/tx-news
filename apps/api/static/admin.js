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

const rtDom = {
  inited: false,
  metrics: {},
  flow: {},
};

const logState = {
  lastKey: "",
  lastTail: "",
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

function computeSparkPoints(values) {
  const w = 120;
  const h = 28;
  const vs = (values || []).slice(-RT_MAX_POINTS);
  if (vs.length < 2) return "";
  const min = Math.min(...vs);
  const max = Math.max(...vs);
  const span = max - min || 1;
  return vs
    .map((v, i) => {
      const x = (i / (vs.length - 1)) * (w - 2) + 1;
      const y = h - 2 - ((v - min) / span) * (h - 4);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

function ensureSpark(containerEl, color) {
  if (containerEl._spark) return containerEl._spark;
  const w = 120;
  const h = 28;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "spark");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("width", String(w));
  svg.setAttribute("height", String(h));
  const pl = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  pl.setAttribute("fill", "none");
  pl.setAttribute("stroke", color);
  pl.setAttribute("stroke-width", "2");
  pl.setAttribute("stroke-linecap", "round");
  pl.setAttribute("stroke-linejoin", "round");
  svg.appendChild(pl);
  containerEl.innerHTML = "";
  containerEl.appendChild(svg);
  containerEl._spark = pl;
  return pl;
}

function setSpark(polylineEl, values) {
  polylineEl.setAttribute("points", computeSparkPoints(values));
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
  qs("rtStatus").textContent = ok ? "正常" : "依赖异常";

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

  const metricsSpec = [
    ["raw", "原始抓取 / 窗口", () => fmt(counts.raw_documents), rt.raw, "#93c5fd", "时间窗口内 raw_documents 总数"],
    ["ver", "版本入库 / 窗口", () => fmt(counts.article_versions), rt.ver, "#a7f3d0", "时间窗口内 article_versions 总数"],
    ["ana", "分析产出 / 窗口", () => fmt(counts.analyses), rt.ana, "#fcd34d", "时间窗口内 analyses 总数"],
    ["lag", "延迟（秒）", () => (lag == null ? "-" : Number(lag).toFixed(1)), rt.lag, "#fca5a5", "raw 最新 created_at - analyses 最新 created_at"],
    ["js", "队列积压", () => fmt(pending), rt.js_pending, "#c4b5fd", "NATS JetStream consumer.num_pending"],
  ];

  if (!rtDom.inited) {
    const rtEl = qs("rtMetrics");
    rtEl.innerHTML = "";
    for (const [key, label, getVal, series, color, hint] of metricsSpec) {
      const card = document.createElement("div");
      card.className = "metric";
      const k = document.createElement("div");
      k.className = "metric-k";
      k.textContent = label;
      const v = document.createElement("div");
      v.className = "metric-v";
      v.textContent = getVal();
      const s = document.createElement("div");
      s.className = "metric-s";
      const h = document.createElement("div");
      h.className = "metric-h";
      h.textContent = hint;
      card.appendChild(k);
      card.appendChild(v);
      card.appendChild(s);
      card.appendChild(h);
      rtEl.appendChild(card);
      const pl = ensureSpark(s, color);
      setSpark(pl, series);
      rtDom.metrics[key] = { v, pl };
    }

    const flow = qs("rtFlow");
    flow.innerHTML = "";
    const row = document.createElement("div");
    row.className = "flow-row";
    flow.appendChild(row);

    function addNode(k, title) {
      const node = document.createElement("div");
      node.className = "node";
      const t = document.createElement("div");
      t.className = "node-title";
      t.textContent = title;
      const sub = document.createElement("div");
      sub.className = "node-sub";
      sub.textContent = "-";
      node.appendChild(t);
      node.appendChild(sub);
      row.appendChild(node);
      rtDom.flow[k] = { node, sub };
    }

    function addArrow() {
      const a = document.createElement("div");
      a.className = "arrow";
      a.textContent = "→";
      row.appendChild(a);
    }

    addNode("collector", "采集器");
    addArrow();
    addNode("nats", "NATS");
    addArrow();
    addNode("worker", "处理器");
    addArrow();
    addNode("pg", "Postgres");
    addNode("qdrant", "Qdrant");
    addNode("minio", "MinIO");

    rtDom.inited = true;
  }

  for (const [key, _label, getVal, series] of metricsSpec) {
    const m = rtDom.metrics[key];
    if (!m) continue;
    m.v.textContent = getVal();
    setSpark(m.pl, series);
  }

  const d = st.dependencies || {};
  const qdrantInfo = st.qdrant || {};
  const s3Info = st.s3 || {};
  const setNode = (k, ok2, subText) => {
    const x = rtDom.flow[k];
    if (!x) return;
    x.node.className = "node " + (ok2 ? "ok" : "bad");
    x.sub.textContent = subText || "-";
  };
  setNode("collector", true, "发布到 NATS");
  setNode("nats", !!(d.nats && d.nats.ok), `pending=${fmt(pending)}`);
  setNode("worker", true, "执行任务链");
  setNode("pg", !!(d.postgres && d.postgres.ok), `articles=${fmt((st.counts || {}).articles)}`);
  setNode("qdrant", !!(d.qdrant && d.qdrant.ok), `collection=${qdrantInfo.collection || "-"}`);
  setNode("minio", !!(d.minio && d.minio.ok), `bucket=${s3Info.bucket || "-"}`);
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

  const key = `${wrap ? 1 : 0}|${level}|${search}`;
  const tail = String(text || "");
  if (logState.lastTail === tail && logState.lastKey === key) return;
  logState.lastTail = tail;
  logState.lastKey = key;

  const minRank = level === "ALL" ? 0 : levelRank(level);
  const lines = tail.split("\\n");
  const el = qs("logTail");
  el.className = "log-view" + (wrap ? " wrap" : "");
  const prevScroll = el.scrollTop;

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
  else el.scrollTop = prevScroll;
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
