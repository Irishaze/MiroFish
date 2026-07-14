<template>
  <div class="simulation-panel">
    <!-- Top Control Bar -->
    <div class="control-bar">
      <div class="status-group">
        <!-- Assay Track -->
        <div class="platform-status assay" :class="{ active: isRunning && currentTrack === 'assay', completed: assayTrackDone }">
          <div class="platform-header">
            <svg class="platform-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line>
            </svg>
            <span class="platform-name">{{ $t('step3.assayTrack') }}</span>
            <span v-if="assayTrackDone" class="status-badge">
              <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="3">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
            </span>
          </div>
          <div class="platform-stats">
            <span class="stat">
              <span class="stat-label">{{ $t('step3.round') }}</span>
              <span class="stat-value mono">{{ runStatus.current_round >= 1 ? 1 : 0 }}<span class="stat-total">/1</span></span>
            </span>
            <span class="stat">
              <span class="stat-label">{{ $t('step3.acts') }}</span>
              <span class="stat-value mono">{{ assayActionsCount }}</span>
            </span>
          </div>
          <div class="actions-tooltip">
            <div class="tooltip-title">{{ $t('step3.assayTrackActions') }}</div>
            <div class="tooltip-actions">
              <span class="tooltip-action">{{ $t('step3.actionPropose') }}</span>
              <span class="tooltip-action">{{ $t('step3.actionReview') }}</span>
              <span class="tooltip-action">{{ $t('step3.actionMatch') }}</span>
              <span class="tooltip-action">{{ $t('step3.actionSelect') }}</span>
            </div>
          </div>
        </div>

        <!-- Hypothesis Track -->
        <div class="platform-status hypothesis" :class="{ active: isRunning && currentTrack === 'hypothesis', completed: runStatus.is_completed }">
          <div class="platform-header">
            <svg class="platform-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M9.5 2A2.5 2.5 0 0 0 7 4.5v1.086a3 3 0 0 0-1.414.828l-1.5 1.5A3 3 0 0 0 3 9.914V19.5A2.5 2.5 0 0 0 5.5 22h9a2.5 2.5 0 0 0 2.5-2.5v-1"></path><circle cx="17" cy="7" r="4"></circle>
            </svg>
            <span class="platform-name">{{ $t('step3.hypothesisTrack') }}</span>
            <span v-if="runStatus.is_completed" class="status-badge">
              <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="3">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
            </span>
          </div>
          <div class="platform-stats">
            <span class="stat">
              <span class="stat-label">{{ $t('step3.round') }}</span>
              <span class="stat-value mono">{{ runStatus.current_round || 0 }}<span class="stat-total">/{{ runStatus.total_rounds || '-' }}</span></span>
            </span>
            <span class="stat">
              <span class="stat-label">{{ $t('step3.acts') }}</span>
              <span class="stat-value mono">{{ hypothesisActionsCount }}</span>
            </span>
          </div>
          <div class="actions-tooltip">
            <div class="tooltip-title">{{ $t('step3.hypothesisTrackActions') }}</div>
            <div class="tooltip-actions">
              <span class="tooltip-action">{{ $t('step3.actionPropose') }}</span>
              <span class="tooltip-action">{{ $t('step3.actionReview') }}</span>
              <span class="tooltip-action">{{ $t('step3.actionMatch') }}</span>
              <span class="tooltip-action">{{ $t('step3.actionRefine') }}</span>
              <span class="tooltip-action">{{ $t('step3.actionMetaReview') }}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="action-controls">
        <button
          class="action-btn primary"
          :disabled="phase !== 2 || isGeneratingReport"
          @click="handleNextStep"
        >
          <span v-if="isGeneratingReport" class="loading-spinner-small"></span>
          {{ isGeneratingReport ? $t('step3.generatingReportBtn') : $t('step3.startGenerateReportBtn') }}
          <span v-if="!isGeneratingReport" class="arrow-icon">→</span>
        </button>
      </div>
    </div>

    <!-- Main Content: Research Loop Timeline -->
    <div class="main-content-area" ref="scrollContainer">
      <div class="timeline-header" v-if="allActions.length > 0">
        <div class="timeline-stats">
          <span class="total-count">{{ $t('step3.totalEvents') }} <span class="mono">{{ allActions.length }}</span></span>
          <span class="platform-breakdown">
            <span class="breakdown-item assay">
              <svg class="mini-icon" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
              <span class="mono">{{ assayActionsCount }}</span>
            </span>
            <span class="breakdown-divider">/</span>
            <span class="breakdown-item hypothesis">
              <svg class="mini-icon" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><circle cx="17" cy="7" r="4"></circle></svg>
              <span class="mono">{{ hypothesisActionsCount }}</span>
            </span>
          </span>
        </div>
      </div>

      <div class="timeline-feed">
        <div class="timeline-axis"></div>

        <TransitionGroup name="timeline-item">
          <div
            v-for="action in chronologicalActions"
            :key="action._uniqueId || `${action.timestamp}-${action.agent_name}`"
            class="timeline-item"
            :class="getTrack(action.action_type)"
          >
            <div class="timeline-marker">
              <div class="marker-dot"></div>
            </div>

            <div class="timeline-card">
              <div class="card-header">
                <div class="agent-info">
                  <div class="avatar-placeholder">{{ (action.agent_name || 'A')[0] }}</div>
                  <span class="agent-name">{{ action.agent_name }}</span>
                </div>

                <div class="header-meta">
                  <div class="action-badge" :class="getActionTypeClass(action.action_type)">
                    {{ getActionTypeLabel(action.action_type) }}
                  </div>
                </div>
              </div>

              <div class="card-body">
                <!-- PROPOSE_ASSAY / PROPOSE_HYPOTHESIS: 提出候选项 -->
                <template v-if="action.action_type === 'PROPOSE_ASSAY' || action.action_type === 'PROPOSE_HYPOTHESIS'">
                  <div class="content-text main-text">
                    {{ $t('step3.proposedCandidates', { count: action.action_args?.count, names: (action.action_args?.names || []).join(', ') }) }}
                  </div>
                </template>

                <!-- SEARCH_LITERATURE: 检索文献 -->
                <template v-if="action.action_type === 'SEARCH_LITERATURE'">
                  <div class="search-info">
                    <svg class="icon-small" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
                    <span class="search-label">{{ $t('step3.queriesLabel', { count: (action.action_args?.queries || []).length }) }}</span>
                  </div>
                  <ul class="query-list">
                    <li v-for="(q, idx) in action.action_args?.queries || []" :key="idx">{{ q }}</li>
                  </ul>
                </template>

                <!-- REVIEW_ASSAY / REVIEW_HYPOTHESIS: 详细评审 -->
                <template v-if="action.action_type === 'REVIEW_ASSAY' || action.action_type === 'REVIEW_HYPOTHESIS'">
                  <div class="review-header">
                    <span class="review-candidate">{{ action.action_args?.candidate }}</span>
                    <span class="confidence-badge" :class="'confidence-' + action.action_args?.confidence">{{ action.action_args?.confidence }}</span>
                  </div>
                  <div class="content-text">{{ truncateContent(action.result, 300) }}</div>
                </template>

                <!-- ASSAY_MATCH / HYPOTHESIS_MATCH: 锦标赛对战 -->
                <template v-if="action.action_type === 'ASSAY_MATCH' || action.action_type === 'HYPOTHESIS_MATCH'">
                  <div class="match-info">
                    <div class="match-row" :class="{ winner: action.action_args?.winner === action.action_args?.candidate_a }">
                      <span class="match-name">{{ action.action_args?.candidate_a }}</span>
                      <span class="match-elo mono">{{ action.action_args?.elo_a }}</span>
                    </div>
                    <div class="match-vs">{{ $t('step3.vsLabel') }}</div>
                    <div class="match-row" :class="{ winner: action.action_args?.winner === action.action_args?.candidate_b }">
                      <span class="match-name">{{ action.action_args?.candidate_b }}</span>
                      <span class="match-elo mono">{{ action.action_args?.elo_b }}</span>
                    </div>
                  </div>
                  <div class="content-text small">{{ truncateContent(action.result, 200) }}</div>
                </template>

                <!-- SELECT_ASSAY: 选定评估视角 -->
                <template v-if="action.action_type === 'SELECT_ASSAY'">
                  <div class="select-info">
                    <svg class="icon-small filled" viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87L18.18 21 12 17.77 5.82 21 7 14.14l-5-4.87 6.91-1.01z"/></svg>
                    <span class="select-label">{{ $t('step3.selectedLabel', { selected: action.action_args?.selected }) }}</span>
                    <span class="select-elo mono">{{ $t('step3.eloValue', { elo: action.action_args?.elo_rating }) }}</span>
                  </div>
                  <div class="content-text">{{ truncateContent(action.result, 250) }}</div>
                </template>

                <!-- SYNTHESIZE_GENERATION_GOAL -->
                <template v-if="action.action_type === 'SYNTHESIZE_GENERATION_GOAL'">
                  <div class="content-text main-text">{{ truncateContent(action.result, 300) }}</div>
                </template>

                <!-- REFINE_HYPOTHESIS -->
                <template v-if="action.action_type === 'REFINE_HYPOTHESIS'">
                  <div class="content-text">
                    {{ $t('step3.refinedCandidates', { input: action.action_args?.input_count, output: action.action_args?.output_count }) }}
                  </div>
                </template>

                <!-- META_REVIEW -->
                <template v-if="action.action_type === 'META_REVIEW'">
                  <div class="content-text main-text">{{ truncateContent(action.result, 350) }}</div>
                  <ul v-if="action.action_args?.key_patterns?.length" class="query-list">
                    <li v-for="(p, idx) in action.action_args.key_patterns" :key="idx">{{ p }}</li>
                  </ul>
                </template>

                <!-- MERGE_DUPLICATE_CANDIDATES -->
                <template v-if="action.action_type === 'MERGE_DUPLICATE_CANDIDATES'">
                  <div class="content-text">
                    {{ $t('step3.mergedCandidates', { candidates: action.action_args?.candidates_count, clusters: action.action_args?.clusters_count }) }}
                  </div>
                </template>
              </div>

              <div class="card-footer">
                <span class="time-tag">R{{ action.round_num }} • {{ formatActionTime(action.timestamp) }}</span>
              </div>
            </div>
          </div>
        </TransitionGroup>

        <div v-if="allActions.length === 0" class="waiting-state">
          <div class="pulse-ring"></div>
          <span>{{ $t('step3.waitingForActions') }}</span>
        </div>
      </div>
    </div>

    <!-- Bottom Info / Logs -->
    <div class="system-logs">
      <div class="log-header">
        <span class="log-title">{{ $t('step3.researchLoopMonitor') }}</span>
        <span class="log-id">{{ simulationId || $t('step3.noSimulation') }}</span>
      </div>
      <div class="log-content" ref="logContent">
        <div class="log-line" v-for="(log, idx) in systemLogs" :key="idx">
          <span class="log-time">{{ log.time }}</span>
          <span class="log-msg">{{ log.msg }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  startSimulation,
  stopSimulation,
  getRunStatus,
  getRunStatusDetail
} from '../api/simulation'
import { generateReport } from '../api/report'

const { t } = useI18n()

const props = defineProps({
  simulationId: String,
  projectData: Object,
  graphData: Object,
  systemLogs: Array
})

const emit = defineEmits(['go-back', 'next-step', 'add-log', 'update-status'])

const router = useRouter()

const ASSAY_ACTION_TYPES = ['PROPOSE_ASSAY', 'REVIEW_ASSAY', 'ASSAY_MATCH', 'SELECT_ASSAY', 'SYNTHESIZE_GENERATION_GOAL', 'MERGE_DUPLICATE_CANDIDATES']

// State
const isGeneratingReport = ref(false)
const phase = ref(0) // 0: 未开始, 1: 运行中, 2: 已完成
const isStarting = ref(false)
const isStopping = ref(false)
const startError = ref(null)
const runStatus = ref({})
const allActions = ref([])
const actionIds = ref(new Set())
const scrollContainer = ref(null)

const chronologicalActions = computed(() => allActions.value)

const getTrack = (actionType) => {
  if (ASSAY_ACTION_TYPES.includes(actionType)) return 'assay'
  if (actionType === 'SEARCH_LITERATURE') {
    return currentTrack.value
  }
  return 'hypothesis'
}

const currentTrack = computed(() => {
  const last = allActions.value[allActions.value.length - 1]
  return last ? getTrack(last.action_type) : 'assay'
})

const assayActionsCount = computed(() => allActions.value.filter(a => ASSAY_ACTION_TYPES.includes(a.action_type)).length)
const hypothesisActionsCount = computed(() => allActions.value.filter(a => !ASSAY_ACTION_TYPES.includes(a.action_type)).length)
const assayTrackDone = computed(() => allActions.value.some(a => a.action_type === 'SYNTHESIZE_GENERATION_GOAL'))
const isRunning = computed(() => runStatus.value.is_running === true)

// Methods
const addLog = (msg) => emit('add-log', msg)

const resetAllState = () => {
  phase.value = 0
  runStatus.value = {}
  allActions.value = []
  actionIds.value = new Set()
  startError.value = null
  isStarting.value = false
  isStopping.value = false
  stopPolling()
}

const doStartSimulation = async () => {
  if (!props.simulationId) {
    addLog(t('log.errorMissingSimId'))
    return
  }

  resetAllState()

  isStarting.value = true
  startError.value = null
  addLog(t('log.startingDualSim'))
  emit('update-status', 'processing')

  try {
    const params = {
      simulation_id: props.simulationId,
      force: true,
      enable_graph_memory_update: true
    }

    addLog(t('log.graphMemoryUpdateEnabled'))

    const res = await startSimulation(params)

    if (res.success && res.data) {
      if (res.data.force_restarted) {
        addLog(t('log.oldSimCleared'))
      }
      addLog(t('log.engineStarted'))
      addLog(t('step3.pidLog', { pid: res.data.process_pid || '-' }))

      phase.value = 1
      runStatus.value = res.data

      startStatusPolling()
      startDetailPolling()
    } else {
      startError.value = res.error || '启动失败'
      addLog(t('log.startFailed', { error: res.error || t('common.unknownError') }))
      emit('update-status', 'error')
    }
  } catch (err) {
    startError.value = err.message
    addLog(t('log.startException', { error: err.message }))
    emit('update-status', 'error')
  } finally {
    isStarting.value = false
  }
}

const handleStopSimulation = async () => {
  if (!props.simulationId) return

  isStopping.value = true
  addLog(t('log.stoppingSim'))

  try {
    const res = await stopSimulation({ simulation_id: props.simulationId })

    if (res.success) {
      addLog(t('log.simStopped'))
      phase.value = 2
      stopPolling()
      emit('update-status', 'completed')
    } else {
      addLog(t('log.stopFailed', { error: res.error || t('common.unknownError') }))
    }
  } catch (err) {
    addLog(t('log.stopException', { error: err.message }))
  } finally {
    isStopping.value = false
  }
}

let statusTimer = null
let detailTimer = null

const startStatusPolling = () => {
  statusTimer = setInterval(fetchRunStatus, 2000)
}

const startDetailPolling = () => {
  detailTimer = setInterval(fetchRunStatusDetail, 3000)
}

const stopPolling = () => {
  if (statusTimer) {
    clearInterval(statusTimer)
    statusTimer = null
  }
  if (detailTimer) {
    clearInterval(detailTimer)
    detailTimer = null
  }
}

const prevRound = ref(0)

const fetchRunStatus = async () => {
  if (!props.simulationId) return

  try {
    const res = await getRunStatus(props.simulationId)

    if (res.success && res.data) {
      const data = res.data
      runStatus.value = data

      if (data.current_round > prevRound.value) {
        addLog(t('step3.roundActionsLog', { round: data.current_round, total: data.total_rounds, count: data.actions_count }))
        prevRound.value = data.current_round
      }

      if (data.is_completed || data.runner_status === 'completed' || data.runner_status === 'stopped') {
        addLog(t('log.simCompleted'))
        phase.value = 2
        stopPolling()
        emit('update-status', 'completed')
      }
    }
  } catch (err) {
    console.warn('获取运行状态失败:', err)
  }
}

const fetchRunStatusDetail = async () => {
  if (!props.simulationId) return

  try {
    const res = await getRunStatusDetail(props.simulationId)

    if (res.success && res.data) {
      const serverActions = res.data.all_actions || []

      serverActions.forEach(action => {
        const actionId = `${action.timestamp}-${action.agent_name}-${action.action_type}`

        if (!actionIds.value.has(actionId)) {
          actionIds.value.add(actionId)
          allActions.value.push({ ...action, _uniqueId: actionId })
        }
      })

      // 保持时间顺序（旧->新，最新在底部）
      allActions.value.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp))
    }
  } catch (err) {
    console.warn('获取详细状态失败:', err)
  }
}

// Helpers
const getActionTypeLabel = (type) => {
  const labels = {
    'PROPOSE_ASSAY': t('step3.badgeProposeLens'),
    'REVIEW_ASSAY': t('step3.badgeReview'),
    'ASSAY_MATCH': t('step3.badgeMatch'),
    'SELECT_ASSAY': t('step3.badgeSelected'),
    'SYNTHESIZE_GENERATION_GOAL': t('step3.badgeSynthesize'),
    'SEARCH_LITERATURE': t('step3.badgeSearch'),
    'PROPOSE_HYPOTHESIS': t('step3.badgePropose'),
    'REVIEW_HYPOTHESIS': t('step3.badgeReview'),
    'HYPOTHESIS_MATCH': t('step3.badgeMatch'),
    'REFINE_HYPOTHESIS': t('step3.badgeRefine'),
    'META_REVIEW': t('step3.badgeMetaReview'),
    'MERGE_DUPLICATE_CANDIDATES': t('step3.badgeDedupe'),
  }
  return labels[type] || type || t('step3.badgeUnknown')
}

const getActionTypeClass = (type) => {
  const classes = {
    'PROPOSE_ASSAY': 'badge-post',
    'REVIEW_ASSAY': 'badge-comment',
    'ASSAY_MATCH': 'badge-action',
    'SELECT_ASSAY': 'badge-post',
    'SYNTHESIZE_GENERATION_GOAL': 'badge-meta',
    'SEARCH_LITERATURE': 'badge-meta',
    'PROPOSE_HYPOTHESIS': 'badge-post',
    'REVIEW_HYPOTHESIS': 'badge-comment',
    'HYPOTHESIS_MATCH': 'badge-action',
    'REFINE_HYPOTHESIS': 'badge-post',
    'META_REVIEW': 'badge-meta',
    'MERGE_DUPLICATE_CANDIDATES': 'badge-idle',
  }
  return classes[type] || 'badge-default'
}

const truncateContent = (content, maxLength = 100) => {
  if (!content) return ''
  if (content.length > maxLength) return content.substring(0, maxLength) + '...'
  return content
}

const formatActionTime = (timestamp) => {
  if (!timestamp) return ''
  try {
    return new Date(timestamp).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return ''
  }
}

const handleNextStep = async () => {
  if (!props.simulationId) {
    addLog(t('log.errorMissingSimId'))
    return
  }

  if (isGeneratingReport.value) {
    addLog(t('log.reportRequestSent'))
    return
  }

  isGeneratingReport.value = true
  addLog(t('log.startingReportGen'))

  try {
    const res = await generateReport({
      simulation_id: props.simulationId,
      force_regenerate: true
    })

    if (res.success && res.data) {
      const reportId = res.data.report_id
      addLog(t('log.reportGenTaskStarted', { reportId }))
      router.push({ name: 'Report', params: { reportId } })
    } else {
      addLog(t('log.reportGenFailed', { error: res.error || t('common.unknownError') }))
      isGeneratingReport.value = false
    }
  } catch (err) {
    addLog(t('log.reportGenException', { error: err.message }))
    isGeneratingReport.value = false
  }
}

const logContent = ref(null)
watch(() => props.systemLogs?.length, () => {
  nextTick(() => {
    if (logContent.value) {
      logContent.value.scrollTop = logContent.value.scrollHeight
    }
  })
})

onMounted(() => {
  addLog(t('log.step3Init'))
  if (props.simulationId) {
    doStartSimulation()
  }
})

onUnmounted(() => {
  stopPolling()
})
</script>

<style scoped>
.simulation-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #FFFFFF;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
  overflow: hidden;
}

/* --- Control Bar --- */
.control-bar {
  background: #FFF;
  padding: 12px 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid #EAEAEA;
  z-index: 10;
  height: 64px;
}

.status-group {
  display: flex;
  gap: 12px;
}

.platform-status {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px 12px;
  border-radius: 4px;
  background: #FAFAFA;
  border: 1px solid #EAEAEA;
  opacity: 0.7;
  transition: all 0.3s;
  min-width: 140px;
  position: relative;
  cursor: pointer;
}

.platform-status.active {
  opacity: 1;
  border-color: #333;
  background: #FFF;
}

.platform-status.completed {
  opacity: 1;
  border-color: #1A936F;
  background: #F2FAF6;
}

.actions-tooltip {
  display: none;
  position: absolute;
  top: 100%;
  left: 0;
  margin-top: 4px;
  background: #000;
  color: #FFF;
  padding: 8px 10px;
  border-radius: 4px;
  font-size: 10px;
  z-index: 20;
  white-space: nowrap;
}

.platform-status:hover .actions-tooltip {
  display: block;
}

.tooltip-title {
  font-weight: 700;
  margin-bottom: 4px;
  color: #FF9800;
}

.tooltip-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.tooltip-action {
  background: #333;
  padding: 2px 6px;
  border-radius: 3px;
}

.platform-header {
  display: flex;
  align-items: center;
  gap: 6px;
}

.platform-icon {
  color: #666;
}

.platform-status.active .platform-icon {
  color: #FF5722;
}

.platform-status.completed .platform-icon {
  color: #1A936F;
}

.platform-name {
  font-size: 11px;
  font-weight: 600;
  color: #333;
}

.status-badge {
  color: #1A936F;
  margin-left: auto;
}

.platform-stats {
  display: flex;
  gap: 12px;
}

.stat {
  display: flex;
  flex-direction: column;
}

.stat-label {
  font-size: 8px;
  color: #999;
}

.stat-value {
  font-size: 12px;
  font-weight: 700;
}

.stat-total {
  color: #999;
  font-weight: 400;
}

.action-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  font-size: 13px;
  font-weight: 600;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s ease;
  background: #000;
  color: #FFF;
}

.action-btn:hover:not(:disabled) {
  opacity: 0.8;
}

.action-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.loading-spinner-small {
  width: 12px;
  height: 12px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: #FFF;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* --- Main Content --- */
.main-content-area {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
  background: #FAFAFA;
}

.timeline-header {
  margin-bottom: 16px;
}

.timeline-stats {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 11px;
  color: #666;
}

.platform-breakdown {
  display: flex;
  align-items: center;
  gap: 6px;
}

.breakdown-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.breakdown-item.assay { color: #1565C0; }
.breakdown-item.hypothesis { color: #FF5722; }

.mono {
  font-family: 'JetBrains Mono', monospace;
}

.timeline-feed {
  position: relative;
  padding-left: 24px;
}

.timeline-axis {
  position: absolute;
  left: 6px;
  top: 0;
  bottom: 0;
  width: 1px;
  background: #E0E0E0;
}

.timeline-item {
  position: relative;
  margin-bottom: 16px;
}

.timeline-marker {
  position: absolute;
  left: -24px;
  top: 8px;
}

.marker-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #FFF;
  border: 2px solid #999;
}

.timeline-item.assay .marker-dot {
  border-color: #1565C0;
}

.timeline-item.hypothesis .marker-dot {
  border-color: #FF5722;
}

.timeline-card {
  background: #FFF;
  border-radius: 8px;
  border: 1px solid #EAEAEA;
  padding: 14px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.03);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.agent-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.avatar-placeholder {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #333;
  color: #FFF;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 700;
}

.agent-name {
  font-size: 12px;
  font-weight: 600;
}

.header-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.action-badge {
  font-size: 9px;
  font-weight: 700;
  padding: 3px 8px;
  border-radius: 4px;
  text-transform: uppercase;
}

.badge-post { background: #E3F2FD; color: #1565C0; }
.badge-comment { background: #FFF3E0; color: #E65100; }
.badge-action { background: #F3E5F5; color: #6A1B9A; }
.badge-meta { background: #E8F5E9; color: #2E7D32; }
.badge-idle { background: #F5F5F5; color: #999; }
.badge-default { background: #F5F5F5; color: #666; }

.card-body {
  font-size: 12px;
  color: #333;
  line-height: 1.6;
}

.content-text.main-text {
  font-weight: 500;
}

.content-text.small {
  font-size: 11px;
  color: #666;
  margin-top: 6px;
}

.query-list {
  margin: 6px 0 0;
  padding-left: 16px;
  font-size: 11px;
  color: #666;
}

.search-info, .select-info {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #666;
}

.select-label {
  font-weight: 600;
  color: #333;
}

.select-elo {
  margin-left: auto;
  color: #FF5722;
  font-weight: 700;
}

.icon-small {
  flex-shrink: 0;
}

.icon-small.filled {
  color: #FFB300;
}

.review-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.review-candidate {
  font-weight: 600;
  font-size: 12px;
}

.confidence-badge {
  font-size: 9px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 3px;
  text-transform: uppercase;
}

.confidence-high { background: #E8F5E9; color: #2E7D32; }
.confidence-medium { background: #FFF3E0; color: #E65100; }
.confidence-low { background: #FFEBEE; color: #C62828; }

.match-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  background: #FAFAFA;
  border-radius: 6px;
  padding: 8px;
}

.match-row {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  padding: 2px 4px;
  border-radius: 3px;
}

.match-row.winner {
  background: #E8F5E9;
  font-weight: 700;
}

.match-vs {
  text-align: center;
  font-size: 9px;
  color: #999;
}

.match-elo {
  color: #666;
}

.card-footer {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed #EEE;
}

.time-tag {
  font-size: 10px;
  color: #999;
  font-family: 'JetBrains Mono', monospace;
}

.waiting-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 60px 0;
  color: #999;
  font-size: 12px;
}

.pulse-ring {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: 2px solid #FF5722;
  animation: pulse 1.5s ease-out infinite;
}

@keyframes pulse {
  0% { transform: scale(0.5); opacity: 1; }
  100% { transform: scale(1.5); opacity: 0; }
}

/* System Logs */
.system-logs {
  background: #000;
  color: #DDD;
  padding: 16px 24px;
  font-family: 'JetBrains Mono', monospace;
  border-top: 1px solid #222;
  flex-shrink: 0;
}

.log-header {
  display: flex;
  justify-content: space-between;
  border-bottom: 1px solid #333;
  padding-bottom: 8px;
  margin-bottom: 8px;
  font-size: 10px;
  color: #888;
}

.log-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
  height: 80px;
  overflow-y: auto;
  padding-right: 4px;
}

.log-content::-webkit-scrollbar {
  width: 4px;
}

.log-content::-webkit-scrollbar-thumb {
  background: #333;
  border-radius: 2px;
}

.log-line {
  font-size: 11px;
  display: flex;
  gap: 12px;
  line-height: 1.5;
}

.log-time {
  color: #666;
  min-width: 75px;
}

.log-msg {
  color: #CCC;
  word-break: break-all;
}
</style>
