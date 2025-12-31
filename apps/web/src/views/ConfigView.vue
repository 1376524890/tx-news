<!-- Input: /api/config（cookie 用户配置） -->
<!-- Output: 在线 LLM 配置界面（base_url/model/api_key），用于按用户分摊成本 -->
<!-- Pos: 配置页（public: /config；admin build: 8001 /）（变更时同步更新以上注释与所属目录 FOLDER.md） -->

<script setup lang="ts">
import { onMounted, ref } from 'vue'

type ConfigResp = {
  uid: string
  configured: boolean
  config: null | {
    base_url: string
    model: string
    api_key_masked: string
    api_key_set: boolean
    updated_at: string
  }
}

const uid = ref<string>('')
const status = ref<string>('加载中…')
const saved = ref<ConfigResp['config']>(null)

const baseUrl = ref<string>('https://dashscope.aliyuncs.com/compatible-mode/v1')
const model = ref<string>('qwen3-max')
const apiKey = ref<string>('')

async function apiGet(): Promise<ConfigResp> {
  const res = await fetch('/api/config', { credentials: 'include' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

async function apiPost(path: string, body?: any): Promise<ConfigResp> {
  const res = await fetch(path, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

async function refresh() {
  try {
    const r = await apiGet()
    uid.value = r.uid
    saved.value = r.config
    status.value = r.configured ? '已配置' : '未配置'
    if (r.config) {
      baseUrl.value = r.config.base_url || baseUrl.value
      model.value = r.config.model || model.value
    }
  } catch (e: any) {
    status.value = `加载失败：${String(e)}`
  }
}

async function save() {
  status.value = '保存中…'
  try {
    const r = await apiPost('/api/config', {
      base_url: baseUrl.value.trim(),
      model: model.value.trim(),
      api_key: apiKey.value.trim()
    })
    uid.value = r.uid
    saved.value = r.config
    apiKey.value = ''
    status.value = '保存成功'
  } catch (e: any) {
    status.value = `保存失败：${String(e)}`
  }
}

async function clear() {
  status.value = '清除中…'
  try {
    const r = await apiPost('/api/config/clear')
    uid.value = r.uid
    saved.value = null
    apiKey.value = ''
    status.value = '已清除'
  } catch (e: any) {
    status.value = `清除失败：${String(e)}`
  }
}

onMounted(() => {
  refresh()
})
</script>

<template>
  <div class="dashboard-grid span-all">
    <section class="panel" style="grid-column: 1 / -1">
      <div class="panel-header">
        <div class="panel-title">LLM 配置</div>
        <div class="panel-actions">
          <a class="btn btn-ghost" href="http://localhost:8000/" target="_blank" rel="noreferrer">打开对话页</a>
          <button class="btn btn-ghost" type="button" @click="refresh">刷新</button>
        </div>
      </div>

      <div class="panel-body" style="padding: 12px 14px">
        <div class="muted" style="margin-bottom: 10px">
          状态：<span class="mono">{{ status }}</span>
          <span v-if="uid" class="muted"> · uid=<span class="mono">{{ uid }}</span></span>
          <span v-if="saved?.api_key_set" class="muted">
            · key=<span class="mono">{{ saved?.api_key_masked }}</span>
          </span>
        </div>

        <div class="kv" style="grid-template-columns: 160px 1fr; gap: 8px 12px; align-items: center">
          <div class="k">Base URL</div>
          <div class="v">
            <input v-model="baseUrl" class="input" style="height: 36px; min-height: 0" />
          </div>

          <div class="k">Model</div>
          <div class="v">
            <input v-model="model" class="input" style="height: 36px; min-height: 0" />
          </div>

          <div class="k">API Key</div>
          <div class="v">
            <input v-model="apiKey" class="input" style="height: 36px; min-height: 0" type="password" />
            <div class="muted" style="margin-top: 6px">
              仅用于本用户的对话请求；保存后浏览器不会回显明文。
            </div>
          </div>
        </div>

        <div style="margin-top: 14px; display: flex; gap: 10px">
          <button class="btn" type="button" @click="save" :disabled="!apiKey.trim()">保存</button>
          <button class="btn btn-ghost" type="button" @click="clear">清除</button>
        </div>
      </div>
    </section>
  </div>
</template>
