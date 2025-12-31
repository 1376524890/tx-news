<!-- Input: /status + /dashboard/summary（轮询） -->
<!-- Output: 分析结果看板（KPI + 热点 + 最新输出） -->
<!-- Pos: 前端看板页（变更时同步更新以上注释与所属目录 FOLDER.md） -->

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

type DashboardRecentItem = {
  canonical_id: string
  kind: string
  created_at: string | null
  title: string | null
  url: string | null
  published_at: string | null
  event_type: string | null
  tickers: Array<{ ts_code?: string; name?: string }> | null
  impact: { scope?: string; direction?: string; confidence?: number } | null
  llm_used: boolean
  deep_optimized_at: string | null
}

type DashboardSummary = {
  window_minutes: number
  signals_by_kind: Record<string, number>
  top_event_types: Array<{ event_type: string; count: number }>
  top_tickers: Array<{ ts_code: string; count: number }>
  recent: DashboardRecentItem[]
}

const windowMinutes = ref(180)
const summary = ref<DashboardSummary | null>(null)
const status = ref<any>(null)
const error = ref<string | null>(null)
const pollStats = ref({ n: 0, totalMs: 0 })

async function api(path: string) {
  const started = performance.now()
  const res = await fetch(path, { cache: 'no-store' })
  const text = await res.text()
  let data: any = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { raw: text }
  }
  if (!res.ok) throw new Error((data && (data.error || data.detail)) || `HTTP ${res.status}`)
  const elapsed = performance.now() - started
  pollStats.value = { n: pollStats.value.n + 1, totalMs: pollStats.value.totalMs + elapsed }
  return data
}

async function refresh() {
  try {
    error.value = null
    const [st, s] = await Promise.all([
      api('/status'),
      api(`/dashboard/summary?minutes=${windowMinutes.value}&limit=40`)
    ])
    status.value = st
    summary.value = s
  } catch (e: any) {
    error.value = String(e?.message || e)
  }
}

const healthOk = computed(() => {
  const deps = status.value?.dependencies || {}
  return Object.values(deps).every((x: any) => x && x.ok)
})

const avgPollMs = computed(() => {
  const n = pollStats.value.n
  if (!n) return null
  return pollStats.value.totalMs / n
})

const totalCounts = computed(() => status.value?.counts || {})

const windowSignals = computed(() => {
  const by = summary.value?.signals_by_kind || {}
  return {
    analysis: by.analysis_updated ?? 0,
    deep: by.deep_analysis_updated ?? 0,
    other: Object.entries(by)
      .filter(([k]) => k !== 'analysis_updated' && k !== 'deep_analysis_updated')
      .reduce((acc, [, v]) => acc + (Number(v) || 0), 0)
  }
})

function fmtTs(iso?: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString()
}

function fmtImpact(r: DashboardRecentItem): string {
  const direction = r.impact?.direction || '-'
  const conf = r.impact?.confidence
  if (typeof conf === 'number') return `${direction} (${Math.round(conf * 100)}%)`
  return direction
}

function tickersText(r: DashboardRecentItem): string {
  const t = r.tickers || []
  const xs = t
    .map((x) => x?.ts_code)
    .filter((x): x is string => typeof x === 'string' && x.length > 0)
    .slice(0, 4)
  return xs.length ? xs.join(', ') : '-'
}

onMounted(() => {
  refresh()
  const t = setInterval(refresh, 5000)
  onUnmounted(() => clearInterval(t))
})

watch(windowMinutes, () => refresh())
</script>

<template>
  <div class="dashboard-grid span-all">
    <section class="panel" style="grid-column: 1 / -1">
      <div class="panel-header">
        <div class="panel-title">分析看板</div>
        <div class="panel-actions">
          <select
            v-model="windowMinutes"
            class="input"
            style="width: 120px; height: 32px; min-height: 0; padding: 0 8px"
          >
            <option :value="60">60m</option>
            <option :value="180">3h</option>
            <option :value="720">12h</option>
          </select>
          <span class="badge" :class="healthOk ? 'badge-ok' : 'badge-warn'">{{ healthOk ? '正常' : '依赖异常' }}</span>
        </div>
      </div>
      <div class="side-body">
        <div v-if="error" class="card">
          <div class="title" style="color: #fca5a5">Error</div>
          <div class="small">{{ error }}</div>
        </div>

        <div class="grid-metrics grid-metrics-6">
          <div class="metric">
            <div class="metric-k">窗口内分析</div>
            <div class="metric-v">{{ windowSignals.analysis }}</div>
            <div class="metric-h">{{ windowMinutes }}m 内 analysis_updated</div>
          </div>
          <div class="metric">
            <div class="metric-k">窗口内深度分析</div>
            <div class="metric-v">{{ windowSignals.deep }}</div>
            <div class="metric-h">{{ windowMinutes }}m 内 deep_analysis_updated</div>
          </div>
          <div class="metric">
            <div class="metric-k">轮询平均耗时</div>
            <div class="metric-v">{{ avgPollMs ? `${avgPollMs.toFixed(0)}ms` : '-' }}</div>
            <div class="metric-h">/status + /dashboard/summary</div>
          </div>
          <div class="metric">
            <div class="metric-k">articles（总）</div>
            <div class="metric-v">{{ totalCounts.articles ?? '-' }}</div>
            <div class="metric-h">当前 DB 总量</div>
          </div>
          <div class="metric">
            <div class="metric-k">analyses（总）</div>
            <div class="metric-v">{{ totalCounts.analyses ?? '-' }}</div>
            <div class="metric-h">当前 DB 总量</div>
          </div>
          <div class="metric">
            <div class="metric-k">signals（总）</div>
            <div class="metric-v">{{ totalCounts.signals ?? '-' }}</div>
            <div class="metric-h">当前 DB 总量</div>
          </div>
        </div>
      </div>
    </section>

    <section class="panel" style="grid-column: 1 / span 1">
      <div class="panel-header">
        <div class="panel-title">最新输出</div>
        <div class="panel-actions">
          <button class="btn btn-ghost" @click="refresh">刷新</button>
        </div>
      </div>
      <div class="side-body">
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th style="width: 168px">时间</th>
                <th style="width: 130px">信号</th>
                <th style="width: 120px">事件类型</th>
                <th style="width: 180px">Tickers</th>
                <th style="width: 140px">影响</th>
                <th>标题</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in summary?.recent || []" :key="r.kind + '|' + r.canonical_id + '|' + r.created_at">
                <td class="mono">{{ fmtTs(r.created_at) }}</td>
                <td>
                  <span class="pill">{{ r.kind }}</span>
                  <span v-if="r.deep_optimized_at" class="pill pill-ok" style="margin-left: 6px">deep</span>
                </td>
                <td class="mono">{{ r.event_type || '-' }}</td>
                <td class="mono">{{ tickersText(r) }}</td>
                <td class="mono">{{ fmtImpact(r) }}</td>
                <td>
                  <a v-if="r.url" :href="r.url" target="_blank" rel="noreferrer">{{ r.title || r.url }}</a>
                  <span v-else>{{ r.title || '-' }}</span>
                </td>
              </tr>
              <tr v-if="(summary?.recent || []).length === 0">
                <td colspan="6" class="muted" style="padding: 14px">暂无数据</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <aside class="panel">
      <div class="panel-header">
        <div class="panel-title">热点事件类型 ({{ windowMinutes }}m)</div>
      </div>
      <div class="side-body">
        <div class="list" style="max-height: 360px">
          <div v-for="x in summary?.top_event_types || []" :key="x.event_type" class="card">
            <div class="title">{{ x.event_type }}</div>
            <div class="small">count: <span class="mono">{{ x.count }}</span></div>
          </div>
          <div v-if="(summary?.top_event_types || []).length === 0" class="card">
            <div class="small muted">暂无数据</div>
          </div>
        </div>
      </div>
    </aside>

    <aside class="panel">
      <div class="panel-header">
        <div class="panel-title">提及最多个股 (近实时)</div>
      </div>
      <div class="side-body">
        <div class="pill-grid">
          <div v-for="x in summary?.top_tickers || []" :key="x.ts_code" class="pill">
            <span class="mono">{{ x.ts_code }}</span>
            <span class="pill-count mono">{{ x.count }}</span>
          </div>
          <div v-if="(summary?.top_tickers || []).length === 0" class="muted">暂无数据</div>
        </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.dashboard-grid {
  display: grid;
  grid-template-columns: 1fr 380px;
  gap: 14px;
}
@media (max-width: 980px) {
  .dashboard-grid {
    grid-template-columns: 1fr;
  }
}
</style>
