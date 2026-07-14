"""
研究循环管理器
管理科学假设检验的两阶段Elo锦标赛研究循环（Assay阶段 -> Hypothesis阶段）
运行参数由用户在UI上直接设置，不再由LLM生成人设/配置
"""

import os
import json
import shutil
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.logger import get_logger
from .zep_entity_reader import ZepEntityReader, FilteredEntities
from .research_loop_config_generator import ResearchLoopConfigGenerator, ResearchRunParameters
from ..utils.locale import t

logger = get_logger('mirofish.simulation')


class SimulationStatus(str, Enum):
    """模拟状态"""
    CREATED = "created"
    PREPARING = "preparing"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"      # 模拟被手动停止
    COMPLETED = "completed"  # 模拟自然完成
    FAILED = "failed"


@dataclass
class SimulationState:
    """研究循环状态"""
    simulation_id: str
    project_id: str
    graph_id: str

    # 状态
    status: SimulationStatus = SimulationStatus.CREATED

    # 准备阶段数据（图谱实体，用于展示/诊断）
    entities_count: int = 0
    entity_types: List[str] = field(default_factory=list)

    # 运行参数（用户直接设置，非LLM生成）
    config_generated: bool = False
    research_question: str = ""
    num_queries: int = 3
    num_candidates: int = 5
    num_assays: int = 3
    num_cycles: int = 1

    # 运行时数据
    current_round: int = 0
    current_phase: str = "not_started"  # not_started / assay_selection / hypothesis_generation / completed

    # 时间戳
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    # 错误信息
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """完整状态字典（内部使用）"""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "entity_types": self.entity_types,
            "config_generated": self.config_generated,
            "research_question": self.research_question,
            "num_queries": self.num_queries,
            "num_candidates": self.num_candidates,
            "num_assays": self.num_assays,
            "num_cycles": self.num_cycles,
            "current_round": self.current_round,
            "current_phase": self.current_phase,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }

    def to_simple_dict(self) -> Dict[str, Any]:
        """简化状态字典（API返回使用）"""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "entity_types": self.entity_types,
            "config_generated": self.config_generated,
            "error": self.error,
        }


class SimulationManager:
    """
    研究循环管理器

    核心功能：
    1. 从Zep图谱读取实体并过滤（用于展示图谱概况）
    2. 校验并持久化用户设置的运行参数（num_queries/num_candidates/num_assays/num_cycles）
    """
    
    # 模拟数据存储目录
    SIMULATION_DATA_DIR = os.path.join(
        os.path.dirname(__file__), 
        '../../uploads/simulations'
    )
    
    def __init__(self):
        # 确保目录存在
        os.makedirs(self.SIMULATION_DATA_DIR, exist_ok=True)
        
        # 内存中的模拟状态缓存
        self._simulations: Dict[str, SimulationState] = {}
    
    def _get_simulation_dir(self, simulation_id: str) -> str:
        """获取模拟数据目录"""
        sim_dir = os.path.join(self.SIMULATION_DATA_DIR, simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        return sim_dir
    
    def _save_simulation_state(self, state: SimulationState):
        """保存模拟状态到文件"""
        sim_dir = self._get_simulation_dir(state.simulation_id)
        state_file = os.path.join(sim_dir, "state.json")
        
        state.updated_at = datetime.now().isoformat()
        
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        
        self._simulations[state.simulation_id] = state
    
    def _load_simulation_state(self, simulation_id: str) -> Optional[SimulationState]:
        """从文件加载模拟状态"""
        if simulation_id in self._simulations:
            return self._simulations[simulation_id]
        
        sim_dir = self._get_simulation_dir(simulation_id)
        state_file = os.path.join(sim_dir, "state.json")
        
        if not os.path.exists(state_file):
            return None
        
        with open(state_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        state = SimulationState(
            simulation_id=simulation_id,
            project_id=data.get("project_id", ""),
            graph_id=data.get("graph_id", ""),
            status=SimulationStatus(data.get("status", "created")),
            entities_count=data.get("entities_count", 0),
            entity_types=data.get("entity_types", []),
            config_generated=data.get("config_generated", False),
            research_question=data.get("research_question", ""),
            num_queries=data.get("num_queries", 3),
            num_candidates=data.get("num_candidates", 5),
            num_assays=data.get("num_assays", 3),
            num_cycles=data.get("num_cycles", 1),
            current_round=data.get("current_round", 0),
            current_phase=data.get("current_phase", "not_started"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            error=data.get("error"),
        )
        
        self._simulations[simulation_id] = state
        return state
    
    def create_simulation(
        self,
        project_id: str,
        graph_id: str,
    ) -> SimulationState:
        """
        创建新的研究循环

        Args:
            project_id: 项目ID
            graph_id: Zep图谱ID

        Returns:
            SimulationState
        """
        import uuid
        simulation_id = f"sim_{uuid.uuid4().hex[:12]}"

        state = SimulationState(
            simulation_id=simulation_id,
            project_id=project_id,
            graph_id=graph_id,
            status=SimulationStatus.CREATED,
        )

        self._save_simulation_state(state)
        logger.info(f"创建研究循环: {simulation_id}, project={project_id}, graph={graph_id}")

        return state
    
    def prepare_simulation(
        self,
        simulation_id: str,
        research_question: str,
        defined_entity_types: Optional[List[str]] = None,
        num_queries: Optional[int] = None,
        num_candidates: Optional[int] = None,
        num_assays: Optional[int] = None,
        max_papers: Optional[int] = None,
        num_cycles: Optional[int] = None,
        progress_callback: Optional[callable] = None,
    ) -> SimulationState:
        """
        准备研究循环环境

        步骤：
        1. 从Zep图谱读取并过滤实体（用于展示图谱概况，诊断用途）
        2. 校验并持久化用户设置的运行参数（num_queries/num_candidates/num_assays/num_cycles）
           —— 不再由LLM生成人设或配置，7个固定角色定义在 coscientist_agents.py 中

        Args:
            simulation_id: 研究循环ID
            research_question: 研究问题
            defined_entity_types: 预定义的实体类型（可选）
            num_queries/num_candidates/num_assays/max_papers/num_cycles: 用户设置的运行参数
            progress_callback: 进度回调函数 (stage, progress, message)

        Returns:
            SimulationState
        """
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"研究循环不存在: {simulation_id}")

        try:
            state.status = SimulationStatus.PREPARING
            self._save_simulation_state(state)

            sim_dir = self._get_simulation_dir(simulation_id)

            # ========== 阶段1: 读取图谱实体（诊断展示用） ==========
            if progress_callback:
                progress_callback("reading", 0, t('progress.connectingZepGraph'))

            reader = ZepEntityReader()

            if progress_callback:
                progress_callback("reading", 30, t('progress.readingNodeData'))

            filtered = reader.filter_defined_entities(
                graph_id=state.graph_id,
                defined_entity_types=defined_entity_types,
                enrich_with_edges=True
            )

            state.entities_count = filtered.filtered_count
            state.entity_types = list(filtered.entity_types)

            if progress_callback:
                progress_callback(
                    "reading", 100,
                    t('progress.readingComplete', count=filtered.filtered_count),
                    current=filtered.filtered_count,
                    total=filtered.filtered_count
                )

            if filtered.filtered_count == 0:
                state.status = SimulationStatus.FAILED
                state.error = "没有找到符合条件的实体，请检查图谱是否正确构建"
                self._save_simulation_state(state)
                return state

            # ========== 阶段2: 校验并持久化运行参数 ==========
            if progress_callback:
                progress_callback("generating_config", 30, t('progress.callingLLMConfig'), current=1, total=2)

            config_generator = ResearchLoopConfigGenerator()
            run_params = config_generator.generate_config(
                simulation_id=simulation_id,
                project_id=state.project_id,
                graph_id=state.graph_id,
                research_question=research_question,
                num_queries=num_queries,
                num_candidates=num_candidates,
                num_assays=num_assays,
                max_papers=max_papers,
                num_cycles=num_cycles,
            )

            config_path = os.path.join(sim_dir, "simulation_config.json")
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(run_params.to_json())

            state.config_generated = True
            state.research_question = run_params.research_question
            state.num_queries = run_params.num_queries
            state.num_candidates = run_params.num_candidates
            state.num_assays = run_params.num_assays
            state.num_cycles = run_params.num_cycles

            if progress_callback:
                progress_callback("generating_config", 100, t('progress.configComplete'), current=2, total=2)

            # 更新状态
            state.status = SimulationStatus.READY
            self._save_simulation_state(state)

            logger.info(f"研究循环准备完成: {simulation_id}, "
                       f"entities={state.entities_count}, num_candidates={state.num_candidates}, "
                       f"num_assays={state.num_assays}, num_cycles={state.num_cycles}")

            return state

        except Exception as e:
            logger.error(f"研究循环准备失败: {simulation_id}, error={str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            state.status = SimulationStatus.FAILED
            state.error = str(e)
            self._save_simulation_state(state)
            raise
    
    def get_simulation(self, simulation_id: str) -> Optional[SimulationState]:
        """获取模拟状态"""
        return self._load_simulation_state(simulation_id)
    
    def list_simulations(self, project_id: Optional[str] = None) -> List[SimulationState]:
        """列出所有模拟"""
        simulations = []
        
        if os.path.exists(self.SIMULATION_DATA_DIR):
            for sim_id in os.listdir(self.SIMULATION_DATA_DIR):
                # 跳过隐藏文件（如 .DS_Store）和非目录文件
                sim_path = os.path.join(self.SIMULATION_DATA_DIR, sim_id)
                if sim_id.startswith('.') or not os.path.isdir(sim_path):
                    continue
                
                state = self._load_simulation_state(sim_id)
                if state:
                    if project_id is None or state.project_id == project_id:
                        simulations.append(state)
        
        return simulations
    
    def get_simulation_config(self, simulation_id: str) -> Optional[Dict[str, Any]]:
        """获取模拟配置"""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            return None
        
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_run_instructions(self, simulation_id: str) -> Dict[str, str]:
        """获取运行说明"""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts'))

        return {
            "simulation_dir": sim_dir,
            "scripts_dir": scripts_dir,
            "config_file": config_path,
            "commands": {
                "research": f"python {scripts_dir}/run_research_loop.py --config {config_path}",
            },
            "instructions": (
                f"1. 激活虚拟环境\n"
                f"2. 运行研究循环 (脚本位于 {scripts_dir}):\n"
                f"   python {scripts_dir}/run_research_loop.py --config {config_path}"
            )
        }
