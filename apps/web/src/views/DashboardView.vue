<!-- Input: /dashboard/summary + /kg/graph（轮询去重） -->
<!-- Output: 分析结果看板（热点 + 最新输出 + 2D 知识图谱；防止轮询乱序） -->
<!-- Pos: 前端看板页（变更时同步更新以上注释与所属目录 FOLDER.md） -->

<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import KG3DGraph from '../components/KG3DGraph.vue'

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

const windowMinutes = ref(720)
const summary = ref<DashboardSummary | null>(null)
const error = ref<string | null>(null)
let refreshInFlight = false
let refreshQueued = false

async function api(path: string) {
  const res = await fetch(path, { cache: 'no-store' })
  const text = await res.text()
  let data: any = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { raw: text }
  }
  if (!res.ok) throw new Error((data && (data.error || data.detail)) || `HTTP ${res.status}`)
  return data
}

async function refresh() {
  if (refreshInFlight) {
    refreshQueued = true
    return
  }
  refreshInFlight = true
  try {
    error.value = null
    summary.value = await api(`/dashboard/summary?minutes=${windowMinutes.value}&limit=40`)
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
      <KG3DGraph :minutes="windowMinutes" :poll-ms="3000" />
    </section>

    <section class="panel" style="grid-column: 1 / span 1">
      <div class="panel-header">
        <div class="panel-title">最新输出</div>
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
          <button class="btn btn-ghost" @click="refresh">刷新</button>
        </div>
      </div>
      <div class="side-body">
        <div v-if="error" class="card">
          <div class="title" style="color: #fca5a5">Error</div>
          <div class="small">{{ error }}</div>
        </div>
        <div class="table-wrap">
          <table class="table recent-table">
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
                <td class="recent-title-cell">
                  <a
                    v-if="r.url"
                    class="recent-title"
                    :href="r.url"
                    target="_blank"
                    rel="noreferrer"
                    :title="r.title || r.url || ''"
                    >{{ r.title || r.url }}</a
                  >
                  <span v-else class="recent-title" :title="r.title || ''">{{ r.title || '-' }}</span>
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

.recent-table {
  table-layout: fixed;
}

.recent-title-cell {
  vertical-align: top;
}

.recent-title {
  display: block;
  width: 100%;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  direction: ltr;
}
</style>
