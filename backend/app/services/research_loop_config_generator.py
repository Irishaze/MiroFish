"""
研究循环运行参数

与旧版 SimulationConfigGenerator 不同，这里不再由LLM推断配置——
用户直接在UI上设置 num_queries / num_candidates / num_assays / num_cycles，
仅做边界校验与默认值填充（参考 Robin RobinConfiguration 的默认值：
num_queries=3, num_candidates=5, num_assays=3）。
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class ResearchRunParameters:
    """研究循环的运行参数"""
    simulation_id: str
    project_id: str
    graph_id: str
    research_question: str

    num_queries: int = 3       # Robin 默认值：每步生成的检索查询数
    num_candidates: int = 5    # Robin 默认值：假设候选数量
    num_assays: int = 3        # Robin 默认值：评估视角候选数量
    max_papers: int = 15       # MiroFish新增：Phase A 自动检索文献数量上限（硬上限20，由图谱构建阶段消费）
    num_cycles: int = 1        # MiroFish新增：Generation->...->Evolution 循环轮数

    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResearchRunParameters":
        return cls(
            simulation_id=data["simulation_id"],
            project_id=data["project_id"],
            graph_id=data["graph_id"],
            research_question=data.get("research_question", ""),
            num_queries=data.get("num_queries", 3),
            num_candidates=data.get("num_candidates", 5),
            num_assays=data.get("num_assays", 3),
            max_papers=data.get("max_papers", 15),
            num_cycles=data.get("num_cycles", 1),
            generated_at=data.get("generated_at", datetime.now().isoformat()),
        )


# 参数边界（用于校验/裁剪用户输入）
PARAM_BOUNDS = {
    "num_queries": (1, 10),
    "num_candidates": (3, 10),
    "num_assays": (1, 5),
    "max_papers": (1, 20),
    "num_cycles": (1, 5),
}


def _clamp(value: int, bounds: tuple) -> int:
    lo, hi = bounds
    return max(lo, min(int(value), hi))


class ResearchLoopConfigGenerator:
    """校验并生成 ResearchRunParameters（不涉及LLM调用）"""

    def generate_config(
        self,
        simulation_id: str,
        project_id: str,
        graph_id: str,
        research_question: str,
        num_queries: Optional[int] = None,
        num_candidates: Optional[int] = None,
        num_assays: Optional[int] = None,
        max_papers: Optional[int] = None,
        num_cycles: Optional[int] = None,
    ) -> ResearchRunParameters:
        params = ResearchRunParameters(
            simulation_id=simulation_id,
            project_id=project_id,
            graph_id=graph_id,
            research_question=research_question,
        )
        if num_queries is not None:
            params.num_queries = _clamp(num_queries, PARAM_BOUNDS["num_queries"])
        if num_candidates is not None:
            params.num_candidates = _clamp(num_candidates, PARAM_BOUNDS["num_candidates"])
        if num_assays is not None:
            params.num_assays = _clamp(num_assays, PARAM_BOUNDS["num_assays"])
        if max_papers is not None:
            params.max_papers = _clamp(max_papers, PARAM_BOUNDS["max_papers"])
        if num_cycles is not None:
            params.num_cycles = _clamp(num_cycles, PARAM_BOUNDS["num_cycles"])
        return params
