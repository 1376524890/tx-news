<!-- Input: 用户对话 + /chat/stream SSE（delta/tool/tool_result/done）+ /signals -->
<!-- Output: 对话 UI（回车发送/Shift+Enter 换行；含工具进度与最新信号侧栏） -->
<!-- Pos: 前端对话页（变更时同步更新以上注释与所属目录 FOLDER.md） -->

<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, nextTick } from 'vue'
import { marked } from 'marked'
// Actually the original app.js had a custom markdown renderer. I'll use `marked` for better support.

const STORAGE_KEY = "txnews_chat_v1_vue"

interface Message {
    role: 'user' | 'assistant'
    content: string
    ts?: string
    meta?: any
}

interface ToolProgressItem {
    id: string
    name: string
    arguments?: any
    status: 'running' | 'done' | 'error'
    started_at: string
    duration_ms?: number
    summary?: any
    error?: string
}

interface Signal {
    canonical_id: string
    kind: string
    created_at: string | null
    title?: string | null
    url?: string | null
    published_at?: string | null
    event_type?: string | null
    tickers?: Array<{ ts_code?: string }> | null
    deep_optimized_at?: string | null
    summary?: string | null
}

const messages = ref<Message[]>([])
const input = ref('')
const isLoading = ref(false)
const chatContainer = ref<HTMLElement | null>(null)
const isComposing = ref(false)

// Side panel stats
const status = ref({ ok: false, text: '连接中…', class: 'dot dot-warn' })
const counts = ref({ articles: '-', analyses: '-', signals: '-', a_share: '-' })
const lat = ref({ status_ms: '-', signals_ms: '-' })
const signals = ref<Signal[]>([])
const latWin = { status: [] as number[], signals: [] as number[] }
const SIGNALS_FETCH_LIMIT = 50
const SIGNALS_VIEW_LIMIT = 12

const signalsView = computed(() => {
    const xs = (signals.value || [])
        .slice()
        .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))
    return xs.slice(0, SIGNALS_VIEW_LIMIT)
})

function fmtTs(iso?: string | null): string {
    if (!iso) return '-'
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return String(iso)
    return d.toLocaleString()
}

function tickersText(s: Signal): string {
    const xs = (s.tickers || [])
        .map((x) => (x && typeof x.ts_code === 'string' ? x.ts_code.trim() : ''))
        .filter((x) => x.length > 0)
        .slice(0, 4)
    return xs.length ? xs.join(', ') : ''
}

// Load history
onMounted(() => {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
        try {
            messages.value = JSON.parse(raw)
        } catch {
            messages.value = []
        }
    }
    if (messages.value.length === 0) {
        messages.value.push({
            role: 'assistant',
            content: "我可以帮你基于本地数据库/知识库检索当天的高时效新闻，并给出结构化影响分析（不展示原文）。\n\n你可以问：\n- 今天有哪些突发影响A股？\n- 某个 ts_code（如 600519.SH）近期有什么事件？\n- 解释某条新闻可能的市场传导路径。"
        })
    }
    scrollToBottom()
    
    // Polling
    refreshStatus()
    refreshSignals()
    const timer1 = setInterval(refreshStatus, 8000)
    const timer2 = setInterval(refreshSignals, 15000)
    
    onUnmounted(() => {
        clearInterval(timer1)
        clearInterval(timer2)
    })
})

function saveHistory() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(messages.value.slice(-40)))
}

function scrollToBottom() {
    nextTick(() => {
        if (chatContainer.value) {
            chatContainer.value.scrollTop = chatContainer.value.scrollHeight
        }
    })
}

function clearHistory() {
    messages.value = []
    localStorage.removeItem(STORAGE_KEY)
}

// Markdown rendering
function renderMd(text: string) {
    return marked.parse(text || '')
}

async function api(path: string) {
    const res = await fetch(path, { cache: "no-store" })
    const text = await res.text()
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    try {
        return text ? JSON.parse(text) : null
    } catch {
        throw new Error("Invalid JSON response")
    }
}

async function refreshStatus() {
    const t0 = Date.now()
    try {
        const st = await api("/status")
        const deps = st.dependencies || {}
        const ok = Object.values(deps).every((x: any) => x && x.ok)
        status.value = {
            ok,
            text: ok ? "正常" : "依赖异常",
            class: ok ? "dot dot-ok" : "dot dot-warn"
        }
        counts.value = {
            articles: (st.counts && st.counts.articles) ?? "-",
            analyses: (st.counts && st.counts.analyses) ?? "-",
            signals: (st.counts && st.counts.signals) ?? "-",
            a_share: (st.counts && st.counts.a_share_basic) ?? "-"
        }
    } catch {
        status.value = { ok: false, text: "连接失败", class: "dot dot-bad" }
    } finally {
        recordLatency('status', Date.now() - t0)
    }
}

async function refreshSignals() {
    const t0 = Date.now()
    try {
        signals.value = await api(`/signals?limit=${SIGNALS_FETCH_LIMIT}`)
    } catch {
        signals.value = [] // or error indicator
    } finally {
        recordLatency('signals', Date.now() - t0)
    }
}

async function sendMessage() {
    const text = input.value.trim()
    if (!text || isLoading.value) return
    
    input.value = ''
    messages.value.push({ role: 'user', content: text, ts: new Date().toISOString() })
    saveHistory()
    scrollToBottom()
    
    isLoading.value = true
    
    // Prepare streaming assistant message
    const assistantMsg = ref<Message>({
        role: 'assistant',
        content: '',
        ts: new Date().toISOString(),
        meta: { tool_progress: [] as ToolProgressItem[], tool_progress_collapsed: false }
    })
    messages.value.push(assistantMsg.value)
    
    try {
        const payload = {
            messages: messages.value.slice(0, -1).map(m => ({ role: m.role, content: m.content })),
            recent_minutes: 180, // Default
            max_steps: 6
        }
        
        const res = await fetch("/chat/stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        })
        
        if (!res.ok || !res.body) throw new Error("Stream error")
        
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buf = ""
        
        while (true) {
            const { value, done } = await reader.read()
            if (done) break
            buf += decoder.decode(value, { stream: true })
            
            while (true) {
                const idx = buf.indexOf("\n\n")
                if (idx < 0) break
                const block = buf.slice(0, idx)
                buf = buf.slice(idx + 2)
                
                // Parse SSE
                const lines = block.split("\n")
                let event = "message"
                let dataStr = ""
                for (const line of lines) {
                    if (line.startsWith("event:")) event = line.slice(6).trim()
                    else if (line.startsWith("data:")) dataStr = line.slice(5).trim()
                }
                
                if (!dataStr) continue
                let data: any = {}
                try { data = JSON.parse(dataStr) } catch {}
                
                if (event === 'delta' && data.content) {
                    assistantMsg.value.content += data.content
                    scrollToBottom()
                } else if (event === 'tool' && data.name) {
                    const tp: ToolProgressItem[] = (assistantMsg.value.meta?.tool_progress || []) as ToolProgressItem[]
                    tp.push({
                        id: `${Date.now()}_${Math.random().toString(16).slice(2)}`,
                        name: String(data.name),
                        arguments: data.arguments,
                        status: 'running',
                        started_at: new Date().toISOString()
                    })
                    assistantMsg.value.meta = { ...(assistantMsg.value.meta || {}), tool_progress: tp, tool_progress_collapsed: false }
                    scrollToBottom()
                } else if (event === 'tool_result' && data.name) {
                    const tp: ToolProgressItem[] = (assistantMsg.value.meta?.tool_progress || []) as ToolProgressItem[]
                    for (let i = tp.length - 1; i >= 0; i--) {
                        const x = tp[i]!
                        if (x.name === data.name && x.status === 'running') {
                            x.status = data.ok ? 'done' : 'error'
                            x.duration_ms = data.duration_ms
                            x.summary = data.summary
                            if (!data.ok && data.summary?.error) x.error = String(data.summary.error)
                            break
                        }
                    }
                    assistantMsg.value.meta = { ...(assistantMsg.value.meta || {}), tool_progress: tp }
                    scrollToBottom()
                } else if (event === 'done' && data.message) {
                    assistantMsg.value.content = data.message.content || assistantMsg.value.content
                    const tp: ToolProgressItem[] = (assistantMsg.value.meta?.tool_progress || []) as ToolProgressItem[]
                    assistantMsg.value.meta = {
                        ...(data.message.meta || {}),
                        tool_progress: tp,
                        tool_progress_collapsed: tp.length > 0
                    }
                }
            }
        }
        saveHistory()
    } catch (e) {
        assistantMsg.value.content += `\n[Error: ${e}]`
        const tp: ToolProgressItem[] = (assistantMsg.value.meta?.tool_progress || []) as ToolProgressItem[]
        for (const x of tp) {
            if (x.status === 'running') x.status = 'error'
        }
        assistantMsg.value.meta = { ...(assistantMsg.value.meta || {}), tool_progress_collapsed: false }
    } finally {
        isLoading.value = false
    }
}

function onComposerKeydown(e: KeyboardEvent) {
    if (e.key !== 'Enter') return
    if (isComposing.value) return
    if (e.shiftKey || e.ctrlKey || e.altKey || e.metaKey) return
    e.preventDefault()
    sendMessage()
}

function fmtToolArgs(args: any): string {
    try {
        if (args == null) return ""
        const s = JSON.stringify(args)
        if (s.length <= 140) return s
        return s.slice(0, 140) + "…"
    } catch {
        return ""
    }
}

function fmtMinutes(minutes?: any): string {
    const n = Number(minutes)
    if (!Number.isFinite(n) || n <= 0) return ""
    if (n % 60 === 0) return `${n / 60} 小时`
    if (n > 60) return `${Math.floor(n / 60)} 小时 ${n % 60} 分钟`
    return `${n} 分钟`
}

function fmtMs(ms?: number): string {
    if (ms == null) return ""
    const x = Number(ms)
    if (!Number.isFinite(x)) return ""
    if (x < 1000) return `${Math.round(x)}ms`
    return `${(x / 1000).toFixed(2)}s`
}

function recordLatency(kind: 'status' | 'signals', ms: number) {
    if (!Number.isFinite(ms) || ms < 0) return
    const w = (kind === 'status' ? latWin.status : latWin.signals)
    w.push(ms)
    if (w.length > 30) w.shift()
    const avg = Math.round(w.reduce((a, b) => a + b, 0) / w.length)
    if (kind === 'status') lat.value.status_ms = `${avg}ms`
    else lat.value.signals_ms = `${avg}ms`
}

function toolLabel(t: ToolProgressItem): string {
    const name = String(t.name || '')
    const args = (t.arguments || {}) as any
    if (name === 'list_recent') {
        const minutes = fmtMinutes(args.minutes)
        const limit = args.limit != null ? Number(args.limit) : null
        const parts = []
        if (minutes) parts.push(`近 ${minutes}`)
        if (limit != null && Number.isFinite(limit)) parts.push(`最多 ${limit} 条`)
        return parts.length ? `检索近期新闻（${parts.join('，')}）` : '检索近期新闻'
    }
    if (name === 'search_news') {
        const q = typeof args.q === 'string' ? args.q.trim() : ''
        const limit = args.limit != null ? Number(args.limit) : null
        const parts = []
        if (q) parts.push(`关键词：${q}`)
        if (limit != null && Number.isFinite(limit)) parts.push(`最多 ${limit} 条`)
        return parts.length ? `搜索知识库（${parts.join('，')}）` : '搜索知识库'
    }
    if (name === 'get_article_analysis') {
        const cid = typeof args.canonical_id === 'string' ? args.canonical_id.trim() : ''
        return cid ? `读取文章分析（${cid}）` : '读取文章分析'
    }
    if (name === 'list_signals') {
        const limit = args.limit != null ? Number(args.limit) : null
        return Number.isFinite(limit) ? `读取最新信号（最多 ${limit} 条）` : '读取最新信号'
    }
    if (name === 'get_event_timeline') {
        const eventId = typeof args.event_id === 'string' ? args.event_id.trim() : ''
        const limit = args.limit != null ? Number(args.limit) : null
        const parts = []
        if (eventId) parts.push(eventId)
        if (limit != null && Number.isFinite(limit)) parts.push(`最多 ${limit} 条`)
        return parts.length ? `获取事件时间线（${parts.join('，')}）` : '获取事件时间线'
    }
    if (name === 'get_entity_profile') {
        const ts = typeof args.ts_code === 'string' ? args.ts_code.trim() : ''
        return ts ? `查询个股资料（${ts}）` : '查询个股资料'
    }
    const raw = fmtToolArgs(args)
    return raw ? `${name}（${raw}）` : name
}

function toolResultText(t: ToolProgressItem): string {
    const s = t.summary || {}
    if (s == null || typeof s !== 'object') return ''
    if (s.items != null) {
        const n = Number(s.items)
        return Number.isFinite(n) ? `返回 ${n} 条结果` : ''
    }
    if (s.error) {
        return `错误：${String(s.error)}`
    }
    return ''
}

function toolProgressSummary(items: ToolProgressItem[]): string {
    const xs = items || []
    const done = xs.filter(x => x.status === 'done').length
    const err = xs.filter(x => x.status === 'error').length
    const ms = xs.filter(x => typeof x.duration_ms === 'number' && x.status === 'done').map(x => Number(x.duration_ms))
    const avg = ms.length ? Math.round(ms.reduce((a, b) => a + b, 0) / ms.length) : null
    const parts = []
    parts.push(`已完成 ${done}/${xs.length}`)
    if (err) parts.push(`失败 ${err}`)
    if (avg != null) parts.push(`平均 ${avg}ms`)
    return parts.join(' · ')
}

function toggleToolProgress(msg: Message) {
    if (!msg.meta) msg.meta = {}
    const cur = Boolean(msg.meta.tool_progress_collapsed)
    msg.meta.tool_progress_collapsed = !cur
    saveHistory()
}
</script>

<template>
  <section class="panel chat-shell" style="grid-column: 1 / span 1">
    <div class="panel-header">
      <div class="panel-title">对话</div>
      <div class="panel-actions">
        <button class="btn btn-ghost" @click="clearHistory">清空</button>
      </div>
    </div>

    <div class="chat" ref="chatContainer">
      <div v-for="(msg, i) in messages" :key="i" class="msg">
        <div class="role">{{ msg.role === 'user' ? '你' : '助手' }}</div>
        <div class="bubble" :class="msg.role">
          <div v-if="msg.role === 'assistant'" class="content md" v-html="renderMd(msg.content)"></div>
          <div v-else class="content">{{ msg.content }}</div>
          
          <div v-if="msg.meta" class="meta">
             <div v-if="msg.meta.tool_progress && msg.meta.tool_progress.length">
                <div style="display:flex; align-items:center; gap:10px; margin-bottom:6px;">
                  <div class="pill">工具进度</div>
                  <div class="muted mono" style="flex:1; min-width:0;">{{ toolProgressSummary(msg.meta.tool_progress) }}</div>
                  <button
                    class="btn btn-ghost"
                    style="padding: 0 8px; height: 26px; min-height: 0;"
                    @click="toggleToolProgress(msg)"
                    type="button"
                  >
                    {{ msg.meta.tool_progress_collapsed ? '展开' : '收起' }}
                  </button>
                </div>
                <div v-if="!msg.meta.tool_progress_collapsed" class="tool-progress">
                    <div v-for="t in msg.meta.tool_progress" :key="t.id" class="tool-step">
                        <div class="tool-head">
                            <div class="tool-left">
                                <span class="tool-dot" :class="`tool-${t.status}`"></span>
                                <span class="tool-name">{{ toolLabel(t) }}</span>
                            </div>
                            <div class="tool-right mono">
                                <span v-if="t.status === 'running'" class="muted">运行中…</span>
                                <span v-else-if="t.status === 'done'" class="muted">耗时 {{ fmtMs(t.duration_ms) }}</span>
                                <span v-else class="muted">失败</span>
                            </div>
                        </div>
                        <div v-if="toolResultText(t)" class="tool-sub muted">{{ toolResultText(t) }}</div>
                    </div>
                </div>
             </div>
             <div v-if="msg.meta.tools && msg.meta.tools.length">
                <div class="pill">工具调用</div>
                <div v-for="t in msg.meta.tools" :key="t.name">
                    <span class="mono">{{ t.name }}</span>
                    <span class="muted">：{{ fmtToolArgs(t.arguments || {}) || "（无参数）" }}</span>
                </div>
             </div>
             <div v-if="msg.meta.evidence && msg.meta.evidence.length" style="margin-top:8px">
                <div class="pill">证据</div>
                <div v-for="e in msg.meta.evidence" :key="e.source_id">
                    <div>{{ e.source_id }} · {{ e.published_at }} · <a :href="e.url" target="_blank">{{ e.url }}</a></div>
                </div>
             </div>
          </div>
        </div>
      </div>
    </div>

    <form class="composer" @submit.prevent="sendMessage">
      <textarea
        v-model="input"
        class="input"
        rows="2"
        placeholder="输入你的问题（例如：今天有哪些影响A股的突发？某某公司发生了什么？）"
        @keydown="onComposerKeydown"
        @compositionstart="isComposing = true"
        @compositionend="isComposing = false"
        required
      ></textarea>
      <div class="composer-row">
        <div class="hint">
          不展示新闻全文；回答基于数据库与知识库检索结果。
        </div>
        <button class="btn" type="submit" :disabled="isLoading">发送</button>
      </div>
    </form>
  </section>

  <aside class="panel side side-shell">
    <div class="panel-header">
      <div class="panel-title">最新信号</div>
      <div class="panel-actions">
        <button class="btn btn-ghost" @click="refreshSignals">刷新</button>
      </div>
    </div>
    <div class="side-body">
        <div class="status" style="margin-bottom: 12px; justify-content: flex-end;">
            <span class="dot" :class="status.class"></span>
            <span class="status-text">{{ status.text }}</span>
        </div>
      <div class="kv">
        <div class="k">文章</div>
        <div class="v">{{ counts.articles }}</div>
        <div class="k">分析</div>
        <div class="v">{{ counts.analyses }}</div>
        <div class="k">信号</div>
        <div class="v">{{ counts.signals }}</div>
        <div class="k">个股</div>
        <div class="v">{{ counts.a_share }}</div>
        <div class="k">状态均耗时</div>
        <div class="v">{{ lat.status_ms }}</div>
        <div class="k">信号均耗时</div>
        <div class="v">{{ lat.signals_ms }}</div>
      </div>
      <div class="list">
        <div v-for="s in signalsView" :key="s.kind + '|' + s.canonical_id + '|' + (s.created_at || '')" class="card">
            <div class="title">
              <span>{{ s.summary || s.kind }}</span>
              <span class="muted" style="margin-left: 8px;">· {{ fmtTs(s.created_at) }}</span>
            </div>
            <div v-if="s.title || s.url" class="small" style="margin-top: 4px;">
              <a v-if="s.url" :href="s.url" target="_blank" rel="noreferrer">{{ s.title || s.url }}</a>
              <span v-else>{{ s.title }}</span>
            </div>
            <div class="small muted" style="margin-top: 4px;">
              <span v-if="s.event_type">类型：{{ s.event_type }}</span>
              <span v-if="tickersText(s)" style="margin-left: 10px;">个股：{{ tickersText(s) }}</span>
              <span v-if="s.deep_optimized_at" style="margin-left: 10px;">deep ✓</span>
            </div>
        </div>
        <div v-if="(signals || []).length > signalsView.length" class="small muted" style="margin-top: 8px;">
          已显示最新 {{ signalsView.length }} 条（共 {{ (signals || []).length }} 条）。
        </div>
      </div>
    </div>
  </aside>
</template>
