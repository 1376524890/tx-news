<!-- Input: /kg/graph（轮询）+ 用户交互（点击/反馈） -->
<!-- Output: Three.js 3D 知识图谱可视化（实时刷新） -->
<!-- Pos: 3D 图谱组件（变更时同步更新以上注释与所属目录 FOLDER.md） -->

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { CSS2DObject, CSS2DRenderer } from 'three/examples/jsm/renderers/CSS2DRenderer.js'

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
  try {
    error.value = null
    graph.value = await api<KgGraph>(`/kg/graph?minutes=${encodeURIComponent(props.minutes)}`)
  } catch (e: any) {
    error.value = String(e?.message || e)
  }
}

function hash01(s: string): number {
  // Deterministic [0,1) hash for stable layout.
  let h = 2166136261
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619)
  return ((h >>> 0) % 1000000) / 1000000
}

function unitVecFromId(id: string): THREE.Vector3 {
  const a = hash01(id + '|a') * Math.PI * 2
  const z = hash01(id + '|z') * 2 - 1
  const r = Math.sqrt(Math.max(0, 1 - z * z))
  return new THREE.Vector3(r * Math.cos(a), r * Math.sin(a), z)
}

function sphereLayout(i: number, n: number): THREE.Vector3 {
  // Golden spiral on a unit sphere.
  const k = i + 0.5
  const phi = Math.acos(1 - (2 * k) / n)
  const theta = Math.PI * (1 + Math.sqrt(5)) * k
  return new THREE.Vector3(
    Math.cos(theta) * Math.sin(phi),
    Math.sin(theta) * Math.sin(phi),
    Math.cos(phi)
  )
}

type RenderState = {
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  renderer: THREE.WebGLRenderer
  labelRenderer: CSS2DRenderer
  controls: OrbitControls
  raycaster: THREE.Raycaster
  eventsMesh: THREE.InstancedMesh
  tickersMesh: THREE.InstancedMesh
  edgesMesh: THREE.InstancedMesh
  nodeIdsByInstance: { events: string[]; tickers: string[] }
  nodeMetaById: Map<string, KgNode>
  edgeMetaByInstance: KgLink[]
  labelsById: Map<string, CSS2DObject>
  pointerDownHandler: ((ev: PointerEvent) => void) | null
  animId: number | null
  resizeObs: ResizeObserver | null
}

let st: RenderState | null = null

function disposeRenderer() {
  if (!st) return
  if (st.animId != null) cancelAnimationFrame(st.animId)
  st.resizeObs?.disconnect()
  st.controls.dispose()
  if (st.pointerDownHandler) st.renderer.domElement.removeEventListener('pointerdown', st.pointerDownHandler)
  st.renderer.dispose()
  st.labelRenderer.domElement.remove()
  st.scene.clear()
  st = null
}

function relationZh(relation: string): string {
  const r = String(relation || '').trim().toLowerCase()
  if (r === 'mentions') return '提及'
  return relation || '-'
}

function buildScene(el: HTMLDivElement) {
  const scene = new THREE.Scene()

  const camera = new THREE.PerspectiveCamera(55, 1, 0.1, 5000)
  camera.position.set(0, 120, 240)

  // Make sure we can overlay labels on top of the canvas.
  el.style.position = 'relative'

  const renderer = new THREE.WebGLRenderer({
    antialias: true,
    alpha: true,
    powerPreference: 'high-performance'
  })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.setClearColor(0x000000, 0)
  el.appendChild(renderer.domElement)

  const labelRenderer = new CSS2DRenderer()
  labelRenderer.domElement.style.position = 'absolute'
  labelRenderer.domElement.style.top = '0'
  labelRenderer.domElement.style.left = '0'
  labelRenderer.domElement.style.pointerEvents = 'none'
  el.appendChild(labelRenderer.domElement)

  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.minDistance = 80
  controls.maxDistance = 900
  controls.rotateSpeed = 0.65

  // Subtle ambient + rim.
  scene.add(new THREE.AmbientLight(0xffffff, 0.65))
  const dir = new THREE.DirectionalLight(0xffffff, 0.9)
  dir.position.set(2, 3, 4)
  scene.add(dir)

  // "Star field" backdrop.
  const starsGeom = new THREE.BufferGeometry()
  const starsN = 1200
  const stars = new Float32Array(starsN * 3)
  for (let i = 0; i < starsN; i++) {
    const v = unitVecFromId(`star:${i}`)
    v.multiplyScalar(900 + hash01(`star:r:${i}`) * 900)
    stars[i * 3 + 0] = v.x
    stars[i * 3 + 1] = v.y
    stars[i * 3 + 2] = v.z
  }
  starsGeom.setAttribute('position', new THREE.BufferAttribute(stars, 3))
  const starsMat = new THREE.PointsMaterial({
    size: 1.2,
    color: 0x8aa4ff,
    transparent: true,
    opacity: 0.16,
    depthWrite: false
  })
  scene.add(new THREE.Points(starsGeom, starsMat))

  const sphere = new THREE.SphereGeometry(1, 16, 16)

  const eventsMesh = new THREE.InstancedMesh(
    sphere,
    new THREE.MeshStandardMaterial({
      vertexColors: true,
      metalness: 0.12,
      roughness: 0.35,
      emissive: new THREE.Color(0x7c3aed),
      emissiveIntensity: 0.28
    }),
    1
  )
  eventsMesh.frustumCulled = false
  const tickersMesh = new THREE.InstancedMesh(
    sphere,
    new THREE.MeshStandardMaterial({
      vertexColors: true,
      metalness: 0.08,
      roughness: 0.45,
      emissive: new THREE.Color(0x22c55e),
      emissiveIntensity: 0.22
    }),
    1
  )
  tickersMesh.frustumCulled = false

  // Edges: use instanced cylinders (LineBasicMaterial lineWidth is ignored on most platforms).
  const edgeGeom = new THREE.CylinderGeometry(1, 1, 1, 6, 1, true)
  const edgesMesh = new THREE.InstancedMesh(
    edgeGeom,
    new THREE.MeshBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: 0.72,
      depthWrite: false
    }),
    1
  )
  edgesMesh.frustumCulled = false
  scene.add(edgesMesh)
  scene.add(eventsMesh)
  scene.add(tickersMesh)

  const raycaster = new THREE.Raycaster()
  raycaster.params.Points = { threshold: 4 }

  const nodeIdsByInstance = { events: [] as string[], tickers: [] as string[] }
  const nodeMetaById = new Map<string, KgNode>()
  const edgeMetaByInstance: KgLink[] = []
  const labelsById = new Map<string, CSS2DObject>()

  const resize = () => {
    const { width, height } = el.getBoundingClientRect()
    const w = Math.max(1, Math.floor(width))
    const h = Math.max(1, Math.floor(height))
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    renderer.setSize(w, h, false)
    labelRenderer.setSize(w, h)
  }
  resize()
  const resizeObs = new ResizeObserver(() => resize())
  resizeObs.observe(el)

  const onPointerDown = (ev: PointerEvent) => {
    if (!st) return
    const rect = el.getBoundingClientRect()
    const x = ((ev.clientX - rect.left) / rect.width) * 2 - 1
    const y = -(((ev.clientY - rect.top) / rect.height) * 2 - 1)
    raycaster.setFromCamera(new THREE.Vector2(x, y), camera)

    const edgeHits = raycaster.intersectObject(edgesMesh, true)
    const nodeHits = [
      ...raycaster.intersectObject(eventsMesh, true),
      ...raycaster.intersectObject(tickersMesh, true)
    ]

    if (!edgeHits.length && !nodeHits.length) {
      selected.value = null
      selectedEdge.value = null
      return
    }

    edgeHits.sort((a, b) => (a.distance || 0) - (b.distance || 0))
    nodeHits.sort((a, b) => (a.distance || 0) - (b.distance || 0))

    // UX: prefer selecting nodes when the click is near a node, even if a thick edge is also hit.
    const nearestEdge = edgeHits[0]
    const nearestNode = nodeHits[0]
    const preferNode = Boolean(nearestNode && (!nearestEdge || nearestNode.distance <= nearestEdge.distance + 2))

    const h0 = (preferNode ? nearestNode : nearestEdge)!
    const instanceId = (h0 as any).instanceId as number | undefined
    const obj = h0.object

    if (typeof instanceId === 'number') {
      if (!preferNode && obj === edgesMesh) {
        const link = st.edgeMetaByInstance[instanceId] || null
        if (!link) return
        selectedEdge.value = link
        selected.value = null
        sendFeedback('kg_graph_edge_click', {
          minutes: props.minutes,
          relation: link.relation,
          source: link.source,
          target: link.target,
          weight: link.weight
        })
        return
      }

      let nodeId: string | null = null
      if (obj === eventsMesh) nodeId = nodeIdsByInstance.events[instanceId] || null
      else if (obj === tickersMesh) nodeId = nodeIdsByInstance.tickers[instanceId] || null
      if (!nodeId) return
      const node = nodeMetaById.get(nodeId) || null
      if (!node) return
      selected.value = node
      selectedEdge.value = null
      sendFeedback('kg_graph_node_click', { minutes: props.minutes, node_id: node.id, kind: node.kind })
    }
  }
  renderer.domElement.addEventListener('pointerdown', onPointerDown)

  const tick = () => {
    controls.update()
    renderer.render(scene, camera)
    labelRenderer.render(scene, camera)
    if (st) st.animId = requestAnimationFrame(tick)
  }
  const animId = requestAnimationFrame(tick)

  st = {
    scene,
    camera,
    renderer,
    labelRenderer,
    controls,
    raycaster,
    eventsMesh,
    tickersMesh,
    edgesMesh,
    nodeIdsByInstance,
    nodeMetaById,
    edgeMetaByInstance,
    labelsById,
    pointerDownHandler: onPointerDown,
    animId,
    resizeObs
  }
}

function updateGraphRender(g: KgGraph | null) {
  if (!st || !g) return

  const nodes = g.nodes || []
  const links = g.links || []

  const events = nodes.filter((n) => n.kind === 'event') as KgEventNode[]
  const tickers = nodes.filter((n) => n.kind === 'ticker') as KgTickerNode[]

  events.sort((a, b) => String(a.event_id).localeCompare(String(b.event_id)))
  tickers.sort((a, b) => String(a.ts_code).localeCompare(String(b.ts_code)))

  st.nodeMetaById.clear()
  for (const n of nodes) st.nodeMetaById.set(n.id, n)

  const eventPos = new Map<string, THREE.Vector3>()
  const eventRadius = 120
  for (let i = 0; i < events.length; i++) {
    const v = sphereLayout(i, Math.max(1, events.length)).multiplyScalar(eventRadius)
    eventPos.set(events[i]!.id, v)
  }

  // Build adjacency for tickers -> related event positions.
  const tickerEvents = new Map<string, string[]>()
  for (const e of links) {
    if (typeof e.source !== 'string' || typeof e.target !== 'string') continue
    if (!e.source.startsWith('event:')) continue
    if (!e.target.startsWith('ticker:')) continue
    const xs = tickerEvents.get(e.target) || []
    xs.push(e.source)
    tickerEvents.set(e.target, xs)
  }

  const tickerPos = new Map<string, THREE.Vector3>()
  for (const t of tickers) {
    const evs = tickerEvents.get(t.id) || []
    const base = new THREE.Vector3(0, 0, 0)
    let n = 0
    for (const eid of evs) {
      const p = eventPos.get(eid)
      if (!p) continue
      base.add(p)
      n++
    }
    if (n > 0) base.multiplyScalar(1 / n)
    else base.copy(unitVecFromId(t.id).multiplyScalar(eventRadius * 0.65))
    base.add(unitVecFromId(t.id).multiplyScalar(18 + hash01(t.id) * 22))
    tickerPos.set(t.id, base)
  }

  // Resize instanced meshes.
  st.eventsMesh.count = events.length
  st.tickersMesh.count = tickers.length
  st.eventsMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage)
  st.tickersMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage)

  st.nodeIdsByInstance.events = events.map((e) => e.id)
  st.nodeIdsByInstance.tickers = tickers.map((t) => t.id)

  const tmpMat = new THREE.Matrix4()
  const tmpPos = new THREE.Vector3()
  const tmpScale = new THREE.Vector3()
  const tmpQuat = new THREE.Quaternion()

  for (let i = 0; i < events.length; i++) {
    const e = events[i]!
    const p = eventPos.get(e.id) || new THREE.Vector3()
    const c = Math.min(40, Math.max(1, Number(e.count || 1)))
    const s = 2.4 + Math.sqrt(c) * 0.35
    tmpPos.copy(p)
    tmpScale.set(s, s, s)
    tmpMat.compose(tmpPos, tmpQuat, tmpScale)
    st.eventsMesh.setMatrixAt(i, tmpMat)
  }
  st.eventsMesh.instanceMatrix.needsUpdate = true

  for (let i = 0; i < tickers.length; i++) {
    const t = tickers[i]!
    const p = tickerPos.get(t.id) || new THREE.Vector3()
    const c = Math.min(80, Math.max(1, Number(t.count || 1)))
    const s = 1.25 + Math.sqrt(c) * 0.12
    tmpPos.copy(p)
    tmpScale.set(s, s, s)
    tmpMat.compose(tmpPos, tmpQuat, tmpScale)
    st.tickersMesh.setMatrixAt(i, tmpMat)
  }
  st.tickersMesh.instanceMatrix.needsUpdate = true

  // Links (instanced cylinders).
  const allowed = new Map<string, THREE.Vector3>([...eventPos.entries(), ...tickerPos.entries()])

  const selectedNodeId = selected.value?.id || null
  const selectedEdgeKey = selectedEdge.value
    ? `${selectedEdge.value.source}|${selectedEdge.value.target}|${selectedEdge.value.relation}`
    : null

  st.edgesMesh.count = links.length
  st.edgesMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage)
  st.edgeMetaByInstance.length = 0
  st.edgeMetaByInstance.push(...links)

  const up = new THREE.Vector3(0, 1, 0)
  const dir = new THREE.Vector3()
  const mid = new THREE.Vector3()
  const tmpCol = new THREE.Color()
  const baseEdge = new THREE.Color(0x60a5fa)
  const dimEdge = new THREE.Color(0x334155)

  for (let i = 0; i < links.length; i++) {
    const l = links[i]!
    const a = allowed.get(String(l.source))
    const b = allowed.get(String(l.target))
    if (!a || !b) continue

    dir.copy(b).sub(a)
    const len = Math.max(0.001, dir.length())
    mid.copy(a).add(b).multiplyScalar(0.5)

    // Thickness: make edges visually prominent; weight increases thickness slightly.
    const w = Math.max(1, Number(l.weight || 1))
    let r = 0.32 + Math.min(0.65, Math.sqrt(w) * 0.08)

    const edgeKey = `${l.source}|${l.target}|${l.relation}`
    const isSelected = selectedEdgeKey && edgeKey === selectedEdgeKey
    const isIncident = selectedNodeId
      ? String(l.source) === selectedNodeId || String(l.target) === selectedNodeId
      : false

    if (isSelected) r *= 1.7
    else if (selectedNodeId && !isIncident) r *= 0.65

    tmpScale.set(r, len, r)
    tmpPos.copy(mid)
    tmpQuat.setFromUnitVectors(up, dir.normalize())
    tmpMat.compose(tmpPos, tmpQuat, tmpScale)
    st.edgesMesh.setMatrixAt(i, tmpMat)

    if (isSelected) tmpCol.set(0xfbbf24)
    else if (selectedNodeId && !isIncident) tmpCol.copy(dimEdge)
    else tmpCol.copy(baseEdge).offsetHSL(0, 0, Math.min(0.22, Math.log1p(w) * 0.06))
    st.edgesMesh.setColorAt(i, tmpCol)
  }
  st.edgesMesh.instanceMatrix.needsUpdate = true
  if (st.edgesMesh.instanceColor) st.edgesMesh.instanceColor.needsUpdate = true

  // Node colors + labels.
  const active = new Set(nodes.map((n) => n.id))
  for (const [id, obj] of st.labelsById.entries()) {
    if (!active.has(id)) {
      st.scene.remove(obj)
      obj.element.remove()
      st.labelsById.delete(id)
    }
  }

  const eventCol = new THREE.Color(0xa78bfa)
  const tickerCol = new THREE.Color(0x34d399)
  const dimNode = new THREE.Color(0x475569)

  for (let i = 0; i < events.length; i++) {
    const e = events[i]!
    const isSel = selected.value?.id === e.id
    const c = Math.min(40, Math.max(1, Number(e.count || 1)))
    const s = (2.6 + Math.sqrt(c) * 0.4) * (isSel ? 1.22 : 1.0)
    st.eventsMesh.getMatrixAt(i, tmpMat)
    tmpMat.decompose(tmpPos, tmpQuat, tmpScale)
    tmpScale.set(s, s, s)
    tmpMat.compose(tmpPos, tmpQuat, tmpScale)
    st.eventsMesh.setMatrixAt(i, tmpMat)
    st.eventsMesh.setColorAt(i, selectedNodeId && !isSel ? dimNode : eventCol)

    const labelText = String(e.event_type || e.label || e.event_id || '').trim() || e.id
    const obj = st.labelsById.get(e.id) || null
    const div = obj ? (obj.element as HTMLDivElement) : document.createElement('div')
    if (!obj) {
      div.className = 'kg3d-label kg3d-label--event'
      const o = new CSS2DObject(div)
      st.labelsById.set(e.id, o)
      st.scene.add(o)
    }
    div.textContent = labelText
    st.labelsById.get(e.id)!.position.copy(tmpPos).add(new THREE.Vector3(0, s * 1.15, 0))
  }
  st.eventsMesh.instanceMatrix.needsUpdate = true
  if (st.eventsMesh.instanceColor) st.eventsMesh.instanceColor.needsUpdate = true

  for (let i = 0; i < tickers.length; i++) {
    const t = tickers[i]!
    const isSel = selected.value?.id === t.id
    const c = Math.min(80, Math.max(1, Number(t.count || 1)))
    const s = (1.35 + Math.sqrt(c) * 0.14) * (isSel ? 1.25 : 1.0)
    st.tickersMesh.getMatrixAt(i, tmpMat)
    tmpMat.decompose(tmpPos, tmpQuat, tmpScale)
    tmpScale.set(s, s, s)
    tmpMat.compose(tmpPos, tmpQuat, tmpScale)
    st.tickersMesh.setMatrixAt(i, tmpMat)
    st.tickersMesh.setColorAt(i, selectedNodeId && !isSel ? dimNode : tickerCol)

    const labelText = String(t.ts_code || t.label || '').trim() || t.id
    const obj = st.labelsById.get(t.id) || null
    const div = obj ? (obj.element as HTMLDivElement) : document.createElement('div')
    if (!obj) {
      div.className = 'kg3d-label kg3d-label--ticker'
      const o = new CSS2DObject(div)
      st.labelsById.set(t.id, o)
      st.scene.add(o)
    }
    div.textContent = labelText
    st.labelsById.get(t.id)!.position.copy(tmpPos).add(new THREE.Vector3(0, s * 1.2, 0))
  }
  st.tickersMesh.instanceMatrix.needsUpdate = true
  if (st.tickersMesh.instanceColor) st.tickersMesh.instanceColor.needsUpdate = true
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

onMounted(() => {
  if (!container.value) return
  buildScene(container.value)
  refresh()
  const t = setInterval(refresh, pollMs.value)
  onUnmounted(() => clearInterval(t))
})

onUnmounted(() => disposeRenderer())

watch(
  () => props.minutes,
  () => refresh()
)

watch(
  () => graph.value,
  (g) => updateGraphRender(g)
)

watch(
  () => selected.value,
  () => updateGraphRender(graph.value)
)

watch(
  () => selectedEdge.value,
  () => updateGraphRender(graph.value)
)
</script>

<template>
  <div class="kg3d">
    <div class="kg3d-head">
      <div class="title">知识图谱（3D）</div>
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

    <div v-if="error" class="kg3d-error">{{ error }}</div>

    <div class="kg3d-body">
      <div class="kg3d-canvas" ref="container"></div>

      <div class="kg3d-side">
        <div v-if="!selected && !selectedEdge" class="card muted">点击节点或连线查看详情（支持拖拽旋转/滚轮缩放）</div>

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
              <div
                v-for="a in (selected as any).articles || []"
                :key="a.canonical_id"
                class="row"
              >
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
            </div>
            <div v-if="(((selectedEdgeInfo as any).evidence || []) as any[]).length === 0" class="small muted">暂无</div>
          </div>
        </div>

        <div class="card muted">
          <div class="small">
            提示：默认显示节点标签；边的粗细反映连接强度（weight）。
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.kg3d {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.kg3d-head {
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 10px;
  align-items: center;
  padding: 12px 12px 10px 14px;
  border-bottom: 1px solid var(--border);
  background: linear-gradient(90deg, rgba(124, 58, 237, 0.12), rgba(59, 130, 246, 0.06));
}

.kg3d-head .title {
  font-weight: 700;
  letter-spacing: 0.3px;
}

.kg3d-head .meta {
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
}

.kg3d-head .actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

.kg3d-error {
  padding: 10px 12px;
  border: 1px solid rgba(239, 68, 68, 0.35);
  background: rgba(239, 68, 68, 0.08);
  border-radius: 12px;
  color: #fecaca;
  font-size: 13px;
}

.kg3d-body {
  display: grid;
  grid-template-columns: 1fr 360px;
  gap: 12px;
}
@media (max-width: 980px) {
  .kg3d-body {
    grid-template-columns: 1fr;
  }
}

.kg3d-canvas {
  height: 560px;
  border-radius: 16px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background:
    radial-gradient(900px 520px at 25% 10%, rgba(99, 102, 241, 0.16), rgba(0, 0, 0, 0) 60%),
    radial-gradient(900px 520px at 80% 70%, rgba(34, 197, 94, 0.12), rgba(0, 0, 0, 0) 55%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.04), rgba(255, 255, 255, 0.02));
  overflow: hidden;
}

.kg3d-label {
  color: rgba(255, 255, 255, 0.94);
  background: rgba(4, 6, 10, 0.72);
  border: 1px solid rgba(255, 255, 255, 0.18);
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 13px;
  line-height: 1.25;
  white-space: nowrap;
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(6px);
  transform: translate(-50%, -50%);
}

.kg3d-label--event {
  border-color: rgba(167, 139, 250, 0.65);
  background: rgba(26, 14, 44, 0.62);
}

.kg3d-label--ticker {
  border-color: rgba(52, 211, 153, 0.55);
  background: rgba(6, 34, 22, 0.58);
}

.kg3d-side {
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
</style>
