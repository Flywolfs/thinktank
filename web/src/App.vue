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
              <el-form-item label="Plan 决策模式">
                <el-radio-group v-model="form.plan_provider">
                  <el-radio-button value="auto">auto (Hermes优先)</el-radio-button>
                  <el-radio-button value="hermes">hermes (Docker)</el-radio-button>
                  <el-radio-button value="local">local (自研)</el-radio-button>
                </el-radio-group>
                <div class="field-hint">auto: Hermes 生成 plan，失败自动降级自研 5 步推理</div>
              </el-form-item>
              <el-form-item label="中途可调整方向">
                <el-switch v-model="form.adjustable" active-text="是" inactive-text="否" />
                <div class="field-hint">开启后每轮暂停，可输入指令调整调查方向（如"专注资金链"）</div>
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

            <!-- P2.3 中途调整方向 -->
            <template v-if="currentJob.status === 'running' && currentJob.adjustable">
              <el-divider>🔀 调整调查方向</el-divider>
              <el-input v-model="adjustInstruction" type="textarea" :rows="2"
                        placeholder="如：别追争议了，专注资金链" />
              <div style="margin-top: 8px">
                <el-button type="warning" size="small" @click="adjustJob">调整方向</el-button>
                <el-button size="small" @click="continueJob">继续（不调整）</el-button>
                <el-button type="danger" size="small" @click="stopJob">停止</el-button>
              </div>
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

          <!-- 动态工具（Phase C） -->
          <el-card shadow="never" class="panel">
            <template #header><b>🛠️ 动态工具 (Phase C)</b></template>
            <el-form label-position="top" @submit.prevent="generateDynamicTool">
              <el-form-item label="需求描述">
                <el-input v-model="dynForm.requirement" type="textarea" :rows="2"
                          placeholder="如：抓取某某网站的公司新闻" />
              </el-form-item>
              <el-form-item label="审批模式">
                <el-radio-group v-model="dynForm.approval_mode">
                  <el-radio-button value="manual">manual (人工)</el-radio-button>
                  <el-radio-button value="auto">auto (LLM审查)</el-radio-button>
                </el-radio-group>
                <div class="field-hint">
                  auto: LLM 安全审查代码，通过自动注册；发现问题反馈 Hermes 修改（最多 3 轮）
                </div>
              </el-form-item>
              <el-button type="primary" size="small" :loading="generating" @click="generateDynamicTool">
                生成爬虫
              </el-button>
            </el-form>

            <el-divider v-if="dynTools.length">待审批 / 已注册</el-divider>
            <div v-for="t in dynTools" :key="t.name" class="dyn-tool-row">
              <div style="flex: 1">
                <b>{{ t.name }}</b>
                <el-tag :type="dynStatusType(t.status)" size="small" style="margin-left: 6px">
                  {{ dynStatusLabel(t.status) }}
                </el-tag>
                <el-tag v-if="t.approval_mode === 'auto'" type="info" size="small" style="margin-left: 4px">
                  auto{{ t.review_rounds ? '·'+t.review_rounds+'轮' : '' }}
                </el-tag>
                <div class="field-hint">{{ t.description }}</div>
                <div v-if="t.error" class="dyn-error">{{ t.error }}</div>
              </div>
              <div v-if="t.status === 'pending'" style="white-space: nowrap">
                <el-button size="small" type="primary" @click="approveDynamicTool(t)">批准</el-button>
                <el-button size="small" @click="viewDynamicCode(t)">源码</el-button>
              </div>
              <div v-else style="white-space: nowrap">
                <el-button size="small" @click="viewDynamicCode(t)">源码</el-button>
              </div>
            </div>
            <div v-if="!dynTools.length" class="empty-hint">还没有动态工具</div>
          </el-card>

          <!-- 数据源健康状态 -->
          <el-card shadow="never" class="panel">
            <template #header>
              <b>📡 数据源状态</b>
              <el-button size="small" style="float:right" @click="loadProviderHealth" :loading="healthLoading">
                刷新
              </el-button>
              <el-button size="small" type="warning" style="float:right; margin-right: 8px"
                         @click="deepHealthCheck" :loading="deepHealthLoading">
                深度检测
              </el-button>
            </template>
            <div v-if="!providerHealth.length" class="empty-hint">点击刷新查看各数据源状态</div>
            <div v-else class="health-list">
              <div v-for="p in providerHealth" :key="p.name" class="health-row">
                <span class="health-dot" :class="'dot-' + p.status"></span>
                <b style="width: 90px">{{ sourceLabel(p.name) }}</b>
                <el-tag size="small" :type="p.status === 'ok' ? 'success' : (p.status === 'error' ? 'danger' : 'warning')">
                  {{ p.status === 'ok' ? '正常' : (p.status === 'error' ? '异常' : '警告') }}
                </el-tag>
                <span class="field-hint" style="flex:1">{{ p.message }}</span>
                <span class="field-hint" style="width: 50px; text-align: right">{{ p.duration_ms }}ms</span>
              </div>
              <div class="field-hint" style="margin-top: 6px">
                深度检测: 实际搜索验证登录态（慢，每源 10-90s）
              </div>
            </div>
          </el-card>
        </el-col>

        <!-- 右侧: 计划 + 报告 + 图谱 -->
        <el-col :span="14">
          <!-- 调查计划（P4.1 可视化） -->
          <el-card shadow="never" class="panel" v-if="currentJob && currentJob.plan && currentJob.plan.length">
            <template #header>
              <b>🗺️ 调查计划</b>
              <el-tag size="small" style="float:right" type="info">
                {{ currentJob.plan.length }} 个维度
              </el-tag>
            </template>
            <el-timeline>
              <el-timeline-item v-for="(dim, i) in currentJob.plan" :key="i"
                                :type="dim.priority === 'high' ? 'primary' : (dim.priority === 'medium' ? 'warning' : 'info')">
                <b>{{ i + 1 }}. {{ dim.name }}</b>
                <el-tag size="small" :type="dim.priority === 'high' ? 'danger' : (dim.priority === 'medium' ? 'warning' : 'info')"
                        style="margin-left: 6px">{{ dim.priority }}</el-tag>
                <div class="field-hint">{{ dim.methodology_source || '' }}</div>
                <div class="field-hint" v-if="dim.rationale">{{ dim.rationale }}</div>
                <div v-if="dim.queries && dim.queries.length" style="margin-top: 4px">
                  <el-tag v-for="(q, qi) in dim.queries" :key="qi" size="small" type="info"
                          effect="plain" style="margin-right: 4px">{{ q }}</el-tag>
                </div>
              </el-timeline-item>
            </el-timeline>
          </el-card>

          <!-- 报告区 -->
          <el-card shadow="never" class="panel">
            <template #header><b>📄 分析报告</b></template>
            <div v-if="!reportMarkdown" class="empty-hint">
              发起调查后，这里将显示情报分析报告（逻辑链路 / 关键发现 / 时间线）
            </div>
            <div v-else class="report-body" v-html="renderMarkdown(reportMarkdown)"></div>
          </el-card>

          <!-- 日志查看器（P4.1） -->
          <el-card shadow="never" class="panel" v-if="currentJob && currentJob.id">
            <template #header>
              <b>📜 调查日志</b>
              <el-button size="small" style="float:right" @click="loadLogs">刷新</el-button>
              <el-radio-group v-model="logLevel" size="small" style="float:right; margin-right: 8px">
                <el-radio-button value="">全部</el-radio-button>
                <el-radio-button value="info">info</el-radio-button>
                <el-radio-button value="debug">debug</el-radio-button>
              </el-radio-group>
            </template>
            <div v-if="!logs.length" class="empty-hint">暂无日志</div>
            <div v-else class="log-list">
              <div v-for="(log, i) in logs" :key="i" class="log-row" :class="'log-' + (log.level || 'info')">
                <span class="log-time">{{ formatTime(log.ts) }}</span>
                <el-tag size="small" :type="log.level === 'error' ? 'danger' : (log.level === 'debug' ? 'info' : 'primary')"
                        style="margin-right: 4px">{{ log.level || 'info' }}</el-tag>
                <b>{{ log.event }}</b>
                <span class="field-hint" v-if="log.node"> [{{ log.node }}]</span>
                <div class="log-detail" v-if="log.detail">{{ shortDetail(log.detail) }}</div>
                <div class="log-code" v-if="log.code">{{ log.code.file }}:{{ log.code.line }}:{{ log.code.func }}</div>
              </div>
            </div>
          </el-card>

          <!-- 图谱区 -->
          <el-card shadow="never" class="panel">
            <template #header>
              <b>🕸️ 知识图谱</b>
              <el-input v-model="graphQuery" placeholder="查询实体" size="small" style="width: 160px; float: right"
                        @keyup.enter="queryGraph" />
              <el-select v-model="graphDepth" size="small" style="width: 80px; float: right; margin-right: 8px"
                         @change="queryGraph">
                <el-option :value="1" label="1跳" />
                <el-option :value="2" label="2跳" />
                <el-option :value="3" label="3跳" />
              </el-select>
              <el-button size="small" style="float: right; margin-right: 8px" @click="queryGraph">查询</el-button>
            </template>
            <div ref="graphEl" class="graph-canvas"></div>
            <div v-if="!graphEntities.length" class="empty-hint">
              输入实体名查询知识图谱（Neo4j），可调跳数展开；点击节点可继续深挖
            </div>

            <!-- P4.2 深挖面板 -->
            <div v-if="deepDiveTarget" class="deepdive-panel">
              <div class="deepdive-title">
                🔍 继续深挖: <b>{{ deepDiveTarget }}</b>
                <el-tag v-if="deepDiveStatus === 'investigated'" type="warning" size="small" style="margin-left: 8px">
                  已挖过 {{ deepDiveCount || 0 }} 次
                </el-tag>
                <el-tag v-else type="success" size="small" style="margin-left: 8px">未调查</el-tag>
              </div>
              <el-form label-position="top" size="small">
                <el-form-item label="关注方向 (hints，自动继承父关系)">
                  <el-input v-model="deepDiveHints" :rows="1" />
                </el-form-item>
                <el-form-item label="深挖轮数">
                  <el-slider v-model="deepDiveRounds" :min="1" :max="30" style="width: 100%" />
                </el-form-item>
                <el-form-item>
                  <el-checkbox v-model="deepDiveAutoReworth">
                    遇到成环时由系统决定是否重挖
                  </el-checkbox>
                  <div class="field-hint">开: 强新线索自动重挖(上限3次)；关: 每次撞回都询问</div>
                </el-form-item>
                <el-button type="primary" size="small" :loading="deepDiving" @click="startDeepDive">
                  开始深挖
                </el-button>
                <el-button size="small" @click="deepDiveTarget = null">取消</el-button>
              </el-form>
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
const form = ref({ entity_name: '', hints: '', goal: '', max_rounds: 30, plan_provider: 'auto', adjustable: false })
const investigating = ref(false)
const currentJob = ref(null)
const jobs = ref([])
const reportMarkdown = ref('')
const graphQuery = ref('')
const graphEntities = ref([])
const graphEl = ref(null)
const graphDepth = ref(1)
const logs = ref([])
const logLevel = ref('')
// P4.2 深挖
const deepDiveTarget = ref('')
const deepDiveHints = ref('')
const deepDiveRounds = ref(6)
const deepDiveAutoReworth = ref(true)
const deepDiveStatus = ref('')      // investigated / new
const deepDiveCount = ref(0)
const deepDiving = ref(false)
// 数据源健康
const providerHealth = ref([])
const healthLoading = ref(false)
const deepHealthLoading = ref(false)
let graphChart = null
let pollTimer = null

// P2.3 调整方向
const adjustInstruction = ref('')

// 动态工具状态
const dynForm = ref({ requirement: '', approval_mode: 'auto' })
const dynTools = ref([])
const generating = ref(false)

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
      plan_provider: form.value.plan_provider,
      adjustable: form.value.adjustable,
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
      if (data.id && data.status !== 'error') loadLogs()
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

// ── P2.3 中途调整方向 ─────────────────────────────────
async function adjustJob() {
  if (!currentJob.value) return
  if (!adjustInstruction.value.trim()) {
    ElMessage.warning('请输入调整指令')
    return
  }
  try {
    const { data } = await axios.post(`/api/jobs/${currentJob.value.id}/adjust`, {
      instruction: adjustInstruction.value.trim(),
    })
    currentJob.value = data
    adjustInstruction.value = ''
    ElMessage.success('已调整方向')
    startPolling(data.id)
  } catch (e) {
    ElMessage.error('调整失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function continueJob() {
  if (!currentJob.value) return
  try {
    const { data } = await axios.post(`/api/jobs/${currentJob.value.id}/continue`)
    currentJob.value = data
    ElMessage.info('继续调查')
    startPolling(data.id)
  } catch (e) {
    ElMessage.error('操作失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function stopJob() {
  if (!currentJob.value) return
  try {
    const { data } = await axios.post(`/api/jobs/${currentJob.value.id}/stop`)
    currentJob.value = data
    ElMessage.info('已停止')
  } catch (e) {
    ElMessage.error('操作失败: ' + (e.response?.data?.detail || e.message))
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
    if (data.id) loadLogs()
  } catch (e) { /* 忽略 */ }
}

// ── P4.1 日志查看器 ───────────────────────────────────
async function loadLogs() {
  if (!currentJob.value?.id) return
  try {
    const { data } = await axios.get(`/api/jobs/${currentJob.value.id}/logs`, {
      params: { limit: 200, level: logLevel.value },
    })
    logs.value = data.records || []
  } catch (e) { /* 日志可能不存在 */ }
}

function shortDetail(detail) {
  if (!detail) return ''
  if (typeof detail === 'string') return detail.length > 120 ? detail.slice(0, 120) + '…' : detail
  try {
    const s = JSON.stringify(detail)
    return s.length > 150 ? s.slice(0, 150) + '…' : s
  } catch (e) { return String(detail) }
}

// ── 动态工具 (Phase C) ───────────────────────────────
async function generateDynamicTool() {
  if (!dynForm.value.requirement.trim()) {
    ElMessage.warning('请输入需求描述')
    return
  }
  generating.value = true
  try {
    const { data } = await axios.post('/api/tools/dynamic/generate', {
      requirement: dynForm.value.requirement.trim(),
      approval_mode: dynForm.value.approval_mode,
    })
    ElMessage.success(data.message || '已生成')
    dynForm.value.requirement = ''
    loadDynamicTools()
  } catch (e) {
    ElMessage.error('生成失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    generating.value = false
  }
}

async function loadDynamicTools() {
  try {
    const { data } = await axios.get('/api/tools/dynamic')
    dynTools.value = data.tools || []
  } catch (e) { /* 忽略 */ }
}

async function approveDynamicTool(t) {
  try {
    const { data } = await axios.post(`/api/tools/dynamic/${t.name}/approve`)
    ElMessage.success(data.message || '已批准')
    loadDynamicTools()
  } catch (e) {
    ElMessage.error('审批失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function viewDynamicCode(t) {
  try {
    const { data } = await axios.get(`/api/tools/dynamic/${t.name}`)
    // 弹出源码查看（简化为 alert 太长，用 ElMessageBox）
    const { ElMessageBox } = await import('element-plus')
    ElMessageBox.alert(
      `<pre style="max-height:400px;overflow:auto;font-size:12px;white-space:pre-wrap">${escapeHtml(data.code)}</pre>`,
      `源码: ${data.name}`,
      { dangerouslyUseHTMLString: true, customClass: 'code-dialog' }
    )
  } catch (e) {
    ElMessage.error('获取源码失败')
  }
}

function dynStatusLabel(s) {
  return { pending: '待审批', approved: '已注册', rejected: '已拒绝' }[s] || s
}
function dynStatusType(s) {
  return { pending: 'warning', approved: 'success', rejected: 'info' }[s] || 'info'
}
function escapeHtml(s) {
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

// ── 数据源健康状态 ────────────────────────────────────
async function loadProviderHealth() {
  healthLoading.value = true
  try {
    const { data } = await axios.get('/api/health/providers')
    providerHealth.value = data.providers || []
  } catch (e) {
    ElMessage.error('数据源状态获取失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    healthLoading.value = false
  }
}

async function deepHealthCheck() {
  deepHealthLoading.value = true
  try {
    const { data } = await axios.get('/api/health/providers', { params: { deep: true } })
    providerHealth.value = data.providers || []
    ElMessage.success('深度检测完成')
  } catch (e) {
    ElMessage.error('深度检测失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    deepHealthLoading.value = false
  }
}

function sourceLabel(name) {
  return {
    wikipedia: '维基百科', serper: 'Google', bilibili: 'B站',
    enterprise: '企业信息', zhihu: '知乎', xiaohongshu: '小红书', weibo: '微博',
  }[name] || name
}

// ── 图谱 ──────────────────────────────────────────────
async function queryGraph(name) {
  const q = name || graphQuery.value
  if (!q?.trim()) return
  if (name) graphQuery.value = name
  try {
    const { data } = await axios.get(`/api/graph/subgraph/${encodeURIComponent(q.trim())}`,
      { params: { depth: graphDepth.value } })
    graphEntities.value = data.entities || []
    renderGraph(data.entities || [], q.trim())
  } catch (e) {
    ElMessage.error('图谱查询失败')
  }
}

function renderGraph(entities, centerName) {
  if (!graphChart && graphEl.value) {
    graphChart = echarts.init(graphEl.value)
  }
  if (!graphChart) return

  const nodes = entities.map(e => ({
    id: e.name,
    name: e.name,
    category: e.type || 'entity',
    symbolSize: e.name === centerName ? 40 : 30,
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
      // P4.1: 点击节点展开该实体子图
      emphasis: { focus: 'adjacency' },
    }],
  })
  // P4.1/4.2: 单击节点 → 深挖面板（查实体状态）；双击 → 展开子图
  graphChart.off('click')
  graphChart.off('dblclick')
  graphChart.on('click', params => {
    if (params.dataType === 'node') {
      openDeepDive(params.data.name)
    }
  })
  graphChart.on('dblclick', params => {
    if (params.dataType === 'node' && params.data.name !== centerName) {
      queryGraph(params.data.name)
    }
  })
}

// ── P4.2 深挖 ─────────────────────────────────────────
async function openDeepDive(name) {
  deepDiveTarget.value = name
  deepDiveHints.value = currentJob.value?.hints || ''
  deepDiveRounds.value = 6
  deepDiveAutoReworth.value = true
  // 查实体状态（已挖过？）
  try {
    const { data } = await axios.get(`/api/graph/entity/${encodeURIComponent(name)}`)
    if (data.found && data.entity.investigated) {
      deepDiveStatus.value = 'investigated'
      deepDiveCount.value = data.entity.investigated_count || 1
    } else {
      deepDiveStatus.value = 'new'
      deepDiveCount.value = 0
    }
  } catch (e) {
    deepDiveStatus.value = 'new'
    deepDiveCount.value = 0
  }
}

async function startDeepDive() {
  if (!deepDiveTarget.value || !currentJob.value) return
  deepDiving.value = true
  try {
    const { data } = await axios.post('/api/investigate', {
      entity_name: deepDiveTarget.value,
      hints: deepDiveHints.value.trim(),
      goal: `图谱递归深挖: 从 ${currentJob.value.entity_name} 调查继续`,
      max_rounds: deepDiveRounds.value,
      plan_provider: 'auto',
      adjustable: false,
      parent_job_id: currentJob.value.id,
      parent_entity: currentJob.value.entity_name,
      parent_relation: '图谱关联',
    })
    ElMessage.success('已发起深挖调查')
    deepDiveTarget.value = null
    const { data: jobData } = await axios.get(`/api/jobs/${data.job_id}`)
    currentJob.value = jobData
    startPolling(data.job_id)
  } catch (e) {
    ElMessage.error('深挖失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    deepDiving.value = false
  }
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
  loadDynamicTools()
  loadProviderHealth()
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
.field-hint { color: #999; font-size: 12px; line-height: 1.4; margin-top: 4px; }
.dyn-tool-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px dashed #eee; }
.dyn-error { color: #f56c6c; font-size: 12px; margin-top: 2px; }
.code-dialog pre { margin: 0; }
.log-list { max-height: 320px; overflow-y: auto; font-size: 12px; }
.log-row { padding: 4px 6px; border-bottom: 1px solid #f5f7fa; line-height: 1.5; }
.log-row:hover { background: #fafafa; }
.log-time { color: #999; margin-right: 6px; font-family: monospace; }
.log-detail { color: #555; word-break: break-all; margin-top: 2px; }
.log-code { color: #b0b3b8; font-family: monospace; font-size: 11px; margin-top: 2px; }
.log-error .log-detail { color: #f56c6c; }
.log-debug .log-detail { color: #999; }
.deepdive-panel { border-top: 1px solid #eee; padding-top: 12px; margin-top: 8px; background: #fafbfc; padding: 12px; border-radius: 6px; }
.deepdive-title { margin-bottom: 8px; font-size: 14px; }
.health-list { max-height: 260px; overflow-y: auto; }
.health-row { display: flex; align-items: center; gap: 8px; padding: 5px 4px; border-bottom: 1px solid #f5f7fa; }
.health-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
.dot-ok { background: #67c23a; }
.dot-error { background: #f56c6c; }
.dot-warning { background: #e6a23c; }
.dot-unknown { background: #909399; }
.dot-disabled { background: #c0c4cc; }
.report-body { font-size: 13px; line-height: 1.7; max-height: 500px; overflow-y: auto; }
.report-body h2 { font-size: 18px; border-bottom: 1px solid #eee; padding-bottom: 6px; }
.report-body h3 { font-size: 15px; margin-top: 16px; }
.report-body blockquote { border-left: 3px solid #409eff; margin: 6px 0; padding: 4px 12px; background: #f0f7ff; color: #555; }
.graph-canvas { width: 100%; height: 420px; }
</style>
