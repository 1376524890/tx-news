<!-- Input: 近实时采样点（抓取/分析/深分析/队列积压） -->
<!-- Output: SVG 折线图（双 Y 轴 + Tooltip） -->
<!-- Pos: 管理台/看板通用时序图组件（变更时同步更新以上注释与所属目录 FOLDER.md） -->

<script setup lang="ts">
import { computed, ref } from 'vue'

export type MetricPoint = {
  ts: number
  crawl: number
  analysis: number
  deep: number
  backlog: number
}

const props = defineProps<{
  points: MetricPoint[]
  title?: string
}>()

const VW = 920
const VH = 260
const margin = { l: 52, r: 54, t: 18, b: 30 }
const innerW = VW - margin.l - margin.r
const innerH = VH - margin.t - margin.b

function safeNumber(v: unknown): number {
  const x = Number(v)
  return Number.isFinite(x) ? x : 0
}

const n = computed(() => props.points.length)

const leftRange = computed(() => {
  const vs: number[] = []
  for (const p of props.points) {
    vs.push(safeNumber(p.crawl), safeNumber(p.analysis), safeNumber(p.deep))
  }
  if (vs.length === 0) return { min: 0, max: 1 }
  const min = Math.min(...vs)
  const max = Math.max(...vs)
  return { min, max: max === min ? min + 1 : max }
})

const rightRange = computed(() => {
  const vs: number[] = []
  for (const p of props.points) vs.push(safeNumber(p.backlog))
  if (vs.length === 0) return { min: 0, max: 1 }
  const min = Math.min(...vs)
  const max = Math.max(...vs)
  return { min, max: max === min ? min + 1 : max }
})

function xAt(i: number): number {
  const denom = Math.max(1, (n.value || 0) - 1)
  return margin.l + (i / denom) * innerW
}

function yLeft(v: number): number {
  const { min, max } = leftRange.value
  const t = (safeNumber(v) - min) / (max - min)
  return margin.t + innerH - t * innerH
}

function yRight(v: number): number {
  const { min, max } = rightRange.value
  const t = (safeNumber(v) - min) / (max - min)
  return margin.t + innerH - t * innerH
}

function pathOf(getter: (p: MetricPoint) => number, yFn: (v: number) => number): string {
  if (n.value < 2) return ''
  let d = ''
  for (let i = 0; i < n.value; i++) {
    const p = props.points[i]!
    const x = xAt(i)
    const y = yFn(getter(p))
    d += (i === 0 ? 'M' : 'L') + x.toFixed(2) + ',' + y.toFixed(2)
  }
  return d
}

const paths = computed(() => {
  return {
    crawl: pathOf((p) => safeNumber(p.crawl), yLeft),
    analysis: pathOf((p) => safeNumber(p.analysis), yLeft),
    deep: pathOf((p) => safeNumber(p.deep), yLeft),
    backlog: pathOf((p) => safeNumber(p.backlog), yRight)
  }
})

const xLabels = computed(() => {
  if (n.value === 0) return { left: '-', right: '-' }
  const a = props.points[0]?.ts
  const b = props.points[n.value - 1]?.ts
  return { left: fmtTime(a), right: fmtTime(b) }
})

function fmtTime(ts?: number): string {
  if (!ts) return '-'
  const d = new Date(ts)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function fmtNum(v: unknown): string {
  const x = Number(v)
  if (!Number.isFinite(x)) return '-'
  if (x >= 1e9) return (x / 1e9).toFixed(2) + 'B'
  if (x >= 1e6) return (x / 1e6).toFixed(2) + 'M'
  if (x >= 1e3) return (x / 1e3).toFixed(1) + 'K'
  return String(Math.round(x * 10) / 10)
}

const hover = ref<{ index: number; pxLeft: number; pxTop: number } | null>(null)

function onMove(e: MouseEvent) {
  if (n.value < 2) return
  const svg = e.currentTarget as SVGSVGElement | null
  if (!svg) return
  const rect = svg.getBoundingClientRect()

  const px = e.clientX - rect.left
  const x0 = (margin.l / VW) * rect.width
  const x1 = rect.width - (margin.r / VW) * rect.width
  const clamped = Math.min(Math.max(px, x0), x1)
  const ratio = (clamped - x0) / Math.max(1, x1 - x0)
  const index = Math.round(ratio * (n.value - 1))

  hover.value = {
    index,
    pxLeft: Math.min(rect.width - 220, clamped + 12),
    pxTop: 10
  }
}

function onLeave() {
  hover.value = null
}

const hoverData = computed(() => {
  if (!hover.value) return null
  const p = props.points[hover.value.index]
  if (!p) return null
  return {
    ts: fmtTime(p.ts),
    crawl: fmtNum(p.crawl),
    analysis: fmtNum(p.analysis),
    deep: fmtNum(p.deep),
    backlog: fmtNum(p.backlog),
    x: xAt(hover.value.index)
  }
})

const yTicks = computed(() => {
  const ticks = 4
  const left = leftRange.value
  const right = rightRange.value
  const out = []
  for (let i = 0; i <= ticks; i++) {
    const t = i / ticks
    out.push({
      y: margin.t + innerH - t * innerH,
      left: fmtNum(left.min + t * (left.max - left.min)),
      right: fmtNum(right.min + t * (right.max - right.min))
    })
  }
  return out
})

const latest = computed(() => {
  if (n.value === 0) return null
  const p = props.points[props.points.length - 1]
  if (!p) return null
  return {
    crawl: fmtNum(p.crawl),
    analysis: fmtNum(p.analysis),
    deep: fmtNum(p.deep),
    backlog: fmtNum(p.backlog)
  }
})
</script>

<template>
  <div class="ts-wrap">
    <div v-if="title" class="ts-title">{{ title }}</div>
    <div class="ts-chart">
      <svg
        class="ts-svg"
        :viewBox="`0 0 ${VW} ${VH}`"
        preserveAspectRatio="none"
        @mousemove="onMove"
        @mouseleave="onLeave"
      >
        <rect x="0" y="0" :width="VW" :height="VH" fill="transparent" />

        <g class="grid">
          <line
            v-for="t in yTicks"
            :key="t.y"
            :x1="margin.l"
            :x2="VW - margin.r"
            :y1="t.y"
            :y2="t.y"
          />
        </g>

        <g class="axes">
          <line :x1="margin.l" :x2="margin.l" :y1="margin.t" :y2="VH - margin.b" />
          <line :x1="VW - margin.r" :x2="VW - margin.r" :y1="margin.t" :y2="VH - margin.b" />
          <line :x1="margin.l" :x2="VW - margin.r" :y1="VH - margin.b" :y2="VH - margin.b" />
        </g>

        <g class="labels">
          <text v-for="t in yTicks" :key="`l-${t.y}`" :x="margin.l - 10" :y="t.y + 4" text-anchor="end">
            {{ t.left }}
          </text>
          <text
            v-for="t in yTicks"
            :key="`r-${t.y}`"
            :x="VW - margin.r + 10"
            :y="t.y + 4"
            text-anchor="start"
          >
            {{ t.right }}
          </text>

          <text :x="margin.l" :y="VH - 10" text-anchor="start">{{ xLabels.left }}</text>
          <text :x="VW - margin.r" :y="VH - 10" text-anchor="end">{{ xLabels.right }}</text>
        </g>

        <g class="lines">
          <path v-if="paths.crawl" :d="paths.crawl" class="l-crawl" />
          <path v-if="paths.analysis" :d="paths.analysis" class="l-analysis" />
          <path v-if="paths.deep" :d="paths.deep" class="l-deep" />
          <path v-if="paths.backlog" :d="paths.backlog" class="l-backlog" />
        </g>

        <g v-if="hoverData" class="hover">
          <line :x1="hoverData.x" :x2="hoverData.x" :y1="margin.t" :y2="VH - margin.b" />
        </g>
      </svg>

      <div v-if="hoverData && hover" class="ts-tip" :style="{ left: hover.pxLeft + 'px', top: hover.pxTop + 'px' }">
        <div class="ts-tip-title">{{ hoverData.ts }}</div>
        <div class="ts-tip-row"><span class="sw crawl"></span>抓取：{{ hoverData.crawl }}</div>
        <div class="ts-tip-row"><span class="sw analysis"></span>分析：{{ hoverData.analysis }}</div>
        <div class="ts-tip-row"><span class="sw deep"></span>深度：{{ hoverData.deep }}</div>
        <div class="ts-tip-row"><span class="sw backlog"></span>积压：{{ hoverData.backlog }}</div>
      </div>
    </div>

    <div class="ts-legend">
      <div class="lg"><span class="sw crawl"></span>抓取<span class="v">{{ latest?.crawl ?? '-' }}</span></div>
      <div class="lg"><span class="sw analysis"></span>分析<span class="v">{{ latest?.analysis ?? '-' }}</span></div>
      <div class="lg"><span class="sw deep"></span>深度分析<span class="v">{{ latest?.deep ?? '-' }}</span></div>
      <div class="lg"><span class="sw backlog"></span>队列积压<span class="v">{{ latest?.backlog ?? '-' }}</span></div>
      <div class="lg-note">左轴：抓取/分析/深度分析；右轴：队列积压</div>
    </div>
  </div>
</template>

<style scoped>
.ts-wrap {
  position: relative;
}
.ts-title {
  font-weight: 650;
  font-size: 13px;
  margin-bottom: 8px;
  color: rgba(230, 237, 243, 0.92);
}
.ts-chart {
  position: relative;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: rgba(8, 10, 14, 0.35);
  overflow: hidden;
}
.ts-svg {
  width: 100%;
  height: 240px;
  display: block;
}
.grid line {
  stroke: rgba(255, 255, 255, 0.06);
  stroke-width: 1;
}
.axes line {
  stroke: rgba(255, 255, 255, 0.12);
  stroke-width: 1;
}
.labels text {
  font-size: 11px;
  fill: rgba(154, 167, 181, 0.9);
}
.lines path {
  fill: none;
  stroke-width: 2.2;
  stroke-linecap: round;
  stroke-linejoin: round;
  filter: drop-shadow(0 2px 8px rgba(0, 0, 0, 0.25));
}
.l-crawl {
  stroke: #93c5fd;
}
.l-analysis {
  stroke: #fcd34d;
}
.l-deep {
  stroke: #a7f3d0;
}
.l-backlog {
  stroke: #c4b5fd;
  stroke-dasharray: 6 4;
}
.hover line {
  stroke: rgba(255, 255, 255, 0.18);
  stroke-width: 1;
}
.ts-tip {
  position: absolute;
  width: 210px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(8, 10, 14, 0.88);
  border-radius: 12px;
  padding: 10px;
  font-size: 12px;
  color: rgba(230, 237, 243, 0.95);
  backdrop-filter: blur(10px);
}
.ts-tip-title {
  font-weight: 700;
  margin-bottom: 6px;
}
.ts-tip-row {
  display: flex;
  align-items: center;
  gap: 8px;
  color: rgba(230, 237, 243, 0.92);
  margin: 4px 0;
}
.sw {
  width: 10px;
  height: 10px;
  border-radius: 3px;
  display: inline-block;
}
.sw.crawl {
  background: #93c5fd;
}
.sw.analysis {
  background: #fcd34d;
}
.sw.deep {
  background: #a7f3d0;
}
.sw.backlog {
  background: #c4b5fd;
}
.ts-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 14px;
  margin-top: 10px;
  color: var(--muted);
  font-size: 12px;
  align-items: center;
}
.lg {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.03);
  padding: 6px 10px;
  border-radius: 999px;
}
.lg .v {
  color: rgba(230, 237, 243, 0.92);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New",
    monospace;
}
.lg-note {
  opacity: 0.9;
}
</style>
