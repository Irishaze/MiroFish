"""
研究循环运行器
在后台运行两阶段Elo锦标赛研究循环（run_research_loop.py），记录动作，支持实时状态监控

相较旧版（OASIS双平台版本），不再有 twitter/reddit 两条独立轨道——
只有一个单进程的研究循环脚本，动作统一写入 research/actions.jsonl
"""

import os
import sys
import json
import time
import threading
import subprocess
import signal
import atexit
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from queue import Queue

from ..config import Config
from ..utils.logger import get_logger
from ..utils.locale import get_locale, set_locale
from .zep_graph_memory_updater import ZepGraphMemoryManager
from .simulation_ipc import SimulationIPCClient, CommandType, IPCResponse

logger = get_logger('mirofish.simulation_runner')

# 标记是否已注册清理函数
_cleanup_registered = False

# 平台检测
IS_WINDOWS = sys.platform == 'win32'


class RunnerStatus(str, Enum):
    """运行器状态"""
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AgentAction:
    """研究循环中的一个动作记录"""
    round_num: int
    timestamp: str
    agent_id: int
    agent_name: str  # Generation / Reflection / Ranking / Tournament / Evolution / Proximity / MetaReview
    action_type: str  # PROPOSE_ASSAY, REVIEW_ASSAY, ASSAY_MATCH, SELECT_ASSAY, SYNTHESIZE_GENERATION_GOAL,
                       # PROPOSE_HYPOTHESIS, REVIEW_HYPOTHESIS, HYPOTHESIS_MATCH, REFINE_HYPOTHESIS, META_REVIEW ...
    action_args: Dict[str, Any] = field(default_factory=dict)
    result: Optional[str] = None
    success: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_num": self.round_num,
            "timestamp": self.timestamp,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "action_type": self.action_type,
            "action_args": self.action_args,
            "result": self.result,
            "success": self.success,
        }


@dataclass
class SimulationRunState:
    """研究循环运行状态（实时）"""
    simulation_id: str
    runner_status: RunnerStatus = RunnerStatus.IDLE

    # 进度信息
    current_round: int = 0
    total_rounds: int = 0
    actions_count: int = 0
    is_running: bool = False
    is_completed: bool = False

    # 最近动作（用于前端实时展示）
    recent_actions: List[AgentAction] = field(default_factory=list)
    max_recent_actions: int = 50

    # 时间戳
    started_at: Optional[str] = None
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None

    # 错误信息
    error: Optional[str] = None

    # 进程ID（用于停止）
    process_pid: Optional[int] = None

    def add_action(self, action: AgentAction):
        """添加动作到最近动作列表"""
        self.recent_actions.insert(0, action)
        if len(self.recent_actions) > self.max_recent_actions:
            self.recent_actions = self.recent_actions[:self.max_recent_actions]
        self.actions_count += 1
        self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "runner_status": self.runner_status.value,
            "current_round": self.current_round,
            "total_rounds": self.total_rounds,
            "progress_percent": round(self.current_round / max(self.total_rounds, 1) * 100, 1),
            "actions_count": self.actions_count,
            "total_actions_count": self.actions_count,
            "is_running": self.is_running,
            "is_completed": self.is_completed,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "process_pid": self.process_pid,
        }

    def to_detail_dict(self) -> Dict[str, Any]:
        """包含最近动作的详细信息"""
        result = self.to_dict()
        result["recent_actions"] = [a.to_dict() for a in self.recent_actions]
        return result


class SimulationRunner:
    """
    研究循环运行器

    负责：
    1. 在后台进程中运行 run_research_loop.py
    2. 解析运行日志，记录每个固定角色的动作
    3. 提供实时状态查询接口
    4. 支持停止操作与固定角色咨询（consult）
    """

    # 运行状态存储目录
    RUN_STATE_DIR = os.path.join(
        os.path.dirname(__file__),
        '../../uploads/simulations'
    )

    # 脚本目录
    SCRIPTS_DIR = os.path.join(
        os.path.dirname(__file__),
        '../../scripts'
    )

    # 内存中的运行状态
    _run_states: Dict[str, SimulationRunState] = {}
    _processes: Dict[str, subprocess.Popen] = {}
    _action_queues: Dict[str, Queue] = {}
    _monitor_threads: Dict[str, threading.Thread] = {}
    _stdout_files: Dict[str, Any] = {}
    _stderr_files: Dict[str, Any] = {}

    # 图谱记忆更新配置
    _graph_memory_enabled: Dict[str, bool] = {}

    @classmethod
    def get_run_state(cls, simulation_id: str) -> Optional[SimulationRunState]:
        """获取运行状态"""
        if simulation_id in cls._run_states:
            return cls._run_states[simulation_id]

        state = cls._load_run_state(simulation_id)
        if state:
            cls._run_states[simulation_id] = state
        return state

    @classmethod
    def _load_run_state(cls, simulation_id: str) -> Optional[SimulationRunState]:
        """从文件加载运行状态"""
        state_file = os.path.join(cls.RUN_STATE_DIR, simulation_id, "run_state.json")
        if not os.path.exists(state_file):
            return None

        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            state = SimulationRunState(
                simulation_id=simulation_id,
                runner_status=RunnerStatus(data.get("runner_status", "idle")),
                current_round=data.get("current_round", 0),
                total_rounds=data.get("total_rounds", 0),
                actions_count=data.get("actions_count", 0),
                is_running=data.get("is_running", False),
                is_completed=data.get("is_completed", False),
                started_at=data.get("started_at"),
                updated_at=data.get("updated_at", datetime.now().isoformat()),
                completed_at=data.get("completed_at"),
                error=data.get("error"),
                process_pid=data.get("process_pid"),
            )

            for a in data.get("recent_actions", []):
                state.recent_actions.append(AgentAction(
                    round_num=a.get("round_num", 0),
                    timestamp=a.get("timestamp", ""),
                    agent_id=a.get("agent_id", 0),
                    agent_name=a.get("agent_name", ""),
                    action_type=a.get("action_type", ""),
                    action_args=a.get("action_args", {}),
                    result=a.get("result"),
                    success=a.get("success", True),
                ))

            return state
        except Exception as e:
            logger.error(f"加载运行状态失败: {str(e)}")
            return None

    @classmethod
    def _save_run_state(cls, state: SimulationRunState):
        """保存运行状态到文件"""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, state.simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        state_file = os.path.join(sim_dir, "run_state.json")

        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state.to_detail_dict(), f, ensure_ascii=False, indent=2)

        cls._run_states[state.simulation_id] = state

    @classmethod
    def start_simulation(
        cls,
        simulation_id: str,
        enable_graph_memory_update: bool = False,
        graph_id: str = None
    ) -> SimulationRunState:
        """
        启动研究循环

        Args:
            simulation_id: 研究循环ID
            enable_graph_memory_update: 是否将Agent活动动态更新到Zep图谱
            graph_id: Zep图谱ID（启用图谱更新时必需）

        Returns:
            SimulationRunState
        """
        existing = cls.get_run_state(simulation_id)
        if existing and existing.runner_status in [RunnerStatus.RUNNING, RunnerStatus.STARTING]:
            raise ValueError(f"研究循环已在运行中: {simulation_id}")

        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")

        if not os.path.exists(config_path):
            raise ValueError(f"研究循环配置不存在，请先调用 /prepare 接口")

        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        # 阶段1（assay选择）算1轮，阶段2每个cycle算1轮
        total_rounds = 1 + config.get("num_cycles", 1)

        state = SimulationRunState(
            simulation_id=simulation_id,
            runner_status=RunnerStatus.STARTING,
            total_rounds=total_rounds,
            started_at=datetime.now().isoformat(),
        )

        cls._save_run_state(state)

        if enable_graph_memory_update:
            if not graph_id:
                raise ValueError("启用图谱记忆更新时必须提供 graph_id")
            try:
                ZepGraphMemoryManager.create_updater(simulation_id, graph_id)
                cls._graph_memory_enabled[simulation_id] = True
                logger.info(f"已启用图谱记忆更新: simulation_id={simulation_id}, graph_id={graph_id}")
            except Exception as e:
                logger.error(f"创建图谱记忆更新器失败: {e}")
                cls._graph_memory_enabled[simulation_id] = False
        else:
            cls._graph_memory_enabled[simulation_id] = False

        script_path = os.path.join(cls.SCRIPTS_DIR, "run_research_loop.py")
        if not os.path.exists(script_path):
            raise ValueError(f"脚本不存在: {script_path}")

        action_queue = Queue()
        cls._action_queues[simulation_id] = action_queue

        try:
            cmd = [sys.executable, script_path, "--config", config_path]

            main_log_path = os.path.join(sim_dir, "simulation.log")
            main_log_file = open(main_log_path, 'w', encoding='utf-8')

            env = os.environ.copy()
            env['PYTHONUTF8'] = '1'
            env['PYTHONIOENCODING'] = 'utf-8'
            env['MIROFISH_LOCALE'] = get_locale()

            process = subprocess.Popen(
                cmd,
                cwd=sim_dir,
                stdout=main_log_file,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                bufsize=1,
                env=env,
                start_new_session=True,
            )

            cls._stdout_files[simulation_id] = main_log_file
            cls._stderr_files[simulation_id] = None

            state.process_pid = process.pid
            state.runner_status = RunnerStatus.RUNNING
            state.is_running = True
            cls._processes[simulation_id] = process
            cls._save_run_state(state)

            current_locale = get_locale()

            monitor_thread = threading.Thread(
                target=cls._monitor_simulation,
                args=(simulation_id, current_locale),
                daemon=True
            )
            monitor_thread.start()
            cls._monitor_threads[simulation_id] = monitor_thread

            logger.info(f"研究循环启动成功: {simulation_id}, pid={process.pid}")

        except Exception as e:
            state.runner_status = RunnerStatus.FAILED
            state.error = str(e)
            cls._save_run_state(state)
            raise

        return state

    @classmethod
    def _monitor_simulation(cls, simulation_id: str, locale: str = 'zh'):
        """监控研究循环进程，解析动作日志"""
        set_locale(locale)
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        actions_log = os.path.join(sim_dir, "research", "actions.jsonl")

        process = cls._processes.get(simulation_id)
        state = cls.get_run_state(simulation_id)

        if not process or not state:
            return

        position = 0

        try:
            # 研究循环进程完成主流程后会进入IPC等待模式（不会立即退出），
            # 所以这里以"simulation_end"事件（而非进程退出）作为完成信号
            while process.poll() is None:
                if os.path.exists(actions_log):
                    position, finished = cls._read_action_log(actions_log, position, state)
                    if finished:
                        state.runner_status = RunnerStatus.COMPLETED
                        state.is_running = False
                        state.is_completed = True
                        state.completed_at = datetime.now().isoformat()
                        cls._save_run_state(state)
                        logger.info(f"研究循环主流程完成（进入待命模式）: {simulation_id}")
                        return  # 进程仍在运行（等待consult/close_env命令），监控线程可以退出了

                cls._save_run_state(state)
                time.sleep(2)

            # 进程已退出（非正常路径，比如异常崩溃）
            if os.path.exists(actions_log):
                cls._read_action_log(actions_log, position, state)

            exit_code = process.returncode
            if exit_code == 0:
                state.runner_status = RunnerStatus.COMPLETED
                state.is_completed = True
                state.completed_at = datetime.now().isoformat()
            else:
                state.runner_status = RunnerStatus.FAILED
                main_log_path = os.path.join(sim_dir, "simulation.log")
                error_info = ""
                try:
                    if os.path.exists(main_log_path):
                        with open(main_log_path, 'r', encoding='utf-8') as f:
                            error_info = f.read()[-2000:]
                except Exception:
                    pass
                state.error = f"进程退出码: {exit_code}, 错误: {error_info}"
                logger.error(f"研究循环失败: {simulation_id}, error={state.error}")

            state.is_running = False
            cls._save_run_state(state)

        except Exception as e:
            logger.error(f"监控线程异常: {simulation_id}, error={str(e)}")
            state.runner_status = RunnerStatus.FAILED
            state.error = str(e)
            cls._save_run_state(state)

        finally:
            if cls._graph_memory_enabled.get(simulation_id, False):
                try:
                    ZepGraphMemoryManager.stop_updater(simulation_id)
                except Exception as e:
                    logger.error(f"停止图谱记忆更新器失败: {e}")
                cls._graph_memory_enabled.pop(simulation_id, None)

            cls._action_queues.pop(simulation_id, None)

    @classmethod
    def _read_action_log(cls, log_path: str, position: int, state: SimulationRunState) -> "tuple[int, bool]":
        """
        读取动作日志文件

        Returns:
            (新的读取位置, 是否检测到 simulation_end 事件)
        """
        graph_memory_enabled = cls._graph_memory_enabled.get(state.simulation_id, False)
        graph_updater = ZepGraphMemoryManager.get_updater(state.simulation_id) if graph_memory_enabled else None
        finished = False

        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                f.seek(position)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)

                        if "event_type" in data:
                            event_type = data.get("event_type")
                            if event_type == "round_end":
                                round_num = data.get("round", 0)
                                if round_num > state.current_round:
                                    state.current_round = round_num
                            elif event_type == "simulation_end":
                                finished = True
                                logger.info(f"研究循环完成: {state.simulation_id}, "
                                            f"total_rounds={data.get('total_rounds')}")
                            continue

                        action = AgentAction(
                            round_num=data.get("round", 0),
                            timestamp=data.get("timestamp", datetime.now().isoformat()),
                            agent_id=data.get("agent_id", 0),
                            agent_name=data.get("agent_name", ""),
                            action_type=data.get("action_type", ""),
                            action_args=data.get("action_args", {}),
                            result=data.get("result"),
                            success=data.get("success", True),
                        )
                        state.add_action(action)

                        if action.round_num and action.round_num > state.current_round:
                            state.current_round = action.round_num

                        if graph_updater:
                            graph_updater.add_activity_from_dict(data, "research")

                    except json.JSONDecodeError:
                        pass
                return f.tell(), finished
        except Exception as e:
            logger.warning(f"读取动作日志失败: {log_path}, error={e}")
            return position, finished

    @classmethod
    def _terminate_process(cls, process: subprocess.Popen, simulation_id: str, timeout: int = 10):
        """跨平台终止进程及其子进程"""
        if IS_WINDOWS:
            logger.info(f"终止进程树 (Windows): simulation={simulation_id}, pid={process.pid}")
            try:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T'], capture_output=True, timeout=5)
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    logger.warning(f"进程未响应，强制终止: {simulation_id}")
                    subprocess.run(['taskkill', '/F', '/PID', str(process.pid), '/T'], capture_output=True, timeout=5)
                    process.wait(timeout=5)
            except Exception as e:
                logger.warning(f"taskkill 失败，尝试 terminate: {e}")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        else:
            pgid = os.getpgid(process.pid)
            logger.info(f"终止进程组 (Unix): simulation={simulation_id}, pgid={pgid}")
            os.killpg(pgid, signal.SIGTERM)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning(f"进程组未响应 SIGTERM，强制终止: {simulation_id}")
                os.killpg(pgid, signal.SIGKILL)
                process.wait(timeout=5)

    @classmethod
    def stop_simulation(cls, simulation_id: str) -> SimulationRunState:
        """停止研究循环"""
        state = cls.get_run_state(simulation_id)
        if not state:
            raise ValueError(f"研究循环不存在: {simulation_id}")

        if state.runner_status not in [RunnerStatus.RUNNING, RunnerStatus.PAUSED]:
            raise ValueError(f"研究循环未在运行: {simulation_id}, status={state.runner_status}")

        state.runner_status = RunnerStatus.STOPPING
        cls._save_run_state(state)

        process = cls._processes.get(simulation_id)
        if process and process.poll() is None:
            try:
                cls._terminate_process(process, simulation_id)
            except ProcessLookupError:
                pass
            except Exception as e:
                logger.error(f"终止进程组失败: {simulation_id}, error={e}")
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    process.kill()

        state.runner_status = RunnerStatus.STOPPED
        state.is_running = False
        state.completed_at = datetime.now().isoformat()
        cls._save_run_state(state)

        if cls._graph_memory_enabled.get(simulation_id, False):
            try:
                ZepGraphMemoryManager.stop_updater(simulation_id)
            except Exception as e:
                logger.error(f"停止图谱记忆更新器失败: {e}")
            cls._graph_memory_enabled.pop(simulation_id, None)

        logger.info(f"研究循环已停止: {simulation_id}")
        return state

    @classmethod
    def _read_actions_from_file(
        cls,
        file_path: str,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """从动作文件中读取动作"""
        if not os.path.exists(file_path):
            return []

        actions = []
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if "event_type" in data:
                        continue
                    if agent_id is not None and data.get("agent_id") != agent_id:
                        continue
                    if round_num is not None and data.get("round") != round_num:
                        continue

                    actions.append(AgentAction(
                        round_num=data.get("round", 0),
                        timestamp=data.get("timestamp", ""),
                        agent_id=data.get("agent_id", 0),
                        agent_name=data.get("agent_name", ""),
                        action_type=data.get("action_type", ""),
                        action_args=data.get("action_args", {}),
                        result=data.get("result"),
                        success=data.get("success", True),
                    ))
                except json.JSONDecodeError:
                    continue
        return actions

    @classmethod
    def get_all_actions(
        cls,
        simulation_id: str,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """获取完整动作历史（无分页限制，按时间倒序）"""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        actions_log = os.path.join(sim_dir, "research", "actions.jsonl")
        actions = cls._read_actions_from_file(actions_log, agent_id=agent_id, round_num=round_num)
        actions.sort(key=lambda x: x.timestamp, reverse=True)
        return actions

    @classmethod
    def get_actions(
        cls,
        simulation_id: str,
        limit: int = 100,
        offset: int = 0,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """获取动作历史（带分页）"""
        actions = cls.get_all_actions(simulation_id, agent_id=agent_id, round_num=round_num)
        return actions[offset:offset + limit]

    @classmethod
    def get_timeline(
        cls,
        simulation_id: str,
        start_round: int = 0,
        end_round: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """获取研究循环时间线（按轮次汇总）"""
        actions = cls.get_actions(simulation_id, limit=10000)

        rounds: Dict[int, Dict[str, Any]] = {}
        for action in actions:
            round_num = action.round_num
            if round_num < start_round:
                continue
            if end_round is not None and round_num > end_round:
                continue

            if round_num not in rounds:
                rounds[round_num] = {
                    "round_num": round_num,
                    "total_actions": 0,
                    "active_agents": set(),
                    "action_types": {},
                    "first_action_time": action.timestamp,
                    "last_action_time": action.timestamp,
                }

            r = rounds[round_num]
            r["total_actions"] += 1
            r["active_agents"].add(action.agent_name)
            r["action_types"][action.action_type] = r["action_types"].get(action.action_type, 0) + 1
            r["last_action_time"] = action.timestamp

        result = []
        for round_num in sorted(rounds.keys()):
            r = rounds[round_num]
            result.append({
                "round_num": round_num,
                "total_actions": r["total_actions"],
                "active_agents_count": len(r["active_agents"]),
                "active_agents": list(r["active_agents"]),
                "action_types": r["action_types"],
                "first_action_time": r["first_action_time"],
                "last_action_time": r["last_action_time"],
            })
        return result

    @classmethod
    def get_agent_stats(cls, simulation_id: str) -> List[Dict[str, Any]]:
        """获取每个固定角色的统计信息"""
        actions = cls.get_actions(simulation_id, limit=10000)

        agent_stats: Dict[str, Dict[str, Any]] = {}
        for action in actions:
            name = action.agent_name
            if name not in agent_stats:
                agent_stats[name] = {
                    "agent_name": name,
                    "total_actions": 0,
                    "action_types": {},
                    "first_action_time": action.timestamp,
                    "last_action_time": action.timestamp,
                }
            stats = agent_stats[name]
            stats["total_actions"] += 1
            stats["action_types"][action.action_type] = stats["action_types"].get(action.action_type, 0) + 1
            stats["last_action_time"] = action.timestamp

        return sorted(agent_stats.values(), key=lambda x: x["total_actions"], reverse=True)

    @classmethod
    def cleanup_simulation_logs(cls, simulation_id: str) -> Dict[str, Any]:
        """清理研究循环的运行日志（用于强制重新开始）"""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            return {"success": True, "message": "研究循环目录不存在，无需清理"}

        cleaned_files = []
        errors = []

        files_to_delete = ["run_state.json", "simulation.log", "env_status.json"]
        for filename in files_to_delete:
            file_path = os.path.join(sim_dir, filename)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    cleaned_files.append(filename)
                except Exception as e:
                    errors.append(f"删除 {filename} 失败: {str(e)}")

        research_dir = os.path.join(sim_dir, "research")
        if os.path.exists(research_dir):
            import shutil
            try:
                shutil.rmtree(research_dir)
                cleaned_files.append("research/")
            except Exception as e:
                errors.append(f"删除 research/ 失败: {str(e)}")

        if simulation_id in cls._run_states:
            del cls._run_states[simulation_id]

        logger.info(f"清理研究循环日志完成: {simulation_id}, 删除文件: {cleaned_files}")
        return {"success": len(errors) == 0, "cleaned_files": cleaned_files, "errors": errors if errors else None}

    _cleanup_done = False

    @classmethod
    def cleanup_all_simulations(cls):
        """清理所有运行中的研究循环进程（服务器关闭时调用）"""
        if cls._cleanup_done:
            return
        cls._cleanup_done = True

        has_processes = bool(cls._processes)
        has_updaters = bool(cls._graph_memory_enabled)
        if not has_processes and not has_updaters:
            return

        logger.info("正在清理所有研究循环进程...")

        try:
            ZepGraphMemoryManager.stop_all()
        except Exception as e:
            logger.error(f"停止图谱记忆更新器失败: {e}")
        cls._graph_memory_enabled.clear()

        for simulation_id, process in list(cls._processes.items()):
            try:
                if process.poll() is None:
                    logger.info(f"终止研究循环进程: {simulation_id}, pid={process.pid}")
                    try:
                        cls._terminate_process(process, simulation_id, timeout=5)
                    except (ProcessLookupError, OSError):
                        try:
                            process.terminate()
                            process.wait(timeout=3)
                        except Exception:
                            process.kill()

                    state = cls.get_run_state(simulation_id)
                    if state:
                        state.runner_status = RunnerStatus.STOPPED
                        state.is_running = False
                        state.completed_at = datetime.now().isoformat()
                        state.error = "服务器关闭，研究循环被终止"
                        cls._save_run_state(state)

                    try:
                        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
                        state_file = os.path.join(sim_dir, "state.json")
                        if os.path.exists(state_file):
                            with open(state_file, 'r', encoding='utf-8') as f:
                                state_data = json.load(f)
                            state_data['status'] = 'stopped'
                            state_data['updated_at'] = datetime.now().isoformat()
                            with open(state_file, 'w', encoding='utf-8') as f:
                                json.dump(state_data, f, indent=2, ensure_ascii=False)
                    except Exception as state_err:
                        logger.warning(f"更新 state.json 失败: {simulation_id}, error={state_err}")

            except Exception as e:
                logger.error(f"清理进程失败: {simulation_id}, error={e}")

        for simulation_id, file_handle in list(cls._stdout_files.items()):
            try:
                if file_handle:
                    file_handle.close()
            except Exception:
                pass
        cls._stdout_files.clear()

        for simulation_id, file_handle in list(cls._stderr_files.items()):
            try:
                if file_handle:
                    file_handle.close()
            except Exception:
                pass
        cls._stderr_files.clear()

        cls._processes.clear()
        cls._action_queues.clear()

        logger.info("研究循环进程清理完成")

    @classmethod
    def register_cleanup(cls):
        """注册清理函数（Flask 应用启动时调用）"""
        global _cleanup_registered
        if _cleanup_registered:
            return

        is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
        is_debug_mode = os.environ.get('FLASK_DEBUG') == '1' or os.environ.get('WERKZEUG_RUN_MAIN') is not None

        if is_debug_mode and not is_reloader_process:
            _cleanup_registered = True
            return

        original_sigint = signal.getsignal(signal.SIGINT)
        original_sigterm = signal.getsignal(signal.SIGTERM)
        original_sighup = None
        has_sighup = hasattr(signal, 'SIGHUP')
        if has_sighup:
            original_sighup = signal.getsignal(signal.SIGHUP)

        def cleanup_handler(signum=None, frame=None):
            if cls._processes or cls._graph_memory_enabled:
                logger.info(f"收到信号 {signum}，开始清理...")
            cls.cleanup_all_simulations()

            if signum == signal.SIGINT and callable(original_sigint):
                original_sigint(signum, frame)
            elif signum == signal.SIGTERM and callable(original_sigterm):
                original_sigterm(signum, frame)
            elif has_sighup and signum == signal.SIGHUP:
                if callable(original_sighup):
                    original_sighup(signum, frame)
                else:
                    sys.exit(0)
            else:
                raise KeyboardInterrupt

        atexit.register(cls.cleanup_all_simulations)

        try:
            signal.signal(signal.SIGTERM, cleanup_handler)
            signal.signal(signal.SIGINT, cleanup_handler)
            if has_sighup:
                signal.signal(signal.SIGHUP, cleanup_handler)
        except ValueError:
            logger.warning("无法注册信号处理器（不在主线程），仅使用 atexit")

        _cleanup_registered = True

    @classmethod
    def get_running_simulations(cls) -> List[str]:
        """获取所有正在运行的研究循环ID列表"""
        return [sim_id for sim_id, process in cls._processes.items() if process.poll() is None]

    # ============== Consult 功能（咨询固定角色） ==============

    @classmethod
    def check_env_alive(cls, simulation_id: str) -> bool:
        """检查研究循环环境是否存活（可以接收consult命令）"""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            return False
        ipc_client = SimulationIPCClient(sim_dir)
        return ipc_client.check_env_alive()

    @classmethod
    def get_env_status_detail(cls, simulation_id: str) -> Dict[str, Any]:
        """获取研究循环环境的详细状态信息"""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        status_file = os.path.join(sim_dir, "env_status.json")

        default_status = {"status": "stopped", "timestamp": None}
        if not os.path.exists(status_file):
            return default_status

        try:
            with open(status_file, 'r', encoding='utf-8') as f:
                status = json.load(f)
            return {"status": status.get("status", "stopped"), "timestamp": status.get("timestamp")}
        except (json.JSONDecodeError, OSError):
            return default_status

    @classmethod
    def consult_agent(
        cls,
        simulation_id: str,
        role: str,
        prompt: str,
        timeout: float = 60.0
    ) -> Dict[str, Any]:
        """咨询单个固定角色（Generation/Reflection/Ranking/Tournament/Evolution/Proximity/MetaReview之一）"""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"研究循环不存在: {simulation_id}")

        ipc_client = SimulationIPCClient(sim_dir)
        if not ipc_client.check_env_alive():
            raise ValueError(f"研究循环环境未运行或已关闭，无法咨询: {simulation_id}")

        logger.info(f"发送consult命令: simulation_id={simulation_id}, role={role}")
        response = ipc_client.send_consult(role=role, prompt=prompt, timeout=timeout)

        if response.status.value == "completed":
            return {"success": True, "role": role, "prompt": prompt, "result": response.result, "timestamp": response.timestamp}
        else:
            return {"success": False, "role": role, "prompt": prompt, "error": response.error, "timestamp": response.timestamp}

    @classmethod
    def consult_agents_batch(
        cls,
        simulation_id: str,
        consultations: List[Dict[str, Any]],
        timeout: float = 120.0
    ) -> Dict[str, Any]:
        """批量咨询多个固定角色"""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"研究循环不存在: {simulation_id}")

        ipc_client = SimulationIPCClient(sim_dir)
        if not ipc_client.check_env_alive():
            raise ValueError(f"研究循环环境未运行或已关闭，无法咨询: {simulation_id}")

        logger.info(f"发送批量consult命令: simulation_id={simulation_id}, count={len(consultations)}")
        response = ipc_client.send_batch_consult(consultations=consultations, timeout=timeout)

        if response.status.value == "completed":
            return {"success": True, "consultations_count": len(consultations), "result": response.result, "timestamp": response.timestamp}
        else:
            return {"success": False, "consultations_count": len(consultations), "error": response.error, "timestamp": response.timestamp}

    @classmethod
    def consult_all_roles(
        cls,
        simulation_id: str,
        prompt: str,
        timeout: float = 180.0
    ) -> Dict[str, Any]:
        """用相同问题咨询全部7个固定角色"""
        role_names = ["Generation", "Reflection", "Ranking", "Tournament", "Evolution", "Proximity", "MetaReview"]
        consultations = [{"role": r, "prompt": prompt} for r in role_names]
        return cls.consult_agents_batch(simulation_id=simulation_id, consultations=consultations, timeout=timeout)

    @classmethod
    def close_simulation_env(cls, simulation_id: str, timeout: float = 30.0) -> Dict[str, Any]:
        """关闭研究循环环境（优雅退出等待命令模式，而不是强制终止进程）"""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"研究循环不存在: {simulation_id}")

        ipc_client = SimulationIPCClient(sim_dir)
        if not ipc_client.check_env_alive():
            return {"success": True, "message": "环境已经关闭"}

        logger.info(f"发送关闭环境命令: simulation_id={simulation_id}")
        try:
            response = ipc_client.send_close_env(timeout=timeout)
            return {
                "success": response.status.value == "completed",
                "message": "环境关闭命令已发送",
                "result": response.result,
                "timestamp": response.timestamp
            }
        except TimeoutError:
            return {"success": True, "message": "环境关闭命令已发送（等待响应超时，环境可能正在关闭）"}
