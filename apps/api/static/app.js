// Input: 浏览器 UI 事件 + /chat/stream、/signals、/admin/status 等 API + Markdown 文本
// Output: 对话页渲染（含 Markdown）、请求封装与本地会话存储
// Pos: 对话页前端逻辑（变更时同步更新以上注释与所属目录 FOLDER.md）

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

function safeHref(href) {
  const raw = String(href || "").trim();
  if (!raw) return "#";
  if (raw.startsWith("http://") || raw.startsWith("https://")) return raw;
  return "#";
}

function renderEmphasisEscaped(html) {
  // Minimal, safe subset: **bold**
  return String(html || "").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function renderInlineMarkdown(raw) {
  // Split by inline code spans to avoid interpreting markdown inside code.
  const parts = String(raw || "").split(/(`[^`]+`)/g);
  const out = [];
  for (const part of parts) {
    if (part.startsWith("`") && part.endsWith("`")) {
      out.push(`<code>${escapeHtml(part.slice(1, -1))}</code>`);
      continue;
    }

    // Links: [text](url)
    const seg = String(part || "");
    const re = /\[([^\]]+)\]\(([^)]+)\)/g;
    let idx = 0;
    let m;
    while ((m = re.exec(seg))) {
      const before = seg.slice(idx, m.index);
      out.push(renderEmphasisEscaped(escapeHtml(before)));
      const text = renderEmphasisEscaped(escapeHtml(m[1]));
      const href = safeHref(m[2]);
      out.push(`<a href="${escapeHtml(href)}" target="_blank" rel="noreferrer">${text}</a>`);
      idx = re.lastIndex;
    }
    out.push(renderEmphasisEscaped(escapeHtml(seg.slice(idx))));
  }
  return out.join("");
}

function renderMarkdown(md) {
  const lines = String(md || "").replaceAll("\r\n", "\n").split("\n");
  const out = [];

  let inCode = false;
  let codeLang = "";
  let codeLines = [];
  let para = [];
  let list = [];
  let table = null; // string[][]
  let tableAlign = null; // ("left"|"center"|"right"|null)[]

  function flushPara() {
    if (!para.length) return;
    const text = para.join("\n").trimEnd();
    if (!text) {
      para = [];
      return;
    }
    const html = renderInlineMarkdown(text).replaceAll("\n", "<br />");
    out.push(`<p>${html}</p>`);
    para = [];
  }

  function flushList() {
    if (!list.length) return;
    const items = list.map((x) => `<li>${renderInlineMarkdown(x)}</li>`).join("");
    out.push(`<ul>${items}</ul>`);
    list = [];
  }

  function flushTable() {
    if (!table || table.length < 2) {
      table = null;
      tableAlign = null;
      return;
    }
    const header = table[0];
    const rows = table.slice(1);
    const aligns = tableAlign || header.map(() => null);
    const ths = header
      .map((cell, i) => {
        const a = aligns[i];
        const style = a ? ` style="text-align:${a}"` : "";
        return `<th${style}>${renderInlineMarkdown(cell)}</th>`;
      })
      .join("");
    const trs = rows
      .map((r) => {
        const tds = header
          .map((_, i) => {
            const cell = (r[i] ?? "").trim();
            const a = aligns[i];
            const style = a ? ` style="text-align:${a}"` : "";
            return `<td${style}>${renderInlineMarkdown(cell)}</td>`;
          })
          .join("");
        return `<tr>${tds}</tr>`;
      })
      .join("");
    out.push(`<div class="md-table"><table><thead><tr>${ths}</tr></thead><tbody>${trs}</tbody></table></div>`);
    table = null;
    tableAlign = null;
  }

  function flushCode() {
    const code = escapeHtml(codeLines.join("\n"));
    const lang = escapeHtml(codeLang || "");
    out.push(
      `<pre class="code"><code data-lang="${lang}">${code}</code></pre>`
    );
    codeLines = [];
    codeLang = "";
  }

  function parseTableRow(line) {
    let s = String(line || "").trim();
    if (!s.includes("|")) return null;
    if (s.startsWith("|")) s = s.slice(1);
    if (s.endsWith("|")) s = s.slice(0, -1);
    const cells = s.split("|").map((x) => x.trim());
    if (cells.length < 2) return null;
    return cells;
  }

  function parseAlignRow(line) {
    const cells = parseTableRow(line);
    if (!cells) return null;
    const aligns = [];
    for (const c of cells) {
      const t = c.replaceAll(" ", "");
      if (!/^:?-+:?$/.test(t)) return null;
      const left = t.startsWith(":");
      const right = t.endsWith(":");
      if (left && right) aligns.push("center");
      else if (right) aligns.push("right");
      else if (left) aligns.push("left");
      else aligns.push(null);
    }
    return aligns;
  }

  for (const lineRaw of lines) {
    const line = String(lineRaw || "");
    const fence = line.match(/^```(\w+)?\s*$/);
    if (fence) {
      if (inCode) {
        flushCode();
        inCode = false;
      } else {
        flushPara();
        flushList();
        flushTable();
        inCode = true;
        codeLang = fence[1] || "";
      }
      continue;
    }

    if (inCode) {
      codeLines.push(line);
      continue;
    }

    // Pipe table (GitHub style): header | header, second line ---|---, then body rows.
    // We only start a table when we see header row followed by a valid align row.
    if (!table) {
      // Lookahead requires peeking next line; handled by buffering in `table` when we can.
      // Here we opportunistically detect a header row and stash it in `table`, then wait for align row.
      const header = parseTableRow(line);
      if (header) {
        // We can't look ahead easily in this streaming loop, so mark pending header with a sentinel
        // and confirm when we see the next align row.
        // Use table as pending with one row until align row arrives.
        table = [header];
        tableAlign = null;
        continue;
      }
    } else if (table && table.length === 1 && tableAlign == null) {
      // Expect align row; otherwise fall back to paragraph.
      const aligns = parseAlignRow(line);
      if (aligns) {
        tableAlign = aligns;
        continue;
      }
      // Not a table; push pending header back into paragraph and continue parsing current line.
      para.push(table[0].join(" | "));
      table = null;
      tableAlign = null;
      // fall through to normal parsing of current line
    } else if (table && tableAlign) {
      const row = parseTableRow(line);
      if (row) {
        table.push(row);
        continue;
      }
      // End of table
      flushTable();
      // fall through to parse current line
    }

    const h = line.match(/^(#{1,3})\s+(.*)$/);
    if (h) {
      flushPara();
      flushList();
      flushTable();
      const lvl = h[1].length;
      out.push(`<h${lvl}>${renderInlineMarkdown(h[2])}</h${lvl}>`);
      continue;
    }

    const li = line.match(/^\s*[-*]\s+(.*)$/);
    if (li) {
      flushPara();
      flushTable();
      list.push(li[1]);
      continue;
    }

    const quote = line.match(/^\s*>\s?(.*)$/);
    if (quote) {
      flushPara();
      flushList();
      flushTable();
      const html = renderInlineMarkdown(quote[1]);
      out.push(`<blockquote>${html}</blockquote>`);
      continue;
    }

    if (!line.trim()) {
      flushPara();
      flushList();
      flushTable();
      continue;
    }

    para.push(line);
  }

  if (inCode) flushCode();
  flushTable();
  flushPara();
  flushList();
  return out.join("\n");
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
  contentEl.className = "content" + (role === "assistant" ? " md" : "");
  if (role === "assistant") {
    contentEl.innerHTML = renderMarkdown(content || "");
  } else {
    contentEl.textContent = content || "";
  }

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

function appendStreamingAssistant() {
  const chat = qs("chat");
  const row = document.createElement("div");
  row.className = "msg";

  const roleEl = document.createElement("div");
  roleEl.className = "role";
  roleEl.textContent = "助手";

  const bubble = document.createElement("div");
  bubble.className = "bubble assistant";

  const contentEl = document.createElement("div");
  contentEl.className = "content md";
  contentEl.textContent = "";
  bubble.appendChild(contentEl);

  row.appendChild(roleEl);
  row.appendChild(bubble);
  chat.appendChild(row);
  chat.scrollTop = chat.scrollHeight;

  return {
    setContent: (text, mode) => {
      const t = text || "";
      if (mode === "md") contentEl.innerHTML = renderMarkdown(t);
      else contentEl.textContent = t;
      chat.scrollTop = chat.scrollHeight;
    },
    setMeta: (meta) => {
      if (!meta) return;
      // Reuse render logic by removing and re-appending.
      bubble.querySelectorAll(".meta").forEach((n) => n.remove());
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
          const url = e.url
            ? `<a href="${escapeHtml(e.url)}" target="_blank" rel="noreferrer">${escapeHtml(e.url)}</a>`
            : "-";
          item.innerHTML = `<div>${escapeHtml(e.source_id || "-")} · ${escapeHtml(e.published_at || "-")} · ${url}</div>`;
          metaEl.appendChild(item);
        }
      }

      bubble.appendChild(metaEl);
      chat.scrollTop = chat.scrollHeight;
    },
  };
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

function parseSseBlock(block) {
  const lines = block.split("\n");
  let event = "message";
  const dataLines = [];
  for (const line of lines) {
    if (line.startsWith("event:")) event = line.slice("event:".length).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice("data:".length).trim());
  }
  return { event, data: dataLines.join("\n") };
}

async function chatStream(payload, handlers) {
  const traceId = Math.random().toString(16).slice(2, 10);
  const t0 = performance.now();
  console.debug(`[txnews] chatStream start trace=${traceId}`, {
    msgs: (payload && payload.messages && payload.messages.length) || 0,
  });
  const res = await fetch("/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text();
    console.warn(`[txnews] chatStream http_error trace=${traceId}`, res.status, text);
    throw new Error(text || `HTTP ${res.status}`);
  }
  if (!res.body) throw new Error("stream not supported");

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  let deltaChars = 0;
  let firstDeltaMs = null;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    while (true) {
      const idx = buf.indexOf("\n\n");
      if (idx < 0) break;
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const { event, data } = parseSseBlock(block);
      if (!data) continue;
      let obj = null;
      try {
        obj = JSON.parse(data);
      } catch {
        obj = { raw: data };
      }
      if (event === "delta" && obj && obj.content) {
        deltaChars += String(obj.content).length;
        if (firstDeltaMs === null) firstDeltaMs = performance.now() - t0;
      }
      if (event === "tool") console.debug(`[txnews] chatStream tool trace=${traceId}`, obj);
      if (event === "done") {
        console.debug(`[txnews] chatStream done trace=${traceId}`, {
          elapsed_ms: Math.round(performance.now() - t0),
          first_delta_ms: firstDeltaMs ? Math.round(firstDeltaMs) : null,
          delta_chars: deltaChars,
        });
      }
      if (handlers && handlers[event]) {
        await handlers[event](obj);
      }
    }
  }
  console.debug(`[txnews] chatStream end trace=${traceId}`, {
    elapsed_ms: Math.round(performance.now() - t0),
    first_delta_ms: firstDeltaMs ? Math.round(firstDeltaMs) : null,
    delta_chars: deltaChars,
  });
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
    const ui = appendStreamingAssistant();
    let acc = "";
    let finalMsg = null;

    const warnTimer = setTimeout(() => {
      console.warn("[txnews] chatStream still waiting for first token (>15s)");
    }, 15000);

    await chatStream(payload, {
      ready: async () => {},
      delta: async (d) => {
        const chunk = (d && d.content) || "";
        if (!chunk) return;
        clearTimeout(warnTimer);
        acc += chunk;
        // During streaming, keep it plain text to avoid heavy markdown re-render on each token.
        ui.setContent(acc, "text");
      },
      tool: async () => {},
      done: async (m) => {
        clearTimeout(warnTimer);
        finalMsg = m;
      },
    });

    const assistant = {
      role: "assistant",
      content: (finalMsg && finalMsg.content) || acc || "",
      ts: nowIso(),
      meta: (finalMsg && finalMsg.meta) || null,
    };
    ui.setContent(assistant.content, "md");
    ui.setMeta(assistant.meta);
    saveMessages([...next, assistant]);
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
