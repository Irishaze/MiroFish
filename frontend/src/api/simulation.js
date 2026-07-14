import service, { requestWithRetry } from './index'

/**
 * 创建研究循环
 * @param {Object} data - { project_id, graph_id? }
 */
export const createSimulation = (data) => {
  return requestWithRetry(() => service.post('/api/simulation/create', data), 3, 1000)
}

/**
 * 准备研究循环环境（异步任务）
 * @param {Object} data - { simulation_id, entity_types?, num_queries?, num_candidates?, num_assays?, max_papers?, num_cycles?, force_regenerate? }
 */
export const prepareSimulation = (data) => {
  return requestWithRetry(() => service.post('/api/simulation/prepare', data), 3, 1000)
}

/**
 * 查询准备任务进度
 * @param {Object} data - { task_id?, simulation_id? }
 */
export const getPrepareStatus = (data) => {
  return service.post('/api/simulation/prepare/status', data)
}

/**
 * 获取模拟状态
 * @param {string} simulationId
 */
export const getSimulation = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}`)
}

/**
 * 获取研究循环运行参数
 * @param {string} simulationId
 */
export const getSimulationConfig = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/config`)
}

/**
 * 实时获取生成中的研究循环运行参数
 * @param {string} simulationId
 * @returns {Promise} 返回配置信息，包含元数据和配置内容
 */
export const getSimulationConfigRealtime = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/config/realtime`)
}

/**
 * 列出所有研究循环
 * @param {string} projectId - 可选，按项目ID过滤
 */
export const listSimulations = (projectId) => {
  const params = projectId ? { project_id: projectId } : {}
  return service.get('/api/simulation/list', { params })
}

/**
 * 启动研究循环
 * @param {Object} data - { simulation_id, enable_graph_memory_update? }
 */
export const startSimulation = (data) => {
  return requestWithRetry(() => service.post('/api/simulation/start', data), 3, 1000)
}

/**
 * 停止研究循环
 * @param {Object} data - { simulation_id }
 */
export const stopSimulation = (data) => {
  return service.post('/api/simulation/stop', data)
}

/**
 * 获取研究循环运行实时状态
 * @param {string} simulationId
 */
export const getRunStatus = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/run-status`)
}

/**
 * 获取研究循环运行详细状态（包含最近动作）
 * @param {string} simulationId
 */
export const getRunStatusDetail = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/run-status/detail`)
}

/**
 * 获取最终排名的假设列表（含Elo评分）
 * @param {string} simulationId
 */
export const getSimulationHypotheses = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/hypotheses`)
}

/**
 * 获取评估视角（assay）候选及锦标赛排名
 * @param {string} simulationId
 */
export const getSimulationAssays = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/assays`)
}

/**
 * 获取检索到的文献证据
 * @param {string} simulationId
 */
export const getSimulationEvidence = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/evidence`)
}

/**
 * 获取研究循环时间线（按轮次汇总）
 * @param {string} simulationId
 * @param {number} startRound - 起始轮次
 * @param {number} endRound - 结束轮次
 */
export const getSimulationTimeline = (simulationId, startRound = 0, endRound = null) => {
  const params = { start_round: startRound }
  if (endRound !== null) {
    params.end_round = endRound
  }
  return service.get(`/api/simulation/${simulationId}/timeline`, { params })
}

/**
 * 获取Agent统计信息
 * @param {string} simulationId
 */
export const getAgentStats = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/agent-stats`)
}

/**
 * 获取模拟动作历史
 * @param {string} simulationId
 * @param {Object} params - { limit, offset, platform, agent_id, round_num }
 */
export const getSimulationActions = (simulationId, params = {}) => {
  return service.get(`/api/simulation/${simulationId}/actions`, { params })
}

/**
 * 关闭模拟环境（优雅退出）
 * @param {Object} data - { simulation_id, timeout? }
 */
export const closeSimulationEnv = (data) => {
  return service.post('/api/simulation/close-env', data)
}

/**
 * 获取模拟环境状态
 * @param {Object} data - { simulation_id }
 */
export const getEnvStatus = (data) => {
  return service.post('/api/simulation/env-status', data)
}

/**
 * 咨询单个固定角色
 * @param {Object} data - { simulation_id, role, prompt, timeout? }
 */
export const consultAgent = (data) => {
  return requestWithRetry(() => service.post('/api/simulation/interview', data), 3, 1000)
}

/**
 * 批量咨询多个固定角色
 * @param {Object} data - { simulation_id, consultations: [{ role, prompt }] }
 */
export const consultAgentsBatch = (data) => {
  return requestWithRetry(() => service.post('/api/simulation/interview/batch', data), 3, 1000)
}

/**
 * 获取历史研究循环列表（带项目详情）
 * 用于首页历史项目展示
 * @param {number} limit - 返回数量限制
 */
export const getSimulationHistory = (limit = 20) => {
  return service.get('/api/simulation/history', { params: { limit } })
}

