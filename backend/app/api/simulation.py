"""
研究循环相关API路由
Step2: Zep实体读取与过滤、科学假设检验研究循环的准备与运行（全程自动化）
"""

import os
import csv
import json
import traceback
from flask import request, jsonify, send_file

from . import simulation_bp
from ..config import Config
from ..services.zep_entity_reader import ZepEntityReader
from ..services.simulation_manager import SimulationManager, SimulationStatus
from ..services.simulation_runner import SimulationRunner, RunnerStatus
from ..utils.logger import get_logger
from ..utils.locale import t, get_locale, set_locale
from ..models.project import ProjectManager

logger = get_logger('mirofish.api.simulation')


# Consult prompt 优化前缀
# 添加此前缀可以避免Agent调用工具，直接用文本回复
CONSULT_PROMPT_PREFIX = "结合你的角色定位与本轮研究循环的完整上下文，不调用任何工具直接用文本回复我："


def optimize_consult_prompt(prompt: str) -> str:
    """优化咨询提问，添加前缀避免Agent调用工具"""
    if not prompt:
        return prompt
    if prompt.startswith(CONSULT_PROMPT_PREFIX):
        return prompt
    return f"{CONSULT_PROMPT_PREFIX}{prompt}"


# ============== 实体读取接口 ==============

@simulation_bp.route('/entities/<graph_id>', methods=['GET'])
def get_graph_entities(graph_id: str):
    """
    获取图谱中的所有实体（已过滤）

    只返回符合预定义实体类型的节点（Labels不只是Entity的节点）

    Query参数：
        entity_types: 逗号分隔的实体类型列表（可选，用于进一步过滤）
        enrich: 是否获取相关边信息（默认true）
    """
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({"success": False, "error": t('api.zepApiKeyMissing')}), 500

        entity_types_str = request.args.get('entity_types', '')
        entity_types = [x.strip() for x in entity_types_str.split(',') if x.strip()] if entity_types_str else None
        enrich = request.args.get('enrich', 'true').lower() == 'true'

        logger.info(f"获取图谱实体: graph_id={graph_id}, entity_types={entity_types}, enrich={enrich}")

        reader = ZepEntityReader()
        result = reader.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=entity_types,
            enrich_with_edges=enrich
        )

        return jsonify({"success": True, "data": result.to_dict()})

    except Exception as e:
        logger.error(f"获取图谱实体失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/entities/<graph_id>/<entity_uuid>', methods=['GET'])
def get_entity_detail(graph_id: str, entity_uuid: str):
    """获取单个实体的详细信息"""
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({"success": False, "error": t('api.zepApiKeyMissing')}), 500

        reader = ZepEntityReader()
        entity = reader.get_entity_with_context(graph_id, entity_uuid)

        if not entity:
            return jsonify({"success": False, "error": t('api.entityNotFound', id=entity_uuid)}), 404

        return jsonify({"success": True, "data": entity.to_dict()})

    except Exception as e:
        logger.error(f"获取实体详情失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/entities/<graph_id>/by-type/<entity_type>', methods=['GET'])
def get_entities_by_type(graph_id: str, entity_type: str):
    """获取指定类型的所有实体"""
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({"success": False, "error": t('api.zepApiKeyMissing')}), 500

        enrich = request.args.get('enrich', 'true').lower() == 'true'

        reader = ZepEntityReader()
        entities = reader.get_entities_by_type(graph_id=graph_id, entity_type=entity_type, enrich_with_edges=enrich)

        return jsonify({
            "success": True,
            "data": {
                "entity_type": entity_type,
                "count": len(entities),
                "entities": [e.to_dict() for e in entities]
            }
        })

    except Exception as e:
        logger.error(f"获取实体失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


# ============== 研究循环管理接口 ==============

@simulation_bp.route('/create', methods=['POST'])
def create_simulation():
    """
    创建新的研究循环

    请求（JSON）：
        {
            "project_id": "proj_xxxx",      // 必填
            "graph_id": "mirofish_xxxx"     // 可选，如不提供则从project获取
        }
    """
    try:
        data = request.get_json() or {}

        project_id = data.get('project_id')
        if not project_id:
            return jsonify({"success": False, "error": t('api.requireProjectId')}), 400

        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({"success": False, "error": t('api.projectNotFound', id=project_id)}), 404

        graph_id = data.get('graph_id') or project.graph_id
        if not graph_id:
            return jsonify({"success": False, "error": t('api.graphNotBuilt')}), 400

        manager = SimulationManager()
        state = manager.create_simulation(project_id=project_id, graph_id=graph_id)

        return jsonify({"success": True, "data": state.to_dict()})

    except Exception as e:
        logger.error(f"创建研究循环失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


def _check_simulation_prepared(simulation_id: str) -> tuple:
    """
    检查研究循环是否已经准备完成

    检查条件：state.json 存在且 status 为已准备状态，simulation_config.json 存在
    """
    simulation_dir = os.path.join(Config.UPLOAD_FOLDER, 'simulations', simulation_id)

    if not os.path.exists(simulation_dir):
        return False, {"reason": "研究循环目录不存在"}

    required_files = ["state.json", "simulation_config.json"]
    existing_files = []
    missing_files = []
    for f in required_files:
        file_path = os.path.join(simulation_dir, f)
        if os.path.exists(file_path):
            existing_files.append(f)
        else:
            missing_files.append(f)

    if missing_files:
        return False, {"reason": "缺少必要文件", "missing_files": missing_files, "existing_files": existing_files}

    state_file = os.path.join(simulation_dir, "state.json")
    try:
        with open(state_file, 'r', encoding='utf-8') as f:
            state_data = json.load(f)

        status = state_data.get("status", "")
        config_generated = state_data.get("config_generated", False)

        prepared_statuses = ["ready", "preparing", "running", "completed", "stopped", "failed"]
        if status in prepared_statuses and config_generated:
            if status == "preparing":
                try:
                    state_data["status"] = "ready"
                    from datetime import datetime
                    state_data["updated_at"] = datetime.now().isoformat()
                    with open(state_file, 'w', encoding='utf-8') as f:
                        json.dump(state_data, f, ensure_ascii=False, indent=2)
                    status = "ready"
                except Exception as e:
                    logger.warning(f"自动更新状态失败: {e}")

            return True, {
                "status": status,
                "entities_count": state_data.get("entities_count", 0),
                "entity_types": state_data.get("entity_types", []),
                "config_generated": config_generated,
                "created_at": state_data.get("created_at"),
                "updated_at": state_data.get("updated_at"),
                "existing_files": existing_files
            }
        else:
            return False, {
                "reason": f"状态不在已准备列表中或config_generated为false: status={status}, config_generated={config_generated}",
                "status": status,
                "config_generated": config_generated
            }

    except Exception as e:
        return False, {"reason": f"读取状态文件失败: {str(e)}"}


@simulation_bp.route('/prepare', methods=['POST'])
def prepare_simulation():
    """
    准备研究循环环境（异步任务）

    这是一个耗时操作，接口会立即返回task_id，
    使用 GET /api/simulation/prepare/status 查询进度

    请求（JSON）：
        {
            "simulation_id": "sim_xxxx",     // 必填
            "entity_types": [...],           // 可选，指定实体类型
            "num_queries": 3,                // 可选，Robin默认值3
            "num_candidates": 5,             // 可选，Robin默认值5
            "num_assays": 3,                 // 可选，Robin默认值3
            "max_papers": 15,                // 可选，硬上限20
            "num_cycles": 1,                 // 可选，Generation->Evolution循环轮数
            "force_regenerate": false        // 可选
        }
    """
    import threading
    from ..models.task import TaskManager, TaskStatus

    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({"success": False, "error": t('api.requireSimulationId')}), 400

        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)

        if not state:
            return jsonify({"success": False, "error": t('api.simulationNotFound', id=simulation_id)}), 404

        force_regenerate = data.get('force_regenerate', False)

        if not force_regenerate:
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
            if is_prepared:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "ready",
                        "message": t('api.alreadyPrepared'),
                        "already_prepared": True,
                        "prepare_info": prepare_info
                    }
                })

        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({"success": False, "error": t('api.projectNotFound', id=state.project_id)}), 404

        research_question = project.simulation_requirement or ""
        if not research_question:
            return jsonify({"success": False, "error": t('api.projectMissingRequirement')}), 400

        entity_types_list = data.get('entity_types')
        num_queries = data.get('num_queries')
        num_candidates = data.get('num_candidates')
        num_assays = data.get('num_assays')
        max_papers = data.get('max_papers')
        num_cycles = data.get('num_cycles')

        try:
            reader = ZepEntityReader()
            filtered_preview = reader.filter_defined_entities(
                graph_id=state.graph_id,
                defined_entity_types=entity_types_list,
                enrich_with_edges=False
            )
            state.entities_count = filtered_preview.filtered_count
            state.entity_types = list(filtered_preview.entity_types)
        except Exception as e:
            logger.warning(f"同步获取实体数量失败（将在后台任务中重试）: {e}")

        task_manager = TaskManager()
        task_id = task_manager.create_task(
            task_type="simulation_prepare",
            metadata={"simulation_id": simulation_id, "project_id": state.project_id}
        )

        state.status = SimulationStatus.PREPARING
        manager._save_simulation_state(state)

        current_locale = get_locale()

        def run_prepare():
            set_locale(current_locale)
            try:
                task_manager.update_task(task_id, status=TaskStatus.PROCESSING, progress=0,
                                          message=t('progress.startPreparingEnv'))

                def progress_callback(stage, progress, message, **kwargs):
                    stage_weights = {"reading": (0, 40), "generating_config": (40, 100)}
                    start, end = stage_weights.get(stage, (0, 100))
                    current_progress = int(start + (end - start) * progress / 100)
                    task_manager.update_task(task_id, progress=current_progress, message=message)

                result_state = manager.prepare_simulation(
                    simulation_id=simulation_id,
                    research_question=research_question,
                    defined_entity_types=entity_types_list,
                    num_queries=num_queries,
                    num_candidates=num_candidates,
                    num_assays=num_assays,
                    max_papers=max_papers,
                    num_cycles=num_cycles,
                    progress_callback=progress_callback,
                )

                task_manager.complete_task(task_id, result=result_state.to_simple_dict())

            except Exception as e:
                logger.error(f"准备研究循环失败: {str(e)}")
                task_manager.fail_task(task_id, str(e))
                state = manager.get_simulation(simulation_id)
                if state:
                    state.status = SimulationStatus.FAILED
                    state.error = str(e)
                    manager._save_simulation_state(state)

        thread = threading.Thread(target=run_prepare, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "task_id": task_id,
                "status": "preparing",
                "message": t('api.prepareStarted'),
                "already_prepared": False,
                "expected_entities_count": state.entities_count,
                "entity_types": state.entity_types
            }
        })

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    except Exception as e:
        logger.error(f"启动准备任务失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/prepare/status', methods=['POST'])
def get_prepare_status():
    """查询准备任务进度"""
    from ..models.task import TaskManager

    try:
        data = request.get_json() or {}
        task_id = data.get('task_id')
        simulation_id = data.get('simulation_id')

        if simulation_id:
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
            if is_prepared:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "ready",
                        "progress": 100,
                        "message": t('api.alreadyPrepared'),
                        "already_prepared": True,
                        "prepare_info": prepare_info
                    }
                })

        if not task_id:
            if simulation_id:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "not_started",
                        "progress": 0,
                        "message": t('api.notStartedPrepare'),
                        "already_prepared": False
                    }
                })
            return jsonify({"success": False, "error": t('api.requireTaskOrSimId')}), 400

        task_manager = TaskManager()
        task = task_manager.get_task(task_id)

        if not task:
            if simulation_id:
                is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
                if is_prepared:
                    return jsonify({
                        "success": True,
                        "data": {
                            "simulation_id": simulation_id,
                            "task_id": task_id,
                            "status": "ready",
                            "progress": 100,
                            "message": t('api.taskCompletedPrepared'),
                            "already_prepared": True,
                            "prepare_info": prepare_info
                        }
                    })
            return jsonify({"success": False, "error": t('api.taskNotFound', id=task_id)}), 404

        task_dict = task.to_dict()
        task_dict["already_prepared"] = False

        return jsonify({"success": True, "data": task_dict})

    except Exception as e:
        logger.error(f"查询任务状态失败: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


@simulation_bp.route('/<simulation_id>', methods=['GET'])
def get_simulation(simulation_id: str):
    """获取研究循环状态"""
    try:
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)

        if not state:
            return jsonify({"success": False, "error": t('api.simulationNotFound', id=simulation_id)}), 404

        result = state.to_dict()
        if state.status == SimulationStatus.READY:
            result["run_instructions"] = manager.get_run_instructions(simulation_id)

        return jsonify({"success": True, "data": result})

    except Exception as e:
        logger.error(f"获取研究循环状态失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/list', methods=['GET'])
def list_simulations():
    """列出所有研究循环"""
    try:
        project_id = request.args.get('project_id')
        manager = SimulationManager()
        simulations = manager.list_simulations(project_id=project_id)

        return jsonify({"success": True, "data": [s.to_dict() for s in simulations], "count": len(simulations)})

    except Exception as e:
        logger.error(f"列出研究循环失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


def _get_report_id_for_simulation(simulation_id: str) -> str:
    """获取 simulation 对应的最新 report_id"""
    reports_dir = os.path.join(os.path.dirname(__file__), '../../uploads/reports')
    if not os.path.exists(reports_dir):
        return None

    matching_reports = []
    try:
        for report_folder in os.listdir(reports_dir):
            report_path = os.path.join(reports_dir, report_folder)
            if not os.path.isdir(report_path):
                continue
            meta_file = os.path.join(report_path, "meta.json")
            if not os.path.exists(meta_file):
                continue
            try:
                with open(meta_file, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                if meta.get("simulation_id") == simulation_id:
                    matching_reports.append({
                        "report_id": meta.get("report_id"),
                        "created_at": meta.get("created_at", ""),
                    })
            except Exception:
                continue

        if not matching_reports:
            return None
        matching_reports.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return matching_reports[0].get("report_id")
    except Exception as e:
        logger.warning(f"查找 simulation {simulation_id} 的 report 失败: {e}")
        return None


@simulation_bp.route('/history', methods=['GET'])
def get_simulation_history():
    """获取历史研究循环列表（带项目详情），用于首页历史项目展示"""
    try:
        limit = request.args.get('limit', 20, type=int)

        manager = SimulationManager()
        simulations = manager.list_simulations()[:limit]

        enriched_simulations = []
        for sim in simulations:
            sim_dict = sim.to_dict()

            config = manager.get_simulation_config(sim.simulation_id)
            if config:
                sim_dict["research_question"] = config.get("research_question", "")
            else:
                sim_dict["research_question"] = sim.research_question

            run_state = SimulationRunner.get_run_state(sim.simulation_id)
            if run_state:
                sim_dict["current_round"] = run_state.current_round
                sim_dict["runner_status"] = run_state.runner_status.value
                sim_dict["total_rounds"] = run_state.total_rounds
            else:
                sim_dict["current_round"] = 0
                sim_dict["runner_status"] = "idle"
                sim_dict["total_rounds"] = 1 + sim.num_cycles

            project = ProjectManager.get_project(sim.project_id)
            if project and hasattr(project, 'files') and project.files:
                sim_dict["files"] = [{"filename": f.get("filename", "未知文件")} for f in project.files[:3]]
            else:
                sim_dict["files"] = []

            sim_dict["report_id"] = _get_report_id_for_simulation(sim.simulation_id)
            sim_dict["version"] = "v2.0.0"

            try:
                sim_dict["created_date"] = sim_dict.get("created_at", "")[:10]
            except Exception:
                sim_dict["created_date"] = ""

            enriched_simulations.append(sim_dict)

        return jsonify({"success": True, "data": enriched_simulations, "count": len(enriched_simulations)})

    except Exception as e:
        logger.error(f"获取历史研究循环失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/<simulation_id>/config/realtime', methods=['GET'])
def get_simulation_config_realtime(simulation_id: str):
    """实时获取研究循环运行参数（用于在生成过程中实时查看进度）"""
    from datetime import datetime

    try:
        sim_dir = os.path.join(Config.UPLOAD_FOLDER, 'simulations', simulation_id)

        if not os.path.exists(sim_dir):
            return jsonify({"success": False, "error": t('api.simulationNotFound', id=simulation_id)}), 404

        config_file = os.path.join(sim_dir, "simulation_config.json")
        file_exists = os.path.exists(config_file)
        config = None
        file_modified_at = None

        if file_exists:
            file_stat = os.stat(config_file)
            file_modified_at = datetime.fromtimestamp(file_stat.st_mtime).isoformat()
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"读取 config 文件失败（可能正在写入中）: {e}")
                config = None

        is_generating = False
        config_generated = False
        state_file = os.path.join(sim_dir, "state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    state_data = json.load(f)
                    status = state_data.get("status", "")
                    is_generating = status == "preparing"
                    config_generated = state_data.get("config_generated", False)
            except Exception:
                pass

        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "file_exists": file_exists,
                "file_modified_at": file_modified_at,
                "is_generating": is_generating,
                "config_generated": config_generated,
                "config": config
            }
        })

    except Exception as e:
        logger.error(f"实时获取Config失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/<simulation_id>/config', methods=['GET'])
def get_simulation_config(simulation_id: str):
    """获取研究循环运行参数"""
    try:
        manager = SimulationManager()
        config = manager.get_simulation_config(simulation_id)

        if not config:
            return jsonify({"success": False, "error": t('api.configNotFound')}), 404

        return jsonify({"success": True, "data": config})

    except Exception as e:
        logger.error(f"获取配置失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/<simulation_id>/config/download', methods=['GET'])
def download_simulation_config(simulation_id: str):
    """下载研究循环配置文件"""
    try:
        manager = SimulationManager()
        sim_dir = manager._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")

        if not os.path.exists(config_path):
            return jsonify({"success": False, "error": t('api.configFileNotFound')}), 404

        return send_file(config_path, as_attachment=True, download_name="simulation_config.json")

    except Exception as e:
        logger.error(f"下载配置失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/script/<script_name>/download', methods=['GET'])
def download_simulation_script(script_name: str):
    """下载研究循环脚本文件（位于 backend/scripts/）"""
    try:
        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts'))

        allowed_scripts = ["run_research_loop.py"]
        if script_name not in allowed_scripts:
            return jsonify({"success": False, "error": t('api.unknownScript', name=script_name, allowed=allowed_scripts)}), 400

        script_path = os.path.join(scripts_dir, script_name)
        if not os.path.exists(script_path):
            return jsonify({"success": False, "error": t('api.scriptFileNotFound', name=script_name)}), 404

        return send_file(script_path, as_attachment=True, download_name=script_name)

    except Exception as e:
        logger.error(f"下载脚本失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


# ============== 研究循环运行控制接口 ==============

@simulation_bp.route('/start', methods=['POST'])
def start_simulation():
    """
    开始运行研究循环

    请求（JSON）：
        {
            "simulation_id": "sim_xxxx",          // 必填
            "enable_graph_memory_update": false,  // 可选：是否将Agent活动动态更新到Zep图谱记忆
            "force": false                        // 可选：强制重新开始
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({"success": False, "error": t('api.requireSimulationId')}), 400

        enable_graph_memory_update = data.get('enable_graph_memory_update', False)
        force = data.get('force', False)

        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)

        if not state:
            return jsonify({"success": False, "error": t('api.simulationNotFound', id=simulation_id)}), 404

        force_restarted = False

        if state.status != SimulationStatus.READY:
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)

            if is_prepared:
                if state.status == SimulationStatus.RUNNING:
                    run_state = SimulationRunner.get_run_state(simulation_id)
                    if run_state and run_state.runner_status.value == "running":
                        if force:
                            logger.info(f"强制模式：停止运行中的研究循环 {simulation_id}")
                            try:
                                SimulationRunner.stop_simulation(simulation_id)
                            except Exception as e:
                                logger.warning(f"停止研究循环时出现警告: {str(e)}")
                        else:
                            return jsonify({"success": False, "error": t('api.simRunningForceHint')}), 400

                if force:
                    logger.info(f"强制模式：清理研究循环日志 {simulation_id}")
                    cleanup_result = SimulationRunner.cleanup_simulation_logs(simulation_id)
                    if not cleanup_result.get("success"):
                        logger.warning(f"清理日志时出现警告: {cleanup_result.get('errors')}")
                    force_restarted = True

                state.status = SimulationStatus.READY
                manager._save_simulation_state(state)
            else:
                return jsonify({"success": False, "error": t('api.simNotReady', status=state.status.value)}), 400

        graph_id = None
        if enable_graph_memory_update:
            graph_id = state.graph_id
            if not graph_id:
                project = ProjectManager.get_project(state.project_id)
                if project:
                    graph_id = project.graph_id
            if not graph_id:
                return jsonify({"success": False, "error": t('api.graphIdRequiredForMemory')}), 400

        run_state = SimulationRunner.start_simulation(
            simulation_id=simulation_id,
            enable_graph_memory_update=enable_graph_memory_update,
            graph_id=graph_id
        )

        state.status = SimulationStatus.RUNNING
        manager._save_simulation_state(state)

        response_data = run_state.to_dict()
        response_data['graph_memory_update_enabled'] = enable_graph_memory_update
        response_data['force_restarted'] = force_restarted
        if enable_graph_memory_update:
            response_data['graph_id'] = graph_id

        return jsonify({"success": True, "data": response_data})

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"启动研究循环失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/stop', methods=['POST'])
def stop_simulation():
    """停止研究循环"""
    try:
        data = request.get_json() or {}
        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({"success": False, "error": t('api.requireSimulationId')}), 400

        run_state = SimulationRunner.stop_simulation(simulation_id)

        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        if state:
            state.status = SimulationStatus.PAUSED
            manager._save_simulation_state(state)

        return jsonify({"success": True, "data": run_state.to_dict()})

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"停止研究循环失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


# ============== 实时状态监控接口 ==============

@simulation_bp.route('/<simulation_id>/run-status', methods=['GET'])
def get_run_status(simulation_id: str):
    """获取研究循环运行实时状态（用于前端轮询）"""
    try:
        run_state = SimulationRunner.get_run_state(simulation_id)

        if not run_state:
            return jsonify({
                "success": True,
                "data": {
                    "simulation_id": simulation_id,
                    "runner_status": "idle",
                    "current_round": 0,
                    "total_rounds": 0,
                    "progress_percent": 0,
                    "actions_count": 0,
                }
            })

        return jsonify({"success": True, "data": run_state.to_dict()})

    except Exception as e:
        logger.error(f"获取运行状态失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/<simulation_id>/run-status/detail', methods=['GET'])
def get_run_status_detail(simulation_id: str):
    """获取研究循环运行详细状态（包含所有动作），用于前端展示实时动态"""
    try:
        run_state = SimulationRunner.get_run_state(simulation_id)

        if not run_state:
            return jsonify({
                "success": True,
                "data": {"simulation_id": simulation_id, "runner_status": "idle", "all_actions": []}
            })

        all_actions = SimulationRunner.get_all_actions(simulation_id=simulation_id)

        current_round = run_state.current_round
        recent_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id, round_num=current_round
        ) if current_round > 0 else []

        result = run_state.to_dict()
        result["all_actions"] = [a.to_dict() for a in all_actions]
        result["recent_actions"] = [a.to_dict() for a in recent_actions]

        return jsonify({"success": True, "data": result})

    except Exception as e:
        logger.error(f"获取详细状态失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/<simulation_id>/actions', methods=['GET'])
def get_simulation_actions(simulation_id: str):
    """获取研究循环中的Agent动作历史"""
    try:
        limit = request.args.get('limit', 100, type=int)
        offset = request.args.get('offset', 0, type=int)
        round_num = request.args.get('round_num', type=int)

        actions = SimulationRunner.get_actions(
            simulation_id=simulation_id, limit=limit, offset=offset, round_num=round_num
        )

        return jsonify({"success": True, "data": {"count": len(actions), "actions": [a.to_dict() for a in actions]}})

    except Exception as e:
        logger.error(f"获取动作历史失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/<simulation_id>/timeline', methods=['GET'])
def get_simulation_timeline(simulation_id: str):
    """获取研究循环时间线（按轮次汇总），用于前端展示进度条和时间线视图"""
    try:
        start_round = request.args.get('start_round', 0, type=int)
        end_round = request.args.get('end_round', type=int)

        timeline = SimulationRunner.get_timeline(simulation_id=simulation_id, start_round=start_round, end_round=end_round)

        return jsonify({"success": True, "data": {"rounds_count": len(timeline), "timeline": timeline}})

    except Exception as e:
        logger.error(f"获取时间线失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/<simulation_id>/agent-stats', methods=['GET'])
def get_agent_stats(simulation_id: str):
    """获取每个固定角色的统计信息"""
    try:
        stats = SimulationRunner.get_agent_stats(simulation_id)
        return jsonify({"success": True, "data": {"agents_count": len(stats), "stats": stats}})

    except Exception as e:
        logger.error(f"获取Agent统计失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


# ============== 研究成果查询接口 ==============

def _read_ranking_csv(path: str):
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return list(csv.DictReader(f))


@simulation_bp.route('/<simulation_id>/hypotheses', methods=['GET'])
def get_simulation_hypotheses(simulation_id: str):
    """获取研究循环最终排名的假设列表（含Elo评分）"""
    try:
        sim_dir = os.path.join(Config.UPLOAD_FOLDER, 'simulations', simulation_id)
        ranked_path = os.path.join(sim_dir, "ranked_hypotheses.csv")

        hypotheses = _read_ranking_csv(ranked_path)

        return jsonify({"success": True, "data": {"count": len(hypotheses), "hypotheses": hypotheses}})

    except Exception as e:
        logger.error(f"获取假设列表失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/<simulation_id>/assays', methods=['GET'])
def get_simulation_assays(simulation_id: str):
    """获取研究循环中评估视角（assay）候选及其锦标赛排名"""
    try:
        sim_dir = os.path.join(Config.UPLOAD_FOLDER, 'simulations', simulation_id)
        ranking_path = os.path.join(sim_dir, "research", "assay_ranking_results.csv")

        matches = _read_ranking_csv(ranking_path)

        return jsonify({"success": True, "data": {"count": len(matches), "matches": matches}})

    except Exception as e:
        logger.error(f"获取评估视角失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/<simulation_id>/evidence', methods=['GET'])
def get_simulation_evidence(simulation_id: str):
    """获取研究循环检索到的文献证据（含来源引用）"""
    try:
        sim_dir = os.path.join(Config.UPLOAD_FOLDER, 'simulations', simulation_id)
        evidence = []

        for kind, dirname in [("hypothesis", "hypothesis_literature_reviews"), ("assay", "assay_literature_reviews")]:
            reviews_dir = os.path.join(sim_dir, "research", dirname)
            if not os.path.exists(reviews_dir):
                continue
            for filename in os.listdir(reviews_dir):
                file_path = os.path.join(reviews_dir, filename)
                if not os.path.isfile(file_path):
                    continue
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                evidence.append({"kind": kind, "candidate_id": filename.replace(".txt", ""), "content": content})

        return jsonify({"success": True, "data": {"count": len(evidence), "evidence": evidence}})

    except Exception as e:
        logger.error(f"获取文献证据失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


# ============== Consult 接口（咨询固定角色） ==============

@simulation_bp.route('/interview', methods=['POST'])
def consult_agent():
    """
    咨询单个固定角色（Generation/Reflection/Ranking/Tournament/Evolution/Proximity/MetaReview之一）

    注意：此功能需要研究循环环境处于运行状态（主流程完成后进入待命模式）

    请求（JSON）：
        {
            "simulation_id": "sim_xxxx",   // 必填
            "role": "Reflection",          // 必填，7个固定角色之一
            "prompt": "为什么假设2的证据被判定为弱？",  // 必填
            "timeout": 60                  // 可选，默认60
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        role = data.get('role')
        prompt = data.get('prompt')
        timeout = data.get('timeout', 60)

        if not simulation_id:
            return jsonify({"success": False, "error": t('api.requireSimulationId')}), 400
        if not role:
            return jsonify({"success": False, "error": t('api.requireAgentId')}), 400
        if not prompt:
            return jsonify({"success": False, "error": t('api.requirePrompt')}), 400

        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({"success": False, "error": t('api.envNotRunning')}), 400

        optimized_prompt = optimize_consult_prompt(prompt)

        result = SimulationRunner.consult_agent(
            simulation_id=simulation_id, role=role, prompt=optimized_prompt, timeout=timeout
        )

        return jsonify({"success": result.get("success", False), "data": result})

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except TimeoutError as e:
        return jsonify({"success": False, "error": t('api.interviewTimeout', error=str(e))}), 504
    except Exception as e:
        logger.error(f"Consult失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/interview/batch', methods=['POST'])
def consult_agents_batch():
    """
    批量咨询多个固定角色

    请求（JSON）：
        {
            "simulation_id": "sim_xxxx",
            "consultations": [
                {"role": "Reflection", "prompt": "..."},
                {"role": "Ranking", "prompt": "..."}
            ],
            "timeout": 120
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        consultations = data.get('consultations') or data.get('interviews')
        timeout = data.get('timeout', 120)

        if not simulation_id:
            return jsonify({"success": False, "error": t('api.requireSimulationId')}), 400
        if not consultations or not isinstance(consultations, list):
            return jsonify({"success": False, "error": t('api.requireInterviews')}), 400

        for i, item in enumerate(consultations):
            if 'role' not in item:
                return jsonify({"success": False, "error": t('api.interviewListMissingAgentId', index=i + 1)}), 400
            if 'prompt' not in item:
                return jsonify({"success": False, "error": t('api.interviewListMissingPrompt', index=i + 1)}), 400

        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({"success": False, "error": t('api.envNotRunning')}), 400

        optimized = [{"role": c["role"], "prompt": optimize_consult_prompt(c.get("prompt", ""))} for c in consultations]

        result = SimulationRunner.consult_agents_batch(simulation_id=simulation_id, consultations=optimized, timeout=timeout)

        return jsonify({"success": result.get("success", False), "data": result})

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except TimeoutError as e:
        return jsonify({"success": False, "error": t('api.batchInterviewTimeout', error=str(e))}), 504
    except Exception as e:
        logger.error(f"批量Consult失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/interview/all', methods=['POST'])
def consult_all_agents():
    """
    全局咨询 - 使用相同问题咨询全部7个固定角色

    请求（JSON）：
        {
            "simulation_id": "sim_xxxx",
            "prompt": "整体而言，你如何评价本轮研究循环的结论？",
            "timeout": 180
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        prompt = data.get('prompt')
        timeout = data.get('timeout', 180)

        if not simulation_id:
            return jsonify({"success": False, "error": t('api.requireSimulationId')}), 400
        if not prompt:
            return jsonify({"success": False, "error": t('api.requirePrompt')}), 400

        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({"success": False, "error": t('api.envNotRunning')}), 400

        optimized_prompt = optimize_consult_prompt(prompt)

        result = SimulationRunner.consult_all_roles(simulation_id=simulation_id, prompt=optimized_prompt, timeout=timeout)

        return jsonify({"success": result.get("success", False), "data": result})

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except TimeoutError as e:
        return jsonify({"success": False, "error": t('api.globalInterviewTimeout', error=str(e))}), 504
    except Exception as e:
        logger.error(f"全局Consult失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/env-status', methods=['POST'])
def get_env_status():
    """获取研究循环环境状态（检查是否可以接收consult命令）"""
    try:
        data = request.get_json() or {}
        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({"success": False, "error": t('api.requireSimulationId')}), 400

        env_alive = SimulationRunner.check_env_alive(simulation_id)
        env_status = SimulationRunner.get_env_status_detail(simulation_id)

        message = t('api.envRunning') if env_alive else t('api.envNotRunningShort')

        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "env_alive": env_alive,
                "status": env_status.get("status"),
                "message": message
            }
        })

    except Exception as e:
        logger.error(f"获取环境状态失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@simulation_bp.route('/close-env', methods=['POST'])
def close_simulation_env():
    """
    关闭研究循环环境

    向研究循环发送关闭环境命令，使其优雅退出等待命令模式。
    这不同于 /stop（强制终止进程）。
    """
    try:
        data = request.get_json() or {}
        simulation_id = data.get('simulation_id')
        timeout = data.get('timeout', 30)

        if not simulation_id:
            return jsonify({"success": False, "error": t('api.requireSimulationId')}), 400

        result = SimulationRunner.close_simulation_env(simulation_id=simulation_id, timeout=timeout)

        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        if state:
            state.status = SimulationStatus.COMPLETED
            manager._save_simulation_state(state)

        return jsonify({"success": result.get("success", False), "data": result})

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"关闭环境失败: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500
