#!/usr/bin/env python3
"""
两阶段Elo锦标赛研究循环

阶段1（Assay/评估视角选择）：镜像 Robin assays.py::experimental_assay()
    生成查询 -> 文献检索 -> 提出num_assays个候选评估视角 -> 详细评审 -> Elo锦标赛排名 -> 选出最佳视角
    -> 综合出下一阶段的生成目标（镜像 Robin 的 synthesize_candidate_goal）

阶段2（Hypothesis/假设生成）：镜像 Robin candidates.py::therapeutic_candidates()
    生成查询 -> 文献检索 -> 提出num_candidates个候选假设 -> 详细评审 -> Elo锦标赛排名
    -> Evolution Agent精炼 -> Meta-Review Agent综合总结 -> 若num_cycles>1则重复本阶段

单进程运行（不使用multiprocessing——只有7个固定角色，不是OASIS式的成百上千人设）。
运行完成后进入IPC等待模式，接受 consult_agent / close_env 命令（同 Robin 的
"环境保持运行以便交互"的设计）。

用法：
    python run_research_loop.py --config <simulation_config.json路径>
"""

import argparse
import csv
import json
import os
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

# 让脚本可以独立运行时找到 app 包
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.services.coscientist_agents import (  # noqa: E402
    Candidate,
    EvolutionAgent,
    GenerationAgent,
    MatchResult,
    MetaReviewAgent,
    ProximityAgent,
    RankingAgent,
    ReflectionAgent,
    TournamentAgent,
)
from app.services.literature_search import LiteratureSearchService  # noqa: E402
from app.services.simulation_ipc import (  # noqa: E402
    CommandStatus,
    CommandType,
    SimulationIPCServer,
)
from app.utils.llm_client import LLMClient  # noqa: E402
from app.utils.locale import get_language_instruction, set_locale  # noqa: E402

ROLE_NAMES = [
    "Generation", "Reflection", "Ranking", "Tournament",
    "Evolution", "Proximity", "MetaReview",
]

# 无命令时最长等待时间（秒），超过则自动退出，避免进程无限挂起
IDLE_TIMEOUT_SECONDS = 30 * 60


class ActionLogger:
    """写入统一的 research/actions.jsonl 动作日志（供 simulation_runner.py 监控解析）"""

    def __init__(self, sim_dir: str):
        self.research_dir = os.path.join(sim_dir, "research")
        os.makedirs(self.research_dir, exist_ok=True)
        self.log_path = os.path.join(self.research_dir, "actions.jsonl")
        self._lock = threading.Lock()

    def _write(self, data: Dict[str, Any]):
        with self._lock:
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def log_action(
        self,
        round_num: int,
        agent_name: str,
        action_type: str,
        action_args: Dict[str, Any],
        result: Optional[str] = None,
    ):
        self._write({
            "round": round_num,
            "timestamp": datetime.now().isoformat(),
            "agent_id": 0,
            "agent_name": agent_name,
            "action_type": action_type,
            "action_args": action_args,
            "result": result,
            "success": True,
        })

    def log_event(self, event_type: str, **kwargs):
        self._write({
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(),
            **kwargs,
        })


def _write_literature_reviews(out_dir: str, candidates: List[Candidate]):
    os.makedirs(out_dir, exist_ok=True)
    for c in candidates:
        if not c.detailed_review:
            continue
        path = os.path.join(out_dir, f"{c.candidate_id}.txt")
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f"Candidate: {c.name}\n\n")
            for lit in c.detailed_review.literature_reviewed:
                f.write(f"- {lit.title} ({lit.year}, {lit.source})\n")
                if lit.abstract:
                    f.write(f"  {lit.abstract}\n")
            f.write("\n")


def _write_summary_txt(path: str, candidates: List[Candidate], kind: str):
    with open(path, 'w', encoding='utf-8') as f:
        for i, c in enumerate(candidates):
            f.write(f"{kind} {i + 1}:\n")
            f.write(f"Name: {c.name}\n")
            f.write(f"Statement: {c.statement}\n")
            f.write(f"Reasoning: {c.reasoning}\n\n")


def _write_detailed_reports(out_dir: str, candidates: List[Candidate]):
    os.makedirs(out_dir, exist_ok=True)
    for c in candidates:
        review = c.detailed_review
        path = os.path.join(out_dir, f"{c.candidate_id}.txt")
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f"Name: {c.name}\n")
            f.write(f"Statement: {c.statement}\n\n")
            if review:
                f.write(f"Critique: {review.critique}\n")
                f.write(f"Strengths: {review.strengths}\n")
                f.write(f"Weaknesses: {review.weaknesses}\n")
                f.write(f"Confidence: {review.confidence}\n")


def _write_ranking_results(path: str, candidates: List[Candidate], matches: List[MatchResult]):
    id_to_name = {c.candidate_id: c.name for c in candidates}
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["candidate_a", "candidate_b", "winner", "rationale"])
        for m in matches:
            writer.writerow([
                id_to_name.get(m.candidate_a_id, m.candidate_a_id),
                id_to_name.get(m.candidate_b_id, m.candidate_b_id),
                id_to_name.get(m.winner_id, m.winner_id),
                m.rationale,
            ])


def _write_ranked_csv(path: str, candidates: List[Candidate]):
    ranked = sorted(candidates, key=lambda c: c.elo_rating, reverse=True)
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["rank", "name", "statement", "elo_rating", "confidence"])
        for i, c in enumerate(ranked):
            confidence = c.detailed_review.confidence if c.detailed_review else ""
            writer.writerow([i + 1, c.name, c.statement, round(c.elo_rating, 1), confidence])


def run_tournament(
    candidates: List[Candidate],
    research_question: str,
    tournament_agent: TournamentAgent,
    ranking_agent: RankingAgent,
    action_logger: ActionLogger,
    round_num: int,
    action_type: str,
) -> List[MatchResult]:
    """运行一轮锦标赛：调度对战 + LLM裁判 + Elo更新"""
    id_to_candidate = {c.candidate_id: c for c in candidates}
    pairs = tournament_agent.schedule_matches(candidates)
    matches = []
    for a_id, b_id in pairs:
        candidate_a = id_to_candidate[a_id]
        candidate_b = id_to_candidate[b_id]
        match = ranking_agent.judge_match(candidate_a, candidate_b, research_question)
        matches.append(match)
        action_logger.log_action(
            round_num=round_num,
            agent_name="Ranking",
            action_type=action_type,
            action_args={
                "candidate_a": candidate_a.name,
                "candidate_b": candidate_b.name,
                "winner": id_to_candidate[match.winner_id].name,
                "elo_a": round(candidate_a.elo_rating, 1),
                "elo_b": round(candidate_b.elo_rating, 1),
            },
            result=match.rationale,
        )
    return matches


def run_generation_review_tournament_phase(
    research_question: str,
    topic: str,
    num_queries: int,
    num_candidates: int,
    candidate_kind: str,
    literature_service: LiteratureSearchService,
    generation_agent: GenerationAgent,
    reflection_agent: ReflectionAgent,
    proximity_agent: ProximityAgent,
    tournament_agent: TournamentAgent,
    ranking_agent: RankingAgent,
    action_logger: ActionLogger,
    round_num: int,
    propose_action_type: str,
    review_action_type: str,
    match_action_type: str,
    prior_feedback: Optional[str] = None,
    cycle: int = 1,
) -> List[Candidate]:
    """一个完整的 Generation -> literature -> Reflection -> Proximity -> Tournament 子流程"""
    queries = generation_agent.generate_queries(topic, research_question, num_queries)
    action_logger.log_action(
        round_num, "Generation", "SEARCH_LITERATURE",
        {"queries": queries}, result=f"{len(queries)} queries generated"
    )

    literature_snippets = []
    for q in queries:
        results = literature_service.search(q, limit=5)
        for r in results:
            literature_snippets.append(f"- {r.title} ({r.year}, {r.source}): {r.abstract or 'no abstract'}")
    literature_context = "\n".join(literature_snippets) or "No literature found."

    candidates = generation_agent.propose_candidates(
        research_question=research_question,
        literature_context=literature_context,
        num_candidates=num_candidates,
        candidate_kind=candidate_kind,
        cycle=cycle,
        prior_feedback=prior_feedback,
    )
    action_logger.log_action(
        round_num, "Generation", propose_action_type,
        {"count": len(candidates), "names": [c.name for c in candidates]},
        result=f"{len(candidates)} {candidate_kind} candidates proposed"
    )

    for c in candidates:
        review = reflection_agent.detailed_review(c, research_question)
        action_logger.log_action(
            round_num, "Reflection", review_action_type,
            {"candidate": c.name, "confidence": review.confidence},
            result=review.critique,
        )

    if len(candidates) > 2:
        clusters = proximity_agent.cluster(candidates)
        if len(clusters) < len(candidates):
            action_logger.log_action(
                round_num, "Proximity", "MERGE_DUPLICATE_CANDIDATES",
                {"clusters_count": len(clusters), "candidates_count": len(candidates)},
                result=f"grouped {len(candidates)} candidates into {len(clusters)} clusters"
            )

    matches = run_tournament(
        candidates, research_question, tournament_agent, ranking_agent,
        action_logger, round_num, match_action_type,
    )
    candidates[0].__dict__.setdefault("_matches", matches)  # stash for caller convenience
    return candidates, matches


def run_research_loop(config: Dict[str, Any], sim_dir: str):
    research_question = config["research_question"]
    num_queries = config.get("num_queries", 3)
    num_candidates = config.get("num_candidates", 5)
    num_assays = config.get("num_assays", 3)
    num_cycles = config.get("num_cycles", 1)

    action_logger = ActionLogger(sim_dir)
    action_logger.log_event("simulation_start", research_question=research_question)

    llm_client = LLMClient()
    literature_service = LiteratureSearchService()
    generation_agent = GenerationAgent(llm_client)
    reflection_agent = ReflectionAgent(llm_client, literature_service)
    proximity_agent = ProximityAgent(llm_client)
    tournament_agent = TournamentAgent()
    ranking_agent = RankingAgent(llm_client)
    evolution_agent = EvolutionAgent(llm_client)
    meta_review_agent = MetaReviewAgent(llm_client)

    round_num = 1

    # ========== Phase 1: Assay / 评估视角选择（镜像 Robin assays.py） ==========
    assay_candidates, assay_matches = run_generation_review_tournament_phase(
        research_question=research_question,
        topic=f"evaluation methodology/lens for: {research_question}",
        num_queries=num_queries,
        num_candidates=num_assays,
        candidate_kind="evaluation lens",
        literature_service=literature_service,
        generation_agent=generation_agent,
        reflection_agent=reflection_agent,
        proximity_agent=proximity_agent,
        tournament_agent=tournament_agent,
        ranking_agent=ranking_agent,
        action_logger=action_logger,
        round_num=round_num,
        propose_action_type="PROPOSE_ASSAY",
        review_action_type="REVIEW_ASSAY",
        match_action_type="ASSAY_MATCH",
    )

    assay_dir = os.path.join(sim_dir, "research")
    _write_literature_reviews(os.path.join(assay_dir, "assay_literature_reviews"), assay_candidates)
    _write_summary_txt(os.path.join(assay_dir, "assay_summary.txt"), assay_candidates, "Assay Candidate")
    _write_detailed_reports(os.path.join(assay_dir, "assay_detailed_hypotheses"), assay_candidates)
    _write_ranking_results(os.path.join(assay_dir, "assay_ranking_results.csv"), assay_candidates, assay_matches)

    top_assay = max(assay_candidates, key=lambda c: c.elo_rating) if assay_candidates else None
    if top_assay:
        action_logger.log_action(
            round_num, "Ranking", "SELECT_ASSAY",
            {"selected": top_assay.name, "elo_rating": round(top_assay.elo_rating, 1)},
            result=top_assay.statement,
        )

    # 综合生成目标（镜像 Robin 的 synthesize_candidate_goal）
    generation_goal = research_question
    if top_assay:
        synth_prompt = (
            f"Research question: {research_question}\n"
            f"Selected evaluation lens: {top_assay.statement}\n\n"
            f"Synthesize a single refined generation goal (one paragraph) that combines "
            f"the research question with this evaluation lens, to guide hypothesis generation."
        )
        generation_goal = llm_client.chat(
            messages=[
                {"role": "system", "content": (
                    "You are the Generation Agent's planning step.\n\n"
                    f"{get_language_instruction()}"
                )},
                {"role": "user", "content": synth_prompt},
            ],
            temperature=0.4,
        )
        action_logger.log_action(
            round_num, "Generation", "SYNTHESIZE_GENERATION_GOAL",
            {"selected_assay": top_assay.name}, result=generation_goal,
        )

    # ========== Phase 2: Hypothesis Generation（镜像 Robin candidates.py），支持多周期 ==========
    prior_feedback = None
    hypothesis_candidates: List[Candidate] = []
    hypothesis_matches: List[MatchResult] = []

    for cycle in range(1, num_cycles + 1):
        round_num += 1
        cycle_candidates, cycle_matches = run_generation_review_tournament_phase(
            research_question=generation_goal,
            topic=generation_goal,
            num_queries=num_queries * 2,  # 镜像 Robin 的 double_queries
            num_candidates=num_candidates,
            candidate_kind="hypothesis",
            literature_service=literature_service,
            generation_agent=generation_agent,
            reflection_agent=reflection_agent,
            proximity_agent=proximity_agent,
            tournament_agent=tournament_agent,
            ranking_agent=ranking_agent,
            action_logger=action_logger,
            round_num=round_num,
            propose_action_type="PROPOSE_HYPOTHESIS",
            review_action_type="REVIEW_HYPOTHESIS",
            match_action_type="HYPOTHESIS_MATCH",
            prior_feedback=prior_feedback,
            cycle=cycle,
        )
        hypothesis_candidates = cycle_candidates
        hypothesis_matches = cycle_matches

        top_n = sorted(hypothesis_candidates, key=lambda c: c.elo_rating, reverse=True)[:max(2, num_candidates // 2)]
        if cycle < num_cycles:
            refined = evolution_agent.refine(top_n, research_question)
            action_logger.log_action(
                round_num, "Evolution", "REFINE_HYPOTHESIS",
                {"input_count": len(top_n), "output_count": len(refined)},
                result=f"refined {len(top_n)} top hypotheses into {len(refined)} for next cycle"
            )

        meta_summary = meta_review_agent.synthesize(hypothesis_candidates, hypothesis_matches, research_question)
        action_logger.log_action(
            round_num, "MetaReview", "META_REVIEW",
            {"key_patterns": meta_summary.key_patterns},
            result=meta_summary.summary,
        )
        prior_feedback = "\n".join(meta_summary.recommendations_for_next_cycle)

        action_logger.log_event("round_end", round=round_num, simulated_hours=cycle)

    hyp_dir = os.path.join(sim_dir, "research")
    _write_literature_reviews(os.path.join(hyp_dir, "hypothesis_literature_reviews"), hypothesis_candidates)
    _write_summary_txt(os.path.join(hyp_dir, "hypothesis_summary.txt"), hypothesis_candidates, "Hypothesis Candidate")
    _write_detailed_reports(os.path.join(hyp_dir, "hypothesis_detailed_reports"), hypothesis_candidates)
    _write_ranking_results(os.path.join(hyp_dir, "hypothesis_ranking_results.csv"), hypothesis_candidates, hypothesis_matches)
    _write_ranked_csv(os.path.join(sim_dir, "ranked_hypotheses.csv"), hypothesis_candidates)

    action_logger.log_event(
        "simulation_end", total_rounds=round_num,
        total_actions=len(assay_candidates) + len(hypothesis_candidates),
    )

    return {
        "assay_candidates": assay_candidates,
        "hypothesis_candidates": hypothesis_candidates,
        "top_assay": top_assay,
        "generation_goal": generation_goal,
    }


def handle_consult(role: str, prompt: str, final_state: Dict[str, Any], llm_client: LLMClient) -> str:
    """处理 consult_agent IPC 命令：让某个固定角色基于最终状态回答问题"""
    hypothesis_candidates = final_state.get("hypothesis_candidates", [])
    ranked = sorted(hypothesis_candidates, key=lambda c: c.elo_rating, reverse=True)
    context = "\n".join(
        f"[Elo {c.elo_rating:.0f}] {c.name}: {c.statement}\n"
        f"Review: {c.detailed_review.critique if c.detailed_review else 'n/a'}"
        for c in ranked
    )
    system_prompt = (
        f"You are the {role} Agent in a scientific co-scientist research loop. "
        f"Answer the user's question in character, based on the final state of the research cycle below.\n\n"
        f"{get_language_instruction()}"
    )
    user_prompt = f"Final ranked hypotheses:\n{context}\n\nQuestion: {prompt}"
    return llm_client.chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.5,
    )


def run_ipc_loop(sim_dir: str, final_state: Dict[str, Any], llm_client: LLMClient):
    """运行完成后进入IPC等待模式，接受 consult_agent / close_env 命令"""
    server = SimulationIPCServer(sim_dir)
    server.start()
    last_command_time = time.time()

    try:
        while time.time() - last_command_time < IDLE_TIMEOUT_SECONDS:
            command = server.poll_commands()
            if command is None:
                time.sleep(1)
                continue

            last_command_time = time.time()
            try:
                if command.command_type == CommandType.CONSULT_AGENT:
                    role = command.args.get("role", "MetaReview")
                    prompt = command.args.get("prompt", "")
                    answer = handle_consult(role, prompt, final_state, llm_client)
                    server.send_success(command.command_id, {"role": role, "response": answer})

                elif command.command_type == CommandType.BATCH_CONSULT:
                    results = {}
                    for item in command.args.get("consultations", []):
                        role = item.get("role", "MetaReview")
                        prompt = item.get("prompt", "")
                        results[role] = handle_consult(role, prompt, final_state, llm_client)
                    server.send_success(command.command_id, {"results": results})

                elif command.command_type == CommandType.CLOSE_ENV:
                    server.send_success(command.command_id, {"message": "closing"})
                    break

            except Exception as e:
                server.send_error(command.command_id, str(e))
    finally:
        server.stop()


def main():
    set_locale(os.environ.get("MIROFISH_LOCALE", "zh"))

    parser = argparse.ArgumentParser(description="Run the two-phase Elo-tournament research loop")
    parser.add_argument("--config", required=True, help="Path to simulation_config.json")
    args = parser.parse_args()

    with open(args.config, 'r', encoding='utf-8') as f:
        config = json.load(f)

    sim_dir = os.path.dirname(os.path.abspath(args.config))

    final_state = run_research_loop(config, sim_dir)

    llm_client = LLMClient()
    run_ipc_loop(sim_dir, final_state, llm_client)


if __name__ == "__main__":
    main()
