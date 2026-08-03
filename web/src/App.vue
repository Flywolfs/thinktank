<template>
  <el-container class="app-container">
    <!-- 顶部标题栏 -->
    <el-header class="app-header">
      <h1>🕵️ 智库情报系统</h1>
      <span class="subtitle">实体深挖 · 情报分析 · 知识图谱</span>
    </el-header>

    <el-main>
      <el-row :gutter="16">
        <!-- 左侧: 输入 + 进度 -->
        <el-col :span="10">
          <!-- 输入面板 -->
          <el-card shadow="never" class="panel">
            <template #header><b>🎯 发起调查</b></template>
            <el-form label-position="top" @submit.prevent="startInvestigation">
              <el-form-item label="实体名称">
                <el-input v-model="form.entity_name" placeholder="如：雷军 / 韩红 / 字节跳动"
                          clearable @keyup.enter="startInvestigation" />
              </el-form-item>
              <el-form-item label="关注方向 (hints)">
                <el-input v-model="form.hints" type="textarea" :rows="2"
                          placeholder="如：关注慈善基金会和捐款明细" />
              </el-form-item>
              <el-form-item label="调查意图 (可选)">
                <el-input v-model="form.goal" placeholder="如：调查人物商业版图" />
              </el-form-item>
              <el-form-item label="深挖轮数 (max_rounds)">
                <el-slider v-model="form.max_rounds" :min="1" :max="30" show-stops
                           style="width: 100%" />
              </el-form-item>
              <el-button type="primary" :loading="investigating" @click="startInvestigation">
                开始调查
              </el-button>
            </el-form>
          </el-card>

          <!-- 进度面板 -->
          <el-card shadow="never" class="panel" v-if="currentJob">
            <template #header>
              <b>📡 调查进度</b>
              <el-tag :type="statusTagType(currentJob.status)" size="small" style="float:right">
                {{ statusLabel(currentJob.status) }}
              </el-tag>
            </template>
            <el-timeline>
              <el-timeline-item v-for="(p, i) in currentJob.progress" :key="i"
                                :type="i === currentJob.progress.length - 1 ? 'primary' : ''"
                                :timestamp="formatTime(p.ts)">
                <b>{{ p.phase }}</b>
                <span v-if="p.detail"> — {{ p.detail }}</span>
              </el-timeline-item>
            </el-timeline>

            <!-- 多轮深挖记录 -->
            <template v-if="currentJob.investigation_rounds && currentJob.investigation_rounds.length">
              <el-divider>🔍 多轮深挖轨迹</el-divider>
              <el-timeline>
                <el-timeline-item v-for="(r, i) in currentJob.investigation_rounds" :key="i"
                                  :type="r.decide_continue ? 'warning' : 'success'"
                                  :timestamp="'第 ' + r.round + ' 轮'">
                  <b>搜索: {{ r.query }}</b>
                  <div>新增线索 {{ r.new_leads }} 条 / 队列共 {{ r.total_leads }} 条</div>
                  <div v-if="r.next_query" style="color: #e6a23c">
                    下一轮追: {{ r.next_query }}
                  </div>
                  <div v-if="!r.decide_continue" style="color: #67c23a">
                    ✓ 线索收敛，生成最终报告
                  </div>
                </el-timeline-item>
              </el-timeline>
            </template>

            <!-- HITL 操作 -->
            <template v-if="currentJob.status === 'review'">
              <el-divider>报告已就绪</el-divider>
              <el-button type="success" @click="approveJob">✅ 批准，构建知识图谱</el-button>
              <el-button type="info" @click="rejectJob">🗑️ 丢弃</el-button>
            </template>
          </el-card>

          <!-- 任务列表 -->
          <el-card shadow="never" class="panel">
            <template #header><b>📋 历史调查</b></template>
            <el-table :data="jobs" size="small" @row-click="selectJob" highlight-current-row>
              <el-table-column prop="entity_name" label="实体" width="100" />
              <el-table-column prop="status" label="状态" width="90">
                <template #default="{ row }">
                  <el-tag :type="statusTagType(row.status)" size="small">{{ row.status }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="created_at" label="时间">
                <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-col>

        <!-- 右侧: 报告 + 图谱 -->
        <el-col :span="14">
          <!-- 报告区 -->
          <el-card shadow="never" class="panel">
            <template #header><b>📄 分析报告</b></template>
            <div v-if="!reportMarkdown" class="empty-hint">
              发起调查后，这里将显示情报分析报告（逻辑链路 / 关键发现 / 时间线）
            </div>
            <div v-else class="report-body" v-html="renderMarkdown(reportMarkdown)"></div>
          </el-card>

          <!-- 图谱区 -->
          <el-card shadow="never" class="panel">
            <template #header>
              <b>🕸️ 知识图谱</b>
              <el-input v-model="graphQuery" placeholder="查询实体" size="small" style="width: 200px; float: right"
                        @keyup.enter="queryGraph" />
              <el-button size="small" style="float: right; margin-right: 8px" @click="queryGraph">查询</el-button>
            </template>
            <div ref="graphEl" class="graph-canvas"></div>
            <div v-if="!graphEntities.length" class="empty-hint">
              输入实体名查询知识图谱（Neo4j）
            </div>
          </el-card>
        </el-col>
      </el-row>
    </el-main>
  </el-container>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'

// ── 状态 ──────────────────────────────────────────────
const form = ref({ entity_name: '', hints: '', goal: '', max_rounds: 30 })
const investigating = ref(false)
const currentJob = ref(null)
const jobs = ref([])
const reportMarkdown = ref('')
const graphQuery = ref('')
const graphEntities = ref([])
const graphEl = ref(null)
let graphChart = null
let pollTimer = null

// ── 调查操作 ──────────────────────────────────────────
async function startInvestigation() {
  if (!form.value.entity_name.trim()) {
    ElMessage.warning('请输入实体名称')
    return
  }
  investigating.value = true
  reportMarkdown.value = ''
  try {
    const { data } = await axios.post('/api/investigate', {
      entity_name: form.value.entity_name.trim(),
      hints: form.value.hints.trim(),
      goal: form.value.goal.trim(),
      max_rounds: form.value.max_rounds,
    })
    await loadJob(data.job_id)
    // 轮询进度（调查是同步执行的，这里主要等 review 状态）
    startPolling(data.job_id)
  } catch (e) {
    ElMessage.error('调查启动失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    investigating.value = false
  }
}

function startPolling(jobId) {
  stopPolling()
  pollTimer = setInterval(async () => {
    try {
      const { data } = await axios.get(`/api/jobs/${jobId}`)
      currentJob.value = data
      if (data.status === 'review' && data.report) {
        reportMarkdown.value = data.report_markdown
        stopPolling()
      } else if (data.status === 'error') {
        ElMessage.error('调查失败: ' + (data.error || '未知错误'))
        stopPolling()
      }
    } catch (e) {
      stopPolling()
    }
  }, 2000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

async function approveJob() {
  if (!currentJob.value) return
  try {
    const { data } = await axios.post(`/api/jobs/${currentJob.value.id}/approve`)
    currentJob.value = data
    ElMessage.success('知识图谱构建完成')
    if (data.graph_built) {
      graphQuery.value = currentJob.value.entity_name
      queryGraph()
    }
  } catch (e) {
    ElMessage.error('批准失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function rejectJob() {
  if (!currentJob.value) return
  try {
    const { data } = await axios.post(`/api/jobs/${currentJob.value.id}/reject`)
    currentJob.value = data
    ElMessage.info('已丢弃')
  } catch (e) {
    ElMessage.error('操作失败')
  }
}

// ── 任务列表 ──────────────────────────────────────────
async function loadJobs() {
  try {
    const { data } = await axios.get('/api/jobs')
    jobs.value = data.jobs
  } catch (e) { /* 忽略 */ }
}

async function selectJob(row) {
  try {
    const { data } = await axios.get(`/api/jobs/${row.id}`)
    currentJob.value = data
    if (data.report) {
      reportMarkdown.value = data.report_markdown
      graphQuery.value = data.entity_name
      queryGraph()
    }
  } catch (e) { /* 忽略 */ }
}

async function loadJob(jobId) {
  try {
    const { data } = await axios.get(`/api/jobs/${jobId}`)
    currentJob.value = data
    if (data.report) reportMarkdown.value = data.report_markdown
  } catch (e) { /* 忽略 */ }
}

// ── 图谱 ──────────────────────────────────────────────
async function queryGraph() {
  if (!graphQuery.value.trim()) return
  try {
    const { data } = await axios.get(`/api/graph/subgraph/${encodeURIComponent(graphQuery.value.trim())}`)
    graphEntities.value = data.entities || []
    renderGraph(data.entities || [])
  } catch (e) {
    ElMessage.error('图谱查询失败')
  }
}

function renderGraph(entities) {
  if (!graphChart && graphEl.value) {
    graphChart = echarts.init(graphEl.value)
  }
  if (!graphChart) return

  const nodes = entities.map(e => ({
    id: e.name,
    name: e.name,
    category: e.type || 'entity',
    symbolSize: 30,
  }))
  const links = []
  for (const e of entities) {
    for (const rel of e.relations || []) {
      const target = rel.other || rel.target
      if (target && nodes.some(n => n.id === target)) {
        links.push({ source: e.name, target, label: { show: true, formatter: rel.rel || rel.type } })
      }
    }
  }

  graphChart.setOption({
    tooltip: {},
    legend: { data: [...new Set(nodes.map(n => n.category))], textStyle: { fontSize: 10 } },
    series: [{
      type: 'graph',
      layout: 'force',
      roam: true,
      draggable: true,
      data: nodes,
      links: links,
      categories: [...new Set(nodes.map(n => n.category))].map(c => ({ name: c })),
      label: { show: true, fontSize: 10 },
      force: { repulsion: 200, edgeLength: 80 },
    }],
  })
}

// ── 工具 ──────────────────────────────────────────────
function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function statusLabel(s) {
  return { pending: '等待中', running: '执行中', review: '待审阅', approved: '已构建', rejected: '已丢弃', error: '失败' }[s] || s
}

function statusTagType(s) {
  return { pending: 'info', running: 'primary', review: 'warning', approved: 'success', rejected: 'info', error: 'danger' }[s] || 'info'
}

function renderMarkdown(md) {
  if (!md) return ''
  // 极简 Markdown 渲染（标题/粗体/列表/引用）
  return md
    .replace(/^# (.*)$/gm, '<h2>$1</h2>')
    .replace(/^### (.*)$/gm, '<h4>$1</h4>')
    .replace(/^## (.*)$/gm, '<h3>$1</h3>')
    .replace(/\*\*(.*?)\*\*/g, '<b>$1</b>')
    .replace(/^- (.*)$/gm, '<li>$1</li>')
    .replace(/^> (.*)$/gm, '<blockquote>$1</blockquote>')
    .replace(/\n/g, '<br/>')
}

// ── 生命周期 ──────────────────────────────────────────
onMounted(() => {
  loadJobs()
  window.addEventListener('resize', () => graphChart && graphChart.resize())
})

onBeforeUnmount(() => {
  stopPolling()
  if (graphChart) graphChart.dispose()
})
</script>

<style>
.app-container { min-height: 100vh; background: #f5f7fa; }
.app-header { background: #1f2d3d; color: #fff; display: flex; align-items: center; gap: 16px; }
.app-header h1 { margin: 0; font-size: 20px; }
.app-header .subtitle { color: #8fa3b8; font-size: 12px; }
.panel { margin-bottom: 16px; }
.empty-hint { color: #999; text-align: center; padding: 30px 0; font-size: 13px; }
.report-body { font-size: 13px; line-height: 1.7; max-height: 500px; overflow-y: auto; }
.report-body h2 { font-size: 18px; border-bottom: 1px solid #eee; padding-bottom: 6px; }
.report-body h3 { font-size: 15px; margin-top: 16px; }
.report-body blockquote { border-left: 3px solid #409eff; margin: 6px 0; padding: 4px 12px; background: #f0f7ff; color: #555; }
.graph-canvas { width: 100%; height: 420px; }
</style>
