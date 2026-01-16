<!-- Input: /kg/graph（轮询去重）+ 用户交互（点击/悬浮/反馈） -->
<!-- Output: 2D 平面知识图谱可视化（实时刷新；避免乱序覆盖） -->
<!-- Pos: 图谱组件（变更时同步更新以上注释与所属目录 FOLDER.md） -->

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

type KgNodeBase = {
  id: string
  kind: 'event' | 'ticker'
  label: string
  count?: number
}

type KgEventNode = KgNodeBase & {
  kind: 'event'
  event_id: string
  event_type?: string | null
  last_created_at?: string | null
  articles?: Array<{
    canonical_id: string
    title: string | null
    url: string | null
    source_id: string | null
    published_at: string | null
    created_at: string | null
  }>
}

type KgTickerNode = KgNodeBase & {
  kind: 'ticker'
  ts_code: string
  name?: string | null
}

type KgNode = KgEventNode | KgTickerNode

type KgLink = {
  source: string
  target: string
  relation: string
  weight?: number
  evidence?: string[]
}

type KgGraph = {
  window_minutes: number
  generated_at: string
  nodes: KgNode[]
  links: KgLink[]
  stats?: { events?: number; tickers?: number; links?: number }
}

type LayoutNode = {
  id: string
  kind: KgNode['kind']
  label: string
  count: number
  x: number
  y: number
  r: number
  color: string
}

type LayoutEdge = {
  key: string
  link: KgLink
  x1: number
  y1: number
  x2: number
  y2: number
  width: number
  color: string
}

type LayoutState = {
  nodes: LayoutNode[]
  edges: LayoutEdge[]
}

type HoverState = {
  kind: 'node' | 'edge'
  id: string
  title: string
  lines: string[]
  x: number
  y: number
}

const props = defineProps<{
  minutes: number
  pollMs?: number
}>()

const pollMs = computed(() => Math.max(1500, Number(props.pollMs ?? 5000)))
const container = ref<HTMLDivElement | null>(null)
const graph = ref<KgGraph | null>(null)
const error = ref<string | null>(null)
const selected = ref<KgNode | null>(null)
const selectedEdge = ref<KgLink | null>(null)
const hover = ref<HoverState | null>(null)
const layout = ref<LayoutState>({ nodes: [], edges: [] })
const size = ref({ width: 1, height: 1 })
let refreshInFlight = false
let refreshQueued = false

const nodeById = computed(() => {
  const m = new Map<string, KgNode>()
  for (const n of graph.value?.nodes || []) m.set(n.id, n)
  return m
})

const canonicalById = computed(() => {
  const m = new Map<string, { canonical_id: string; title: string | null; url: string | null }>()
  for (const n of graph.value?.nodes || []) {
    if (n.kind !== 'event') continue
    for (const a of (n.articles || []) as any[]) {
      const cid = String(a?.canonical_id || '').trim()
      if (!cid || m.has(cid)) continue
      m.set(cid, { canonical_id: cid, title: a?.title ?? null, url: a?.url ?? null })
    }
  }
  return m
})

const selectedEdgeInfo = computed(() => {
  const l = selectedEdge.value
  if (!l) return null
  const src = nodeById.value.get(String(l.source)) || null
  const tgt = nodeById.value.get(String(l.target)) || null
  const evidence = (l.evidence || []).map((cid) => canonicalById.value.get(String(cid)) || { canonical_id: cid, title: null, url: null })
  return { link: l, source: src, target: tgt, evidence }
})

const selectedNodeId = computed(() => selected.value?.id || null)
const selectedEdgeKey = computed(() => (selectedEdge.value ? edgeKey(selectedEdge.value) : null))
const hoveredNodeId = computed(() => (hover.value?.kind === 'node' ? hover.value.id : null))
const hoveredEdgeKey = computed(() => (hover.value?.kind === 'edge' ? hover.value.id : null))

async function api<T>(path: string): Promise<T> {
  const res = await fetch(path, { cache: 'no-store' })
  const text = await res.text()
  let data: any = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { raw: text }
  }
  if (!res.ok) throw new Error((data && (data.error || data.detail)) || `HTTP ${res.status}`)
  return data as T
}

async function sendFeedback(kind: string, data: Record<string, any>) {
  try {
    await fetch('/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind, data })
    })
  } catch {
    // Best-effort only.
  }
}

async function refresh() {
  if (refreshInFlight) {
    refreshQueued = true
    return
  }
  refreshInFlight = true
  try {
    error.value = null
    graph.value = await api<KgGraph>(`/kg/graph?minutes=${encodeURIComponent(props.minutes)}`)
  } catch (e: any) {
    error.value = String(e?.message || e)
  } finally {
    refreshInFlight = false
    if (refreshQueued) {
      refreshQueued = false
      void refresh()
    }
  }
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v))
}

function hash01(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619)
  return ((h >>> 0) % 1000000) / 1000000
}

function nodeLabel(n: KgNode): string {
  if (n.kind === 'event') return String(n.event_type || n.label || n.event_id || n.id).trim() || n.id
  return String(n.ts_code || n.label || n.id).trim() || n.id
}

function relationZh(relation: string): string {
  const r = String(relation || '').trim().toLowerCase()
  if (r === 'mentions') return '提及'
  return relation || '-'
}

function relationColor(relation: string): string {
  const r = String(relation || '').trim().toLowerCase()
  const mapped: Record<string, string> = {
    mentions: '#60a5fa',
    related: '#f59e0b',
    co_occurs: '#f472b6',
    causes: '#22c55e'
  }
  if (mapped[r]) return mapped[r]
  const palette = ['#94a3b8', '#c084fc', '#38bdf8', '#facc15', '#34d399', '#fb7185']
  const idx = Math.floor(hash01(r) * palette.length) % palette.length
  return palette[idx] || '#94a3b8'
}

function edgeKey(l: KgLink): string {
  return `${l.source}|${l.target}|${l.relation}`
}

function layoutGraph(g: KgGraph | null, width: number, height: number): LayoutState {
  if (!g || width < 10 || height < 10) return { nodes: [], edges: [] }

  const nodes = g.nodes || []
  const links = g.links || []
  const w = Math.max(1, width)
  const h = Math.max(1, height)
  const cx = w / 2
  const cy = h / 2
  const padding = 70
  const seedRadius = Math.min(w, h) * 0.33

  const events = nodes.filter((n) => n.kind === 'event') as KgEventNode[]
  const tickers = nodes.filter((n) => n.kind === 'ticker') as KgTickerNode[]

  events.sort((a, b) => String(a.event_id).localeCompare(String(b.event_id)))
  tickers.sort((a, b) => String(a.ts_code).localeCompare(String(b.ts_code)))

  const positions = new Map<string, { x: number; y: number }>()
  const kindById = new Map<string, KgNode['kind']>()
  for (const n of nodes) kindById.set(n.id, n.kind)

  const eventCount = Math.max(1, events.length)
  for (let i = 0; i < events.length; i++) {
    const e = events[i]!
    const angle = (i / eventCount) * Math.PI * 2 + hash01(`event:${e.id}`) * 0.3
    const r = seedRadius * (0.92 + hash01(`event:r:${e.id}`) * 0.12)
    const x = cx + Math.cos(angle) * r
    const y = cy + Math.sin(angle) * r
    positions.set(e.id, { x, y })
  }

  const tickerEvents = new Map<string, string[]>()
  for (const l of links) {
    const s = String(l.source)
    const t = String(l.target)
    const sKind = kindById.get(s)
    const tKind = kindById.get(t)
    if (sKind === 'event' && tKind === 'ticker') {
      const arr = tickerEvents.get(t) || []
      arr.push(s)
      tickerEvents.set(t, arr)
      continue
    }
    if (tKind === 'event' && sKind === 'ticker') {
      const arr = tickerEvents.get(s) || []
      arr.push(t)
      tickerEvents.set(s, arr)
    }
  }

  for (const t of tickers) {
    const related = tickerEvents.get(t.id) || []
    let x = cx
    let y = cy
    if (related.length) {
      let n = 0
      for (const eid of related) {
        const pos = positions.get(eid)
        if (!pos) continue
        x += pos.x
        y += pos.y
        n += 1
      }
      if (n > 0) {
        x /= n
        y /= n
      }
    } else {
      const angle = hash01(`ticker:angle:${t.id}`) * Math.PI * 2
      const r = seedRadius * 0.62
      x = cx + Math.cos(angle) * r
      y = cy + Math.sin(angle) * r
    }

    const jitterAngle = hash01(`ticker:j:${t.id}`) * Math.PI * 2
    const jitterRadius = 16 + hash01(`ticker:r:${t.id}`) * 28
    x += Math.cos(jitterAngle) * jitterRadius
    y += Math.sin(jitterAngle) * jitterRadius

    positions.set(t.id, {
      x: clamp(x, padding, w - padding),
      y: clamp(y, padding, h - padding)
    })
  }

  const fallbackRadius = Math.min(w, h) * 0.18
  for (const n of nodes) {
    if (positions.has(n.id)) continue
    const angle = hash01(`fallback:${n.id}`) * Math.PI * 2
    const r = fallbackRadius + hash01(`fallback:r:${n.id}`) * 18
    positions.set(n.id, {
      x: clamp(cx + Math.cos(angle) * r, padding, w - padding),
      y: clamp(cy + Math.sin(angle) * r, padding, h - padding)
    })
  }

  const eventColor = '#a78bfa'
  const tickerColor = '#34d399'
  const otherColor = '#94a3b8'

  const simNodes = nodes.map((n) => {
    const pos = positions.get(n.id) || { x: cx, y: cy }
    const count = Math.max(1, Number(n.count || 1))
    const base = n.kind === 'event' ? 7.5 : 5.5
    const gain = n.kind === 'event' ? 1.8 : 1.2
    const r = clamp(base + Math.sqrt(count) * gain, n.kind === 'event' ? 7 : 4.5, n.kind === 'event' ? 20 : 14)
    return {
      id: n.id,
      kind: n.kind,
      label: nodeLabel(n),
      count,
      r,
      color: n.kind === 'event' ? eventColor : n.kind === 'ticker' ? tickerColor : otherColor,
      x: pos.x,
      y: pos.y,
      vx: 0,
      vy: 0
    }
  })

  const simById = new Map<string, (typeof simNodes)[number]>()
  for (const n of simNodes) simById.set(n.id, n)

  const iterations = Math.min(280, 140 + nodes.length * 4)
  let alpha = 1
  const alphaDecay = 0.02
  const damping = 0.86
  const chargeStrength = 2200
  const centerStrength = 0.08
  const clusterStrength = 0.12
  const linkStrength = 0.08
  const collisionPadding = 8

  const linkDistance = (l: KgLink, a: (typeof simNodes)[number], b: (typeof simNodes)[number]) => {
    const same = a.kind === b.kind
    const base = same ? 120 : 240
    const weight = Math.max(1, Number(l.weight || 1))
    const scaled = base / (1 + Math.log1p(weight) * 0.28)
    return clamp(scaled, same ? 80 : 140, same ? 170 : 280)
  }

  for (let step = 0; step < iterations; step++) {
    alpha *= 1 - alphaDecay
    const centers = new Map<string, { x: number; y: number; n: number }>()
    for (const n of simNodes) {
      const key = n.kind
      const c = centers.get(key) || { x: 0, y: 0, n: 0 }
      c.x += n.x
      c.y += n.y
      c.n += 1
      centers.set(key, c)
    }
    for (const c of centers.values()) {
      c.x /= Math.max(1, c.n)
      c.y /= Math.max(1, c.n)
    }

    for (let i = 0; i < simNodes.length; i++) {
      const a = simNodes[i]!
      for (let j = i + 1; j < simNodes.length; j++) {
        const b = simNodes[j]!
        const dx = b.x - a.x
        const dy = b.y - a.y
        const dist2 = dx * dx + dy * dy + 0.01
        const dist = Math.sqrt(dist2)
        const force = (chargeStrength * alpha) / dist2
        const fx = (dx / dist) * force
        const fy = (dy / dist) * force
        a.vx -= fx
        a.vy -= fy
        b.vx += fx
        b.vy += fy

        const minDist = a.r + b.r + collisionPadding
        if (dist < minDist) {
          const push = (minDist - dist) * 0.55
          const px = (dx / dist) * push
          const py = (dy / dist) * push
          a.vx -= px
          a.vy -= py
          b.vx += px
          b.vy += py
        }
      }
    }

    for (const l of links) {
      const s = simById.get(String(l.source))
      const t = simById.get(String(l.target))
      if (!s || !t) continue
      const dx = t.x - s.x
      const dy = t.y - s.y
      const dist = Math.sqrt(dx * dx + dy * dy) || 1
      const desired = linkDistance(l, s, t)
      const diff = dist - desired
      const strength = linkStrength * alpha
      const fx = (dx / dist) * diff * strength
      const fy = (dy / dist) * diff * strength
      s.vx += fx
      s.vy += fy
      t.vx -= fx
      t.vy -= fy
    }

    for (const n of simNodes) {
      const c = centers.get(n.kind)
      if (c) {
        n.vx += (c.x - n.x) * clusterStrength * alpha
        n.vy += (c.y - n.y) * clusterStrength * alpha
      }
      n.vx += (cx - n.x) * centerStrength * alpha
      n.vy += (cy - n.y) * centerStrength * alpha
      n.vx *= damping
      n.vy *= damping
      n.x += n.vx
      n.y += n.vy
      n.x = clamp(n.x, padding, w - padding)
      n.y = clamp(n.y, padding, h - padding)
    }
  }

  let minX = Number.POSITIVE_INFINITY
  let maxX = Number.NEGATIVE_INFINITY
  let minY = Number.POSITIVE_INFINITY
  let maxY = Number.NEGATIVE_INFINITY
  for (const n of simNodes) {
    minX = Math.min(minX, n.x)
    maxX = Math.max(maxX, n.x)
    minY = Math.min(minY, n.y)
    maxY = Math.max(maxY, n.y)
  }
  const spanX = Math.max(1, maxX - minX)
  const spanY = Math.max(1, maxY - minY)
  let scale = Math.min((w - 2 * padding) / spanX, (h - 2 * padding) / spanY)
  if (!Number.isFinite(scale) || scale <= 0) scale = 1

  for (const n of simNodes) {
    n.x = padding + (n.x - minX) * scale
    n.y = padding + (n.y - minY) * scale
  }

  const nodeLayouts: LayoutNode[] = simNodes.map((n) => ({
    id: n.id,
    kind: n.kind,
    label: n.label,
    count: n.count,
    x: n.x,
    y: n.y,
    r: n.r,
    color: n.color
  }))

  const edgeLayouts: LayoutEdge[] = []
  for (const l of links) {
    const s = simById.get(String(l.source))
    const t = simById.get(String(l.target))
    if (!s || !t) continue
    const weight = Math.max(1, Number(l.weight || 1))
    const width = clamp(1 + Math.sqrt(weight) * 1.2, 0.8, 6)
    edgeLayouts.push({
      key: edgeKey(l),
      link: l,
      x1: s.x,
      y1: s.y,
      x2: t.x,
      y2: t.y,
      width,
      color: relationColor(l.relation)
    })
  }

  return { nodes: nodeLayouts, edges: edgeLayouts }
}

function rebuildLayout() {
  layout.value = layoutGraph(graph.value, size.value.width, size.value.height)
}

function edgeOpacity(edge: LayoutEdge): number {
  if (selectedEdgeKey.value && edge.key === selectedEdgeKey.value) return 1
  if (selectedNodeId.value) {
    const isIncident = edge.link.source === selectedNodeId.value || edge.link.target === selectedNodeId.value
    return isIncident ? 0.85 : 0.2
  }
  if (hoveredEdgeKey.value && edge.key === hoveredEdgeKey.value) return 1
  return 0.7
}

function edgeWidth(edge: LayoutEdge): number {
  let w = edge.width
  if (selectedEdgeKey.value && edge.key === selectedEdgeKey.value) w *= 1.6
  else if (selectedNodeId.value) {
    const isIncident = edge.link.source === selectedNodeId.value || edge.link.target === selectedNodeId.value
    if (!isIncident) w *= 0.6
  }
  if (hoveredEdgeKey.value && edge.key === hoveredEdgeKey.value) w *= 1.25
  return w
}

function nodeOpacity(node: LayoutNode): number {
  if (selectedNodeId.value && node.id !== selectedNodeId.value) return 0.35
  if (selectedEdgeKey.value) {
    const match = layout.value.edges.some(
      (e) => e.key === selectedEdgeKey.value && (e.link.source === node.id || e.link.target === node.id)
    )
    return match ? 1 : 0.4
  }
  return 1
}

function nodeRadius(node: LayoutNode): number {
  let r = node.r
  if (selectedNodeId.value && node.id === selectedNodeId.value) r *= 1.25
  if (hoveredNodeId.value && node.id === hoveredNodeId.value) r *= 1.12
  return r
}

function updateSize() {
  if (!container.value) return
  const rect = container.value.getBoundingClientRect()
  size.value = { width: Math.max(1, Math.floor(rect.width)), height: Math.max(1, Math.floor(rect.height)) }
}

function eventPos(ev: MouseEvent): { x: number; y: number } {
  const rect = container.value?.getBoundingClientRect()
  if (!rect) return { x: 0, y: 0 }
  return { x: ev.clientX - rect.left, y: ev.clientY - rect.top }
}

function hoverNode(node: LayoutNode, ev: MouseEvent) {
  const info = nodeById.value.get(node.id) || null
  const lines: string[] = []
  lines.push(info?.kind === 'event' ? '类型：事件' : '类型：个股')
  if (info?.kind === 'event' && info.event_type && info.event_type !== node.label) {
    lines.push(`事件：${info.event_type}`)
  }
  if (info?.kind === 'ticker') {
    const name = (info as KgTickerNode).name
    const code = (info as KgTickerNode).ts_code
    if (code) lines.push(`代码：${code}`)
    if (name) lines.push(`名称：${name}`)
  }
  if (info?.count != null) lines.push(`频次：${info.count}`)
  const pos = eventPos(ev)
  hover.value = { kind: 'node', id: node.id, title: node.label || node.id, lines, x: pos.x, y: pos.y }
}

function hoverEdge(edge: LayoutEdge, ev: MouseEvent) {
  const link = edge.link
  const src = nodeById.value.get(String(link.source))
  const tgt = nodeById.value.get(String(link.target))
  const title = `关系：${relationZh(link.relation)}`
  const lines: string[] = []
  const srcLabel = src ? nodeLabel(src) : String(link.source)
  const tgtLabel = tgt ? nodeLabel(tgt) : String(link.target)
  lines.push(`${srcLabel} → ${tgtLabel}`)
  if (link.weight != null) lines.push(`权重：${link.weight}`)
  if ((link.evidence || []).length) lines.push(`证据：${(link.evidence || []).length}`)
  const pos = eventPos(ev)
  hover.value = { kind: 'edge', id: edge.key, title, lines, x: pos.x, y: pos.y }
}

function moveHover(ev: MouseEvent) {
  if (!hover.value) return
  const pos = eventPos(ev)
  hover.value = { ...hover.value, x: pos.x, y: pos.y }
}

function clearHover() {
  hover.value = null
}

function selectNode(node: LayoutNode) {
  const full = nodeById.value.get(node.id) || null
  if (!full) return
  selected.value = full
  selectedEdge.value = null
  sendFeedback('kg_graph_node_click', { minutes: props.minutes, node_id: full.id, kind: full.kind })
}

function selectEdge(edge: LayoutEdge) {
  const link = edge.link
  selectedEdge.value = link
  selected.value = null
  sendFeedback('kg_graph_edge_click', {
    minutes: props.minutes,
    relation: link.relation,
    source: link.source,
    target: link.target,
    weight: link.weight
  })
}

function onThumb(verdict: 'up' | 'down') {
  const node = selected.value
  if (!node) return
  sendFeedback(verdict === 'up' ? 'kg_graph_thumb_up' : 'kg_graph_thumb_down', {
    minutes: props.minutes,
    node_id: node.id,
    kind: node.kind
  })
}

function onArticleThumb(
  verdict: 'up' | 'down',
  ctx: {
    source: 'event' | 'edge'
    canonical_id: string
    url?: string | null
    node_id?: string
    relation?: string
    edge_source?: string
    edge_target?: string
  }
) {
  sendFeedback(verdict === 'up' ? 'kg_graph_article_thumb_up' : 'kg_graph_article_thumb_down', {
    minutes: props.minutes,
    ...ctx
  })
}

let timer: number | null = null
let resizeObs: ResizeObserver | null = null

onMounted(() => {
  updateSize()
  if (container.value) {
    resizeObs = new ResizeObserver(() => updateSize())
    resizeObs.observe(container.value)
  }
  refresh()
  timer = window.setInterval(refresh, pollMs.value)
})

onUnmounted(() => {
  if (timer != null) window.clearInterval(timer)
  resizeObs?.disconnect()
})

watch(
  () => [graph.value, size.value.width, size.value.height],
  () => rebuildLayout()
)

watch(
  () => props.minutes,
  () => refresh()
)

watch(
  () => pollMs.value,
  (ms) => {
    if (timer != null) window.clearInterval(timer)
    timer = window.setInterval(refresh, ms)
  }
)
</script>

<template>
  <div class="kg2d">
    <div class="kg2d-head">
      <div class="title">知识图谱（2D）</div>
      <div class="meta mono">
        <span>{{ graph?.stats?.events ?? '-' }} 事件</span>
        <span>·</span>
        <span>{{ graph?.stats?.tickers ?? '-' }} 个股</span>
        <span>·</span>
        <span>{{ graph?.stats?.links ?? '-' }} 边</span>
        <span v-if="graph?.generated_at">·</span>
        <span v-if="graph?.generated_at" class="muted">更新于 {{ graph?.generated_at }}</span>
      </div>
      <div class="actions">
        <button class="btn btn-ghost" @click="refresh">刷新</button>
      </div>
    </div>

    <div v-if="error" class="kg2d-error">{{ error }}</div>

    <div class="kg2d-body">
      <div class="kg2d-canvas" ref="container">
        <svg
          class="kg2d-svg"
          :width="size.width"
          :height="size.height"
          :viewBox="`0 0 ${size.width} ${size.height}`"
          preserveAspectRatio="xMidYMid meet"
          @mousemove="moveHover"
        >
          <g class="kg2d-edges">
            <line
              v-for="edge in layout.edges"
              :key="edge.key"
              class="kg2d-edge"
              :x1="edge.x1"
              :y1="edge.y1"
              :x2="edge.x2"
              :y2="edge.y2"
              :stroke="edge.color"
              :stroke-width="edgeWidth(edge)"
              :opacity="edgeOpacity(edge)"
              stroke-linecap="round"
              @mouseenter="(e) => hoverEdge(edge, e)"
              @mouseleave="clearHover"
              @click="selectEdge(edge)"
            />
          </g>

          <g class="kg2d-nodes">
            <circle
              v-for="node in layout.nodes"
              :key="node.id"
              class="kg2d-node"
              :cx="node.x"
              :cy="node.y"
              :r="nodeRadius(node)"
              :fill="node.color"
              :opacity="nodeOpacity(node)"
              :stroke="node.id === selectedNodeId ? '#facc15' : 'rgba(15, 23, 42, 0.6)'"
              :stroke-width="node.id === selectedNodeId ? 2.4 : 1.2"
              @mouseenter="(e) => hoverNode(node, e)"
              @mouseleave="clearHover"
              @click="selectNode(node)"
            />
          </g>
        </svg>

        <div
          v-if="hover"
          class="kg2d-tooltip"
          :style="{ left: `${hover.x + 12}px`, top: `${hover.y + 12}px` }"
        >
          <div class="kg2d-tooltip-title">{{ hover.title }}</div>
          <div v-for="(line, i) in hover.lines" :key="i" class="kg2d-tooltip-line">{{ line }}</div>
        </div>
      </div>

      <div class="kg2d-side">
        <div v-if="!selected && !selectedEdge" class="card muted">
          点击节点或连线查看详情（支持悬浮查看标签）
        </div>

        <div v-else-if="selected" class="card">
          <div class="title">{{ selected.label }}</div>
          <div class="small mono">{{ selected.id }}</div>

          <div class="small" style="margin-top: 10px">
            <span class="pill">{{ selected.kind === 'event' ? '事件' : '个股' }}</span>
            <span v-if="selected.count != null" class="pill" style="margin-left: 8px"
              >频次: <span class="mono">{{ selected.count }}</span></span
            >
          </div>

          <div class="feedback-row">
            <span class="small muted">有用吗</span>
            <button class="btn btn-ghost" @click="onThumb('up')" title="有用">赞</button>
            <button class="btn btn-ghost" @click="onThumb('down')" title="无用">踩</button>
          </div>

          <div v-if="selected.kind === 'event'">
            <div class="subtitle">最近证据</div>
            <div class="list">
              <div v-for="a in (selected as any).articles || []" :key="a.canonical_id" class="row">
                <a
                  v-if="a.url"
                  :href="a.url"
                  target="_blank"
                  rel="noreferrer"
                  class="link"
                  @click="
                    sendFeedback('kg_graph_evidence_click', {
                      minutes,
                      node_id: selected.id,
                      canonical_id: a.canonical_id,
                      url: a.url
                    })
                  "
                  >{{ a.title || a.url }}</a
                >
                <span v-else class="small muted">{{ a.title || a.canonical_id }}</span>
                <div class="small muted mono">{{ a.published_at || a.created_at || '-' }}</div>
                <div class="row-actions">
                  <button
                    class="btn btn-ghost btn-xs"
                    title="赞"
                    @click="
                      onArticleThumb('up', {
                        source: 'event',
                        node_id: selected.id,
                        canonical_id: a.canonical_id,
                        url: a.url
                      })
                    "
                  >
                    赞
                  </button>
                  <button
                    class="btn btn-ghost btn-xs"
                    title="踩"
                    @click="
                      onArticleThumb('down', {
                        source: 'event',
                        node_id: selected.id,
                        canonical_id: a.canonical_id,
                        url: a.url
                      })
                    "
                  >
                    踩
                  </button>
                </div>
              </div>
              <div v-if="(((selected as any).articles || []) as any[]).length === 0" class="small muted">暂无</div>
            </div>
          </div>

          <div v-else>
            <div class="subtitle">实体信息</div>
            <div class="small mono">{{ (selected as any).ts_code }}</div>
            <div class="small muted">{{ (selected as any).name || '-' }}</div>
          </div>
        </div>

        <div v-else class="card">
          <div class="title">关系：{{ relationZh((selectedEdgeInfo as any).link.relation) }}</div>
          <div class="small">
            <span class="pill" style="margin-right: 8px">起点</span>
            <span class="mono">{{ (selectedEdgeInfo as any).source?.label || (selectedEdgeInfo as any).link.source }}</span>
          </div>
          <div class="small" style="margin-top: 6px">
            <span class="pill" style="margin-right: 8px">终点</span>
            <span class="mono">{{ (selectedEdgeInfo as any).target?.label || (selectedEdgeInfo as any).link.target }}</span>
          </div>

          <div class="small" style="margin-top: 10px">
            <span class="pill">边</span>
            <span v-if="(selectedEdgeInfo as any).link.weight != null" class="pill" style="margin-left: 8px"
              >权重: <span class="mono">{{ (selectedEdgeInfo as any).link.weight }}</span></span
            >
          </div>

          <div class="subtitle">证据（文章）</div>
          <div class="list">
            <div v-for="a in ((selectedEdgeInfo as any).evidence || [])" :key="a.canonical_id" class="row">
              <a v-if="a.url" class="link" :href="a.url" target="_blank" rel="noreferrer">{{ a.title || a.url }}</a>
              <span v-else class="small muted">{{ a.title || a.canonical_id }}</span>
              <div class="small muted mono">{{ a.canonical_id }}</div>
              <div class="row-actions">
                <button
                  class="btn btn-ghost btn-xs"
                  title="赞"
                  @click="
                    onArticleThumb('up', {
                      source: 'edge',
                      relation: (selectedEdgeInfo as any).link.relation,
                      edge_source: (selectedEdgeInfo as any).link.source,
                      edge_target: (selectedEdgeInfo as any).link.target,
                      canonical_id: a.canonical_id,
                      url: a.url
                    })
                  "
                >
                  赞
                </button>
                <button
                  class="btn btn-ghost btn-xs"
                  title="踩"
                  @click="
                    onArticleThumb('down', {
                      source: 'edge',
                      relation: (selectedEdgeInfo as any).link.relation,
                      edge_source: (selectedEdgeInfo as any).link.source,
                      edge_target: (selectedEdgeInfo as any).link.target,
                      canonical_id: a.canonical_id,
                      url: a.url
                    })
                  "
                >
                  踩
                </button>
              </div>
            </div>
            <div v-if="(((selectedEdgeInfo as any).evidence || []) as any[]).length === 0" class="small muted">暂无</div>
          </div>
        </div>

        <div class="card muted">
          <div class="small">
            提示：颜色区分节点/边类型；节点大小与边粗细反映重要程度，悬浮可查看标签。
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.kg2d {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.kg2d-head {
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 10px;
  align-items: center;
  padding: 12px 12px 10px 14px;
  border-bottom: 1px solid var(--border);
  background: linear-gradient(90deg, rgba(124, 58, 237, 0.12), rgba(59, 130, 246, 0.06));
}

.kg2d-head .title {
  font-weight: 700;
  letter-spacing: 0.3px;
}

.kg2d-head .meta {
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
}

.kg2d-head .actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

.kg2d-error {
  padding: 10px 12px;
  border: 1px solid rgba(239, 68, 68, 0.35);
  background: rgba(239, 68, 68, 0.08);
  border-radius: 12px;
  color: #fecaca;
  font-size: 13px;
}

.kg2d-body {
  display: grid;
  grid-template-columns: 1fr 360px;
  gap: 12px;
}
@media (max-width: 980px) {
  .kg2d-body {
    grid-template-columns: 1fr;
  }
}

.kg2d-canvas {
  position: relative;
  height: 560px;
  border-radius: 16px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background:
    radial-gradient(900px 520px at 25% 10%, rgba(99, 102, 241, 0.16), rgba(0, 0, 0, 0) 60%),
    radial-gradient(900px 520px at 80% 70%, rgba(34, 197, 94, 0.12), rgba(0, 0, 0, 0) 55%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.04), rgba(255, 255, 255, 0.02));
  overflow: hidden;
}

.kg2d-svg {
  width: 100%;
  height: 100%;
  display: block;
}

.kg2d-edge {
  cursor: pointer;
  transition: opacity 0.15s ease, stroke-width 0.15s ease;
}

.kg2d-node {
  cursor: pointer;
  transition: opacity 0.15s ease, r 0.15s ease;
}

.kg2d-tooltip {
  position: absolute;
  z-index: 10;
  max-width: 260px;
  padding: 8px 10px;
  border-radius: 10px;
  background: rgba(4, 6, 10, 0.86);
  border: 1px solid rgba(255, 255, 255, 0.18);
  color: rgba(255, 255, 255, 0.94);
  font-size: 12px;
  line-height: 1.4;
  pointer-events: none;
  backdrop-filter: blur(6px);
}

.kg2d-tooltip-title {
  font-weight: 600;
  margin-bottom: 4px;
}

.kg2d-tooltip-line {
  color: rgba(226, 232, 240, 0.9);
}

.kg2d-side {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.subtitle {
  margin-top: 12px;
  font-size: 12px;
  color: var(--muted);
}

.feedback-row {
  margin-top: 10px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.list {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 320px;
  overflow: auto;
}

.row .link {
  display: block;
  color: #93c5fd;
  text-decoration: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row .link:hover {
  text-decoration: underline;
}

.row-actions {
  display: flex;
  gap: 6px;
  margin-top: 6px;
}

.btn-xs {
  padding: 2px 6px;
  font-size: 11px;
  line-height: 1.1;
}
</style>
