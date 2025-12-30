<!-- Input: 用户对话 + /chat/stream SSE（delta/tool/tool_result/done） -->
<!-- Output: 对话 UI（含工具调用实时进度与证据展示） -->
<!-- Pos: 前端对话页（变更时同步更新以上注释与所属目录 FOLDER.md） -->

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
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
    created_at: string
}

const messages = ref<Message[]>([])
const input = ref('')
const isLoading = ref(false)
const chatContainer = ref<HTMLElement | null>(null)

// Side panel stats
const status = ref({ ok: false, text: '连接中…', class: 'dot dot-warn' })
const counts = ref({ articles: '-', analyses: '-', signals: '-', a_share: '-' })
const signals = ref<Signal[]>([])

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
    const res = await fetch(path)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
}

async function refreshStatus() {
    try {
        const st = await api("/admin/status")
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
    }
}

async function refreshSignals() {
    try {
        signals.value = await api("/signals?limit=30")
    } catch {
        signals.value = [] // or error indicator
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
        meta: { tool_progress: [] as ToolProgressItem[] }
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
                    assistantMsg.value.meta = { ...(assistantMsg.value.meta || {}), tool_progress: tp }
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
                    assistantMsg.value.meta = {
                        ...(data.message.meta || {}),
                        tool_progress: (assistantMsg.value.meta?.tool_progress || []) as ToolProgressItem[]
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
    } finally {
        isLoading.value = false
    }
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

function fmtMs(ms?: number): string {
    if (ms == null) return ""
    const x = Number(ms)
    if (!Number.isFinite(x)) return ""
    if (x < 1000) return `${Math.round(x)}ms`
    return `${(x / 1000).toFixed(2)}s`
}
</script>

<template>
  <section class="panel" style="grid-column: 1 / span 1">
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
                <div class="pill">工具进度</div>
                <div class="tool-progress">
                    <div v-for="t in msg.meta.tool_progress" :key="t.id" class="tool-step">
                        <div class="tool-head">
                            <div class="tool-left">
                                <span class="tool-dot" :class="`tool-${t.status}`"></span>
                                <code class="tool-name">{{ t.name }}({{ fmtToolArgs(t.arguments) }})</code>
                            </div>
                            <div class="tool-right mono">
                                <span v-if="t.status === 'running'" class="muted">运行中…</span>
                                <span v-else-if="t.status === 'done'" class="muted">{{ fmtMs(t.duration_ms) }}</span>
                                <span v-else class="muted">失败</span>
                            </div>
                        </div>
                        <div v-if="t.summary && (t.summary.items != null || t.summary.error)" class="tool-sub muted">
                            <span v-if="t.summary.items != null">items={{ t.summary.items }}</span>
                            <span v-else-if="t.summary.error">error={{ t.summary.error }}</span>
                        </div>
                    </div>
                </div>
             </div>
             <div v-if="msg.meta.tools && msg.meta.tools.length">
                <div class="pill">工具调用</div>
                <div v-for="t in msg.meta.tools" :key="t.name">
                    <code>{{ t.name }}({{ JSON.stringify(t.arguments || {}) }})</code>
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
        @keydown.enter.exact.prevent="sendMessage"
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

  <aside class="panel side">
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
        <div class="k">articles</div>
        <div class="v">{{ counts.articles }}</div>
        <div class="k">analyses</div>
        <div class="v">{{ counts.analyses }}</div>
        <div class="k">signals</div>
        <div class="v">{{ counts.signals }}</div>
        <div class="k">a_share</div>
        <div class="v">{{ counts.a_share }}</div>
      </div>
      <div class="list">
        <div v-for="s in signals" :key="s.canonical_id" class="card">
            <div class="title">{{ s.kind }} · {{ s.created_at }}</div>
            <div class="small">canonical_id: <code>{{ s.canonical_id }}</code></div>
        </div>
      </div>
    </div>
  </aside>
</template>
