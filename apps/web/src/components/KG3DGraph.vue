<!-- Input: /kg/graph（轮询）+ 用户交互（点击/反馈） -->
<!-- Output: Three.js 3D 知识图谱可视化（实时刷新） -->
<!-- Pos: 3D 图谱组件（变更时同步更新以上注释与所属目录 FOLDER.md） -->

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

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
  controls: OrbitControls
  raycaster: THREE.Raycaster
  eventsMesh: THREE.InstancedMesh
  tickersMesh: THREE.InstancedMesh
  linksMesh: THREE.LineSegments
  nodeIdsByInstance: { events: string[]; tickers: string[] }
  nodeMetaById: Map<string, KgNode>
  animId: number | null
  resizeObs: ResizeObserver | null
}

let st: RenderState | null = null

function disposeRenderer() {
  if (!st) return
  if (st.animId != null) cancelAnimationFrame(st.animId)
  st.resizeObs?.disconnect()
  st.controls.dispose()
  st.renderer.dispose()
  st.scene.clear()
  st = null
}

function buildScene(el: HTMLDivElement) {
  const scene = new THREE.Scene()

  const camera = new THREE.PerspectiveCamera(55, 1, 0.1, 5000)
  camera.position.set(0, 120, 240)

  const renderer = new THREE.WebGLRenderer({
    antialias: true,
    alpha: true,
    powerPreference: 'high-performance'
  })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.setClearColor(0x000000, 0)
  el.appendChild(renderer.domElement)

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
    new THREE.MeshStandardMaterial({ color: 0x7c3aed, metalness: 0.2, roughness: 0.35 }),
    1
  )
  const tickersMesh = new THREE.InstancedMesh(
    sphere,
    new THREE.MeshStandardMaterial({ color: 0x22c55e, metalness: 0.12, roughness: 0.45 }),
    1
  )

  // Lines
  const linksGeom = new THREE.BufferGeometry()
  const linksMat = new THREE.LineBasicMaterial({
    color: 0x93c5fd,
    transparent: true,
    opacity: 0.22
  })
  const linksMesh = new THREE.LineSegments(linksGeom, linksMat)
  scene.add(linksMesh)
  scene.add(eventsMesh)
  scene.add(tickersMesh)

  const raycaster = new THREE.Raycaster()
  raycaster.params.Points = { threshold: 4 }

  const nodeIdsByInstance = { events: [] as string[], tickers: [] as string[] }
  const nodeMetaById = new Map<string, KgNode>()

  const resize = () => {
    const { width, height } = el.getBoundingClientRect()
    const w = Math.max(1, Math.floor(width))
    const h = Math.max(1, Math.floor(height))
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    renderer.setSize(w, h, false)
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

    const hits = [
      ...raycaster.intersectObject(eventsMesh, true),
      ...raycaster.intersectObject(tickersMesh, true)
    ]
    if (!hits.length) return
    hits.sort((a, b) => (a.distance || 0) - (b.distance || 0))
    const h0 = hits[0]!
    const instanceId = (h0 as any).instanceId as number | undefined
    const obj = h0.object

    let nodeId: string | null = null
    if (typeof instanceId === 'number') {
      if (obj === eventsMesh) nodeId = nodeIdsByInstance.events[instanceId] || null
      else if (obj === tickersMesh) nodeId = nodeIdsByInstance.tickers[instanceId] || null
    }
    if (!nodeId) return
    const node = nodeMetaById.get(nodeId) || null
    if (!node) return
    selected.value = node
    sendFeedback('kg_graph_node_click', { minutes: props.minutes, node_id: node.id, kind: node.kind })
  }
  renderer.domElement.addEventListener('pointerdown', onPointerDown)

  const tick = () => {
    controls.update()
    renderer.render(scene, camera)
    if (st) st.animId = requestAnimationFrame(tick)
  }
  const animId = requestAnimationFrame(tick)

  st = {
    scene,
    camera,
    renderer,
    controls,
    raycaster,
    eventsMesh,
    tickersMesh,
    linksMesh,
    nodeIdsByInstance,
    nodeMetaById,
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

  // Links (line segments).
  const allowed = new Map<string, THREE.Vector3>([...eventPos.entries(), ...tickerPos.entries()])
  const pairs: Array<[THREE.Vector3, THREE.Vector3]> = []
  for (const l of links) {
    const s = allowed.get(String(l.source))
    const t = allowed.get(String(l.target))
    if (!s || !t) continue
    pairs.push([s, t])
  }
  const pos = new Float32Array(pairs.length * 6)
  for (let i = 0; i < pairs.length; i++) {
    const [a, b] = pairs[i]!
    pos[i * 6 + 0] = a.x
    pos[i * 6 + 1] = a.y
    pos[i * 6 + 2] = a.z
    pos[i * 6 + 3] = b.x
    pos[i * 6 + 4] = b.y
    pos[i * 6 + 5] = b.z
  }
  st.linksMesh.geometry.dispose()
  const g2 = new THREE.BufferGeometry()
  g2.setAttribute('position', new THREE.BufferAttribute(pos, 3))
  st.linksMesh.geometry = g2
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
</script>

<template>
  <div class="kg3d">
    <div class="kg3d-head">
      <div class="title">Knowledge Graph (3D)</div>
      <div class="meta mono">
        <span>{{ graph?.stats?.events ?? '-' }} events</span>
        <span>·</span>
        <span>{{ graph?.stats?.tickers ?? '-' }} tickers</span>
        <span>·</span>
        <span>{{ graph?.stats?.links ?? '-' }} links</span>
        <span v-if="graph?.generated_at">·</span>
        <span v-if="graph?.generated_at" class="muted">updated {{ graph?.generated_at }}</span>
      </div>
      <div class="actions">
        <button class="btn btn-ghost" @click="refresh">刷新</button>
      </div>
    </div>

    <div v-if="error" class="kg3d-error">{{ error }}</div>

    <div class="kg3d-body">
      <div class="kg3d-canvas" ref="container"></div>

      <div class="kg3d-side">
        <div v-if="!selected" class="card muted">点击图中的节点查看详情</div>

        <div v-else class="card">
          <div class="title">{{ selected.label }}</div>
          <div class="small mono">{{ selected.id }}</div>

          <div class="small" style="margin-top: 10px">
            <span class="pill">{{ selected.kind }}</span>
            <span v-if="selected.count != null" class="pill" style="margin-left: 8px"
              >count: <span class="mono">{{ selected.count }}</span></span
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

        <div class="card muted">
          <div class="small">
            交互：拖拽旋转 / 滚轮缩放 / 点击节点查看。
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
