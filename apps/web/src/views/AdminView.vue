<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'

const status = ref<any>({})
const pipeline = ref<any>({})
const procs = ref<any[]>([])
const tushare = ref<any>({})
const logs = ref('')
const windowMinutes = ref(60)
const logName = ref('api')
const logFollow = ref(true)

const deps = ref<any[]>([])

async function api(path: string) {
    const res = await fetch(path)
    return res.json()
}

async function refreshAll() {
    try {
        const st = await api("/admin/status")
        status.value = st
        deps.value = Object.entries(st.dependencies || {}).map(([k, v]: any) => ({ name: k, ...v }))
        
        pipeline.value = await api(`/admin/pipeline?minutes=${windowMinutes.value}`)
        
        const procData = await api("/admin/processes")
        procs.value = procData.processes || []
        
        const tsData = await api("/admin/masterdata")
        tushare.value = tsData.stock_basic_cache || {}
        
        refreshLog()
    } catch (e) {
        console.error(e)
    }
}

async function refreshLog() {
    try {
        const data = await api(`/admin/logs/${logName.value}?n=200`)
        logs.value = data.tail || ''
        if (logFollow.value) {
            // Scroll logic here if needed
        }
    } catch (e) {
        logs.value = `Error: ${e}`
    }
}

onMounted(() => {
    refreshAll()
    const t = setInterval(refreshAll, 5000)
    onUnmounted(() => clearInterval(t))
})
</script>

<template>
  <div class="admin-grid">
      <section class="panel">
        <div class="panel-header">
          <div class="panel-title">依赖健康</div>
          <div class="panel-actions">
              <span class="badge" :class="deps.every(d => d.ok) ? 'badge-ok' : 'badge-warn'">{{ deps.every(d => d.ok) ? '正常' : '异常' }}</span>
          </div>
        </div>
        <div class="side-body">
          <div class="list">
              <div v-for="d in deps" :key="d.name" class="card">
                  <div class="title" :style="{color: d.ok ? '#86efac' : '#fca5a5'}">{{ d.name }}</div>
                  <div class="small">{{ d.ok ? 'ok' : d.error }}</div>
              </div>
          </div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-header">
          <div class="panel-title">进程状态</div>
        </div>
        <div class="side-body">
          <div class="list">
              <div v-for="p in procs" :key="p.name" class="card">
                  <div class="title" :style="{color: p.alive ? '#86efac' : '#fca5a5'}">{{ p.name }}</div>
                  <div class="small">
                      <div>pid: {{ p.pid }}</div>
                      <div>{{ p.cmdline }}</div>
                  </div>
              </div>
          </div>
        </div>
      </section>
      
      <section class="panel" style="grid-column: 1 / -1">
        <div class="panel-header">
          <div class="panel-title">抓取/分析进度 ({{ windowMinutes }}m)</div>
           <div class="panel-actions">
            <select v-model="windowMinutes" class="input" style="width:120px; height:32px; min-height:0; padding:0 8px;">
                <option :value="30">30m</option>
                <option :value="60">60m</option>
                <option :value="180">3h</option>
            </select>
           </div>
        </div>
        <div class="side-body">
            <div class="kv">
                <div class="k">raw_documents</div> <div class="v">{{ (pipeline.counts || {}).raw_documents }}</div>
                <div class="k">analyses</div> <div class="v">{{ (pipeline.counts || {}).analyses }}</div>
                <div class="k">lag (s)</div> <div class="v">{{ pipeline.lag_seconds_raw_minus_analysis?.toFixed(1) || '-' }}</div>
            </div>
            
            <div class="list">
                <div v-if="pipeline.latest?.raw_document" class="card">
                    <div class="title">Latest Raw</div>
                    <div class="small">
                        {{ pipeline.latest.raw_document.source_id }} · {{ pipeline.latest.raw_document.url }}
                    </div>
                </div>
            </div>
        </div>
      </section>

      <section class="panel" style="grid-column: 1 / -1">
        <div class="panel-header">
            <div class="panel-title">日志</div>
            <div class="panel-actions">
                <select v-model="logName" @change="refreshLog" class="input" style="width:120px; height:32px; min-height:0; padding:0 8px;">
                    <option value="api">api</option>
                    <option value="collector">collector</option>
                    <option value="celery_worker">worker</option>
                    <option value="nats_bridge">bridge</option>
                </select>
            </div>
        </div>
        <div class="side-body">
            <div class="log-view wrap">{{ logs }}</div>
        </div>
      </section>
  </div>
</template>

<style scoped>
.admin-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
}
@media (max-width: 900px) {
    .admin-grid { grid-template-columns: 1fr; }
}
</style>
