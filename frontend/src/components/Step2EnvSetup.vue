<template>
  <div class="env-setup-panel">
    <div class="scroll-container">
      <!-- Step 01: 研究循环实例 -->
      <div class="step-card" :class="{ 'active': phase === 0, 'completed': phase > 0 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">01</span>
            <span class="step-title">{{ $t('step2.simInstanceInit') }}</span>
          </div>
          <div class="step-status">
            <span v-if="phase > 0" class="badge success">{{ $t('common.completed') }}</span>
            <span v-else class="badge processing">{{ $t('step2.initializing') }}</span>
          </div>
        </div>

        <div class="card-content">
          <p class="api-note">POST /api/simulation/create</p>
          <p class="description">{{ $t('step2.simInstanceDesc') }}</p>

          <div v-if="simulationId" class="info-card">
            <div class="info-row">
              <span class="info-label">{{ $t('step2.projectIdLabel') }}</span>
              <span class="info-value mono">{{ projectData?.project_id }}</span>
            </div>
            <div class="info-row">
              <span class="info-label">{{ $t('step2.graphIdLabel') }}</span>
              <span class="info-value mono">{{ projectData?.graph_id }}</span>
            </div>
            <div class="info-row">
              <span class="info-label">{{ $t('step2.simulationIdLabel') }}</span>
              <span class="info-value mono">{{ simulationId }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Step 02: 配置研究循环参数（7个固定角色） -->
      <div class="step-card" :class="{ 'active': phase === 1, 'completed': phase > 1 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">02</span>
            <span class="step-title">{{ $t('step2.configureRunParams') }}</span>
          </div>
          <div class="step-status">
            <span v-if="phase > 1" class="badge success">{{ $t('common.completed') }}</span>
            <span v-else-if="phase === 1" class="badge processing">{{ $t('step2.awaitingInput') }}</span>
            <span v-else class="badge pending">{{ $t('common.pending') }}</span>
          </div>
        </div>

        <div class="card-content">
          <p class="api-note">POST /api/simulation/prepare</p>
          <p class="description">{{ $t('step2.configureRunParamsDesc') }}</p>

          <!-- 7个固定角色说明 -->
          <div class="roles-grid">
            <div v-for="role in fixedRoles" :key="role.id" class="role-card">
              <span class="role-name">{{ role.name }}</span>
              <span class="role-desc">{{ role.desc }}</span>
            </div>
          </div>

          <!-- 参数滑块 -->
          <div v-if="phase <= 1" class="params-form">
            <div class="param-slider-row">
              <div class="param-slider-label">
                <span>{{ $t('step2.numQueries') }}</span>
                <span class="param-slider-value">{{ runParams.num_queries }}</span>
              </div>
              <input type="range" v-model.number="runParams.num_queries" min="1" max="10" step="1" class="minimal-slider" />
            </div>
            <div class="param-slider-row">
              <div class="param-slider-label">
                <span>{{ $t('step2.numCandidates') }}</span>
                <span class="param-slider-value">{{ runParams.num_candidates }}</span>
              </div>
              <input type="range" v-model.number="runParams.num_candidates" min="3" max="10" step="1" class="minimal-slider" />
            </div>
            <div class="param-slider-row">
              <div class="param-slider-label">
                <span>{{ $t('step2.numAssays') }}</span>
                <span class="param-slider-value">{{ runParams.num_assays }}</span>
              </div>
              <input type="range" v-model.number="runParams.num_assays" min="1" max="5" step="1" class="minimal-slider" />
            </div>
            <div class="param-slider-row">
              <div class="param-slider-label">
                <span>{{ $t('step2.numCycles') }}</span>
                <span class="param-slider-value">{{ runParams.num_cycles }}</span>
              </div>
              <input type="range" v-model.number="runParams.num_cycles" min="1" max="5" step="1" class="minimal-slider" />
            </div>

            <button class="action-btn primary full-width" @click="startPrepareSimulation">
              {{ $t('step2.startPreparation') }} ➝
            </button>
          </div>

          <!-- 准备中 / 已验证参数展示 -->
          <div v-else class="info-card">
            <div class="info-row">
              <span class="info-label">{{ $t('step2.numQueries') }}</span>
              <span class="info-value mono">{{ validatedParams?.num_queries ?? runParams.num_queries }}</span>
            </div>
            <div class="info-row">
              <span class="info-label">{{ $t('step2.numCandidates') }}</span>
              <span class="info-value mono">{{ validatedParams?.num_candidates ?? runParams.num_candidates }}</span>
            </div>
            <div class="info-row">
              <span class="info-label">{{ $t('step2.numAssays') }}</span>
              <span class="info-value mono">{{ validatedParams?.num_assays ?? runParams.num_assays }}</span>
            </div>
            <div class="info-row">
              <span class="info-label">{{ $t('step2.numCycles') }}</span>
              <span class="info-value mono">{{ validatedParams?.num_cycles ?? runParams.num_cycles }}</span>
            </div>
            <div class="info-row" v-if="entitiesCount">
              <span class="info-label">{{ $t('step2.graphEntities') }}</span>
              <span class="info-value mono">{{ entitiesCount }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Step 03: 准备完成，可启动研究循环 -->
      <div class="step-card" :class="{ 'active': phase === 2 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">03</span>
            <span class="step-title">{{ $t('step2.setupComplete') }}</span>
          </div>
          <div class="step-status">
            <span v-if="phase >= 2" class="badge processing">{{ $t('step1.inProgress') }}</span>
            <span v-else class="badge pending">{{ $t('common.pending') }}</span>
          </div>
        </div>

        <div class="card-content">
          <p class="api-note">POST /api/simulation/start</p>
          <p class="description">{{ $t('step2.setupCompleteDesc') }}</p>

          <div class="action-group dual">
            <button class="action-btn secondary" @click="$emit('go-back')">
              ← {{ $t('step2.backToGraphBuild') }}
            </button>
            <button class="action-btn primary" :disabled="phase < 2" @click="handleStartSimulation">
              {{ $t('step2.startResearchLoop') }} ➝
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Bottom Info / Logs -->
    <div class="system-logs">
      <div class="log-header">
        <span class="log-title">{{ $t('step2.systemDashboard') }}</span>
        <span class="log-id">{{ simulationId || $t('step2.noSimulation') }}</span>
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
import { useI18n } from 'vue-i18n'
import { prepareSimulation, getPrepareStatus } from '../api/simulation'

const { t } = useI18n()

const props = defineProps({
  simulationId: String,
  projectData: Object,
  graphData: Object,
  systemLogs: Array
})

const emit = defineEmits(['go-back', 'next-step', 'add-log', 'update-status'])

// State
const phase = ref(0) // 0: 初始化, 1: 配置参数, 2: 准备完成
const taskId = ref(null)
const entitiesCount = ref(null)
const validatedParams = ref(null)

const runParams = ref({
  num_queries: 3,
  num_candidates: 5,
  num_assays: 3,
  num_cycles: 1,
})

const fixedRoles = computed(() => [
  { id: 'Generation', name: t('step2.roleNameGeneration'), desc: t('step2.roleGeneration') },
  { id: 'Reflection', name: t('step2.roleNameReflection'), desc: t('step2.roleReflection') },
  { id: 'Ranking', name: t('step2.roleNameRanking'), desc: t('step2.roleRanking') },
  { id: 'Tournament', name: t('step2.roleNameTournament'), desc: t('step2.roleTournament') },
  { id: 'Evolution', name: t('step2.roleNameEvolution'), desc: t('step2.roleEvolution') },
  { id: 'Proximity', name: t('step2.roleNameProximity'), desc: t('step2.roleProximity') },
  { id: 'MetaReview', name: t('step2.roleNameMetaReview'), desc: t('step2.roleMetaReview') },
])

let pollTimer = null

const addLog = (msg) => emit('add-log', msg)

const handleStartSimulation = () => {
  emit('next-step', {})
}

const startPrepareSimulation = async () => {
  if (!props.simulationId) {
    addLog(t('log.errorMissingSimId'))
    emit('update-status', 'error')
    return
  }

  phase.value = 1
  addLog(t('log.simInstanceCreated', { id: props.simulationId }))
  addLog(t('log.preparingSimEnv'))
  emit('update-status', 'processing')

  try {
    const res = await prepareSimulation({
      simulation_id: props.simulationId,
      num_queries: runParams.value.num_queries,
      num_candidates: runParams.value.num_candidates,
      num_assays: runParams.value.num_assays,
      num_cycles: runParams.value.num_cycles,
    })

    if (res.success && res.data) {
      if (res.data.already_prepared) {
        addLog(t('log.detectedExistingPrep'))
        finishPrepare(res.data.prepare_info)
        return
      }

      taskId.value = res.data.task_id
      addLog(t('log.prepareTaskStarted'))

      if (res.data.expected_entities_count) {
        entitiesCount.value = res.data.expected_entities_count
        addLog(t('log.zepEntitiesFound', { count: res.data.expected_entities_count }))
      }

      addLog(t('log.startPollingProgress'))
      startPolling()
    } else {
      addLog(t('log.prepareFailed', { error: res.error || t('common.unknownError') }))
      emit('update-status', 'error')
    }
  } catch (err) {
    addLog(t('log.prepareException', { error: err.message }))
    emit('update-status', 'error')
  }
}

const startPolling = () => {
  pollTimer = setInterval(pollPrepareStatus, 2000)
}

const stopPolling = () => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

let lastLoggedMessage = ''

const pollPrepareStatus = async () => {
  if (!taskId.value && !props.simulationId) return

  try {
    const res = await getPrepareStatus({
      task_id: taskId.value,
      simulation_id: props.simulationId
    })

    if (res.success && res.data) {
      const data = res.data

      if (data.message && data.message !== lastLoggedMessage) {
        lastLoggedMessage = data.message
        addLog(data.message)
      }

      if (data.status === 'completed' || data.status === 'ready' || data.already_prepared) {
        stopPolling()
        finishPrepare(data.result || data.prepare_info)
      } else if (data.status === 'failed') {
        addLog(t('log.prepareFailedWithError', { error: data.error || t('common.unknownError') }))
        stopPolling()
        emit('update-status', 'error')
      }
    }
  } catch (err) {
    console.warn('轮询状态失败:', err)
  }
}

const finishPrepare = (info) => {
  addLog(t('log.prepareComplete'))
  validatedParams.value = {
    num_queries: info?.num_queries ?? runParams.value.num_queries,
    num_candidates: info?.num_candidates ?? runParams.value.num_candidates,
    num_assays: info?.num_assays ?? runParams.value.num_assays,
    num_cycles: info?.num_cycles ?? runParams.value.num_cycles,
  }
  if (info?.entities_count) {
    entitiesCount.value = info.entities_count
  }
  phase.value = 2
  addLog(t('log.envSetupComplete'))
  emit('update-status', 'completed')
}

// Scroll log to bottom
const logContent = ref(null)
watch(() => props.systemLogs?.length, () => {
  nextTick(() => {
    if (logContent.value) {
      logContent.value.scrollTop = logContent.value.scrollHeight
    }
  })
})

onMounted(() => {
  if (props.simulationId) {
    addLog(t('log.step2Init'))
    phase.value = 1
    emit('update-status', 'processing')
  }
})

onUnmounted(() => {
  stopPolling()
})
</script>

<style scoped>
.env-setup-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #FAFAFA;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
}

.scroll-container {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* Step Card */
.step-card {
  background: #FFF;
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
  border: 1px solid #EAEAEA;
  transition: all 0.3s ease;
  position: relative;
}

.step-card.active {
  border-color: #FF5722;
  box-shadow: 0 4px 12px rgba(255, 87, 34, 0.08);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.step-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.step-num {
  font-family: 'JetBrains Mono', monospace;
  font-size: 20px;
  font-weight: 700;
  color: #E0E0E0;
}

.step-card.active .step-num,
.step-card.completed .step-num {
  color: #000;
}

.step-title {
  font-weight: 600;
  font-size: 14px;
  letter-spacing: 0.5px;
}

.badge {
  font-size: 10px;
  padding: 4px 8px;
  border-radius: 4px;
  font-weight: 600;
  text-transform: uppercase;
}

.badge.success { background: #E8F5E9; color: #2E7D32; }
.badge.processing { background: #FF5722; color: #FFF; }
.badge.pending { background: #F5F5F5; color: #999; }

.card-content {
  /* No extra padding - uses step-card's padding */
}

.api-note {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: #999;
  margin-bottom: 8px;
}

.description {
  font-size: 12px;
  color: #666;
  line-height: 1.5;
  margin-bottom: 16px;
}

/* Action Section */
.action-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 12px 24px;
  font-size: 14px;
  font-weight: 600;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.action-btn.primary {
  background: #000;
  color: #FFF;
}

.action-btn.primary:hover:not(:disabled) {
  opacity: 0.8;
}

.action-btn.secondary {
  background: #F5F5F5;
  color: #333;
}

.action-btn.secondary:hover:not(:disabled) {
  background: #E5E5E5;
}

.action-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.action-btn.full-width {
  width: 100%;
  margin-top: 8px;
}

.action-group {
  display: flex;
  gap: 12px;
  margin-top: 16px;
}

.action-group.dual {
  display: grid;
  grid-template-columns: 1fr 1fr;
}

.action-group.dual .action-btn {
  width: 100%;
}

/* Info Card */
.info-card {
  background: #F5F5F5;
  border-radius: 6px;
  padding: 16px;
  margin-top: 16px;
}

.info-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px dashed #E0E0E0;
}

.info-row:last-child {
  border-bottom: none;
}

.info-label {
  font-size: 12px;
  color: #666;
}

.info-value {
  font-size: 13px;
  font-weight: 500;
}

.info-value.mono {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
}

/* 7 Fixed Roles Grid */
.roles-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 8px;
  background: #F9F9F9;
  padding: 12px;
  border-radius: 6px;
  margin-bottom: 20px;
}

.role-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px;
  background: #FFF;
  border-radius: 4px;
  border: 1px solid #EEE;
}

.role-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 700;
  color: #FF5722;
}

.role-desc {
  font-size: 10px;
  color: #888;
  line-height: 1.4;
}

/* Params Form */
.params-form {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.param-slider-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.param-slider-label {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: #333;
  font-weight: 500;
}

.param-slider-value {
  font-family: 'JetBrains Mono', monospace;
  color: #FF5722;
  font-weight: 700;
}

.minimal-slider {
  -webkit-appearance: none;
  width: 100%;
  height: 4px;
  background: #EAEAEA;
  border-radius: 2px;
  outline: none;
}

.minimal-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #FF5722;
  cursor: pointer;
  border: 2px solid #FFF;
  box-shadow: 0 1px 4px rgba(0,0,0,0.2);
}

/* System Logs */
.system-logs {
  background: #000;
  color: #DDD;
  padding: 16px;
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
