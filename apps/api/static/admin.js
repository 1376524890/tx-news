// Input: 浏览器 UI 事件 + /admin/* API
// Output: 管理台渲染（依赖健康/进程/流水线/日志/主数据缓存）
// Pos: 管理台前端逻辑（变更时同步更新以上注释与所属目录 FOLDER.md）

function qs(id) {
  return document.getElementById(id);
}

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

async function refreshLog() {
  const name = qs("logName").value || "api";
  try {
    const data = await api(`/admin/logs/${encodeURIComponent(name)}?n=200`);
    qs("logTail").textContent = data.tail || "";
  } catch (e) {
    qs("logTail").textContent = `加载失败：${e.message || e}`;
  }
}

async function refreshAll() {
  await Promise.all([refreshDeps(), refreshProcs(), refreshPipeline(), refreshTushare(), refreshLog()]);
}

function bootstrap() {
  qs("btnRefresh").addEventListener("click", refreshAll);
  qs("window").addEventListener("change", refreshPipeline);
  qs("logName").addEventListener("change", refreshLog);
  refreshAll();
  setInterval(refreshAll, 12000);
}

bootstrap();
