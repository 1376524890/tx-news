const STORAGE_KEY = "txnews_chat_v1";

function qs(id) {
  return document.getElementById(id);
}

function nowIso() {
  return new Date().toISOString();
}

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function loadMessages() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const obj = JSON.parse(raw);
    if (!Array.isArray(obj)) return [];
    return obj;
  } catch {
    return [];
  }
}

function saveMessages(messages) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(messages.slice(-40)));
}

function appendMessage(role, content, meta) {
  const chat = qs("chat");
  const row = document.createElement("div");
  row.className = "msg";

  const roleEl = document.createElement("div");
  roleEl.className = "role";
  roleEl.textContent = role === "user" ? "你" : "助手";

  const bubble = document.createElement("div");
  bubble.className = "bubble " + (role === "user" ? "user" : "assistant");

  const contentEl = document.createElement("div");
  contentEl.className = "content";
  contentEl.textContent = content || "";

  bubble.appendChild(contentEl);

  if (meta) {
    const metaEl = document.createElement("div");
    metaEl.className = "meta";

    if (meta.tools && meta.tools.length) {
      const title = document.createElement("div");
      title.innerHTML = `<span class="pill">工具调用</span>`;
      metaEl.appendChild(title);

      for (const t of meta.tools) {
        const item = document.createElement("div");
        item.innerHTML = `<code>${escapeHtml(t.name)}(${escapeHtml(JSON.stringify(t.arguments || {}))})</code>`;
        metaEl.appendChild(item);
      }
    }

    if (meta.evidence && meta.evidence.length) {
      const title = document.createElement("div");
      title.style.marginTop = "8px";
      title.innerHTML = `<span class="pill">证据</span>`;
      metaEl.appendChild(title);
      for (const e of meta.evidence) {
        const item = document.createElement("div");
        const url = e.url ? `<a href="${escapeHtml(e.url)}" target="_blank" rel="noreferrer">${escapeHtml(e.url)}</a>` : "-";
        item.innerHTML = `<div>${escapeHtml(e.source_id || "-")} · ${escapeHtml(e.published_at || "-")} · ${url}</div>`;
        metaEl.appendChild(item);
      }
    }

    bubble.appendChild(metaEl);
  }

  row.appendChild(roleEl);
  row.appendChild(bubble);
  chat.appendChild(row);
  chat.scrollTop = chat.scrollHeight;
}

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!res.ok) {
    const msg = (data && (data.error || data.detail)) || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

async function refreshStatus() {
  try {
    const st = await api("/admin/status");
    const deps = st.dependencies || {};
    const ok = Object.values(deps).every((x) => x && x.ok);
    qs("statusDot").className = "dot " + (ok ? "dot-ok" : "dot-warn");
    qs("statusText").textContent = ok ? "正常" : "依赖异常";

    qs("countArticles").textContent = (st.counts && st.counts.articles) ?? "-";
    qs("countAnalyses").textContent = (st.counts && st.counts.analyses) ?? "-";
    qs("countSignals").textContent = (st.counts && st.counts.signals) ?? "-";
    qs("countAShare").textContent = (st.counts && st.counts.a_share_basic) ?? "-";
  } catch (e) {
    qs("statusDot").className = "dot dot-bad";
    qs("statusText").textContent = "连接失败";
  }
}

async function refreshSignals() {
  const list = qs("signals");
  list.innerHTML = "";
  try {
    const rows = await api("/signals?limit=30");
    for (const r of rows) {
      const card = document.createElement("div");
      card.className = "card";
      const title = document.createElement("div");
      title.className = "title";
      title.textContent = `${r.kind || "signal"} · ${r.created_at || ""}`;
      const small = document.createElement("div");
      small.className = "small";
      const cid = r.canonical_id || "";
      small.innerHTML = `canonical_id: <code>${escapeHtml(cid)}</code>`;
      card.appendChild(title);
      card.appendChild(small);
      list.appendChild(card);
    }
  } catch (e) {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `<div class="small">加载失败：${escapeHtml(String(e.message || e))}</div>`;
    list.appendChild(card);
  }
}

async function sendMessage(text) {
  const btn = qs("btnSend");
  btn.disabled = true;
  try {
    const messages = loadMessages();
    const next = [...messages, { role: "user", content: text, ts: nowIso() }];
    saveMessages(next);

    const payload = {
      messages: next.map((m) => ({ role: m.role, content: m.content })),
    };
    const res = await api("/chat", { method: "POST", body: JSON.stringify(payload) });

    const assistant = {
      role: "assistant",
      content: (res.message && res.message.content) || "",
      ts: nowIso(),
      meta: (res.message && res.message.meta) || null,
    };
    const final = [...next, assistant];
    saveMessages(final);
    appendMessage("assistant", assistant.content, assistant.meta);
  } finally {
    btn.disabled = false;
  }
}

function bootstrap() {
  const existing = loadMessages();
  if (existing.length === 0) {
    appendMessage(
      "assistant",
      "我可以帮你基于本地数据库/知识库检索当天的高时效新闻，并给出结构化影响分析（不展示原文）。\n\n你可以问：\n- 今天有哪些突发影响A股？\n- 某个 ts_code（如 600519.SH）近期有什么事件？\n- 解释某条新闻可能的市场传导路径。",
      null
    );
    saveMessages([{ role: "assistant", content: "ready", ts: nowIso() }].slice(0, 0)); // keep empty
  } else {
    for (const m of existing) {
      appendMessage(m.role, m.content, m.meta || null);
    }
  }

  qs("btnClear").addEventListener("click", () => {
    localStorage.removeItem(STORAGE_KEY);
    qs("chat").innerHTML = "";
  });
  qs("btnRefresh").addEventListener("click", async () => {
    await refreshStatus();
    await refreshSignals();
  });

  qs("composer").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = qs("input");
    const text = (input.value || "").trim();
    if (!text) return;
    appendMessage("user", text, null);
    input.value = "";
    await sendMessage(text);
  });

  refreshStatus();
  refreshSignals();
  setInterval(refreshStatus, 8000);
  setInterval(refreshSignals, 15000);
}

bootstrap();

