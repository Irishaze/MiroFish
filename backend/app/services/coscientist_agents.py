"""
AI Co-Scientist 风格的固定功能角色（非LLM生成的人设）

架构参考：
- Robin (FutureHouse, github.com/Future-House/robin) 的真实两阶段流水线机制：
  query生成 -> 文献检索 -> 提出候选 -> 详细报告 -> 两两对比排名
  （见 robin/candidates.py::therapeutic_candidates, robin/assays.py::experimental_assay）
- Google DeepMind "AI co-scientist" 论文的7个固定功能角色命名与Elo锦标赛机制

七个角色全部是代码内固定的system prompt驱动函数，而非按项目由LLM生成的人设——
所有项目共享同一套角色定义，只有检索到的文献与候选池内容随项目变化。
"""

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..utils.llm_client import LLMClient
from ..utils.locale import get_language_instruction
from ..utils.logger import get_logger
from .literature_search import LiteratureResult, LiteratureSearchService

logger = get_logger('mirofish.coscientist_agents')

ELO_INITIAL_RATING = 1200
ELO_K_FACTOR = 32


@dataclass
class Candidate:
    """一个候选项（可以是"评估视角"或"假设"，由track区分）"""
    candidate_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = ""
    statement: str = ""
    reasoning: str = ""
    elo_rating: float = ELO_INITIAL_RATING
    match_history: List[Dict[str, Any]] = field(default_factory=list)
    detailed_review: Optional["DetailedReview"] = None
    cluster_id: Optional[str] = None
    cycle: int = 1


@dataclass
class DetailedReview:
    """Reflection Agent 对单个候选项的详细文献综述与评估"""
    candidate_id: str
    literature_reviewed: List[LiteratureResult]
    critique: str
    strengths: str
    weaknesses: str
    confidence: str  # "high" | "medium" | "low"


@dataclass
class MatchResult:
    """Tournament + Ranking Agent 的一场两两对比结果"""
    candidate_a_id: str
    candidate_b_id: str
    winner_id: str
    rationale: str


@dataclass
class MetaReviewSummary:
    """Meta-Review Agent 对整个周期的综合总结"""
    summary: str
    key_patterns: List[str]
    recommendations_for_next_cycle: List[str]


def _parse_candidate_blocks(raw_text: str) -> List[Dict[str, str]]:
    """
    解析形如 Robin candidates.py 中 <CANDIDATE START>...<CANDIDATE END> 的候选块

    每个块内以 "KEY: value" 形式包含 NAME / STATEMENT / REASONING 三个字段
    """
    blocks = []
    raw_blocks = raw_text.strip().split("<CANDIDATE END>")
    for block_content in raw_blocks:
        block_content = block_content.strip()
        if not block_content or "<CANDIDATE START>" not in block_content:
            continue
        content_str = block_content.split("<CANDIDATE START>", 1)[1].strip()

        field_data: Dict[str, str] = {}
        current_key = None
        accumulated: List[str] = []
        for line in content_str.split("\n"):
            line_stripped = line.strip()
            if not line_stripped:
                continue
            match = re.match(r"^([A-Z_]+):\s*(.*)", line)
            if match:
                if current_key and accumulated:
                    field_data[current_key] = "\n".join(accumulated).strip()
                current_key = match.group(1).strip()
                initial_value = match.group(2).strip()
                accumulated = [initial_value] if initial_value else []
            elif current_key:
                accumulated.append(line_stripped)
        if current_key and accumulated:
            field_data[current_key] = "\n".join(accumulated).strip()

        if field_data.get("NAME") and field_data.get("STATEMENT"):
            blocks.append(field_data)
    return blocks


class GenerationAgent:
    """Generation Agent：生成检索查询 + 提出候选项（镜像 Robin Step 1/3）"""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    def generate_queries(self, topic: str, research_question: str, num_queries: int) -> List[str]:
        """镜像 Robin candidates.py Step 1：为文献检索生成若干查询语句"""
        system_prompt = (
            "You are the Generation Agent in a scientific hypothesis-testing pipeline. "
            "Your job is to formulate precise literature-search queries that will surface "
            "evidence relevant to the research question and the current sub-topic.\n\n"
            f"{get_language_instruction()}\n"
            "NOTE: the queries themselves must stay in English (or be translated to English) "
            "since they are sent to English-language academic search engines (Semantic Scholar, "
            "arXiv) — the language instruction above applies only if you add any surrounding prose."
        )
        user_prompt = (
            f"Research question: {research_question}\n"
            f"Current sub-topic to search for: {topic}\n\n"
            f"Generate exactly {num_queries} distinct, specific literature-search queries "
            f"(each a short phrase suitable for an academic search engine). "
            f"Separate queries with the delimiter '<>' and output nothing else."
        )
        result = self.llm_client.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.5,
        )
        queries = [q.strip() for q in result.split("<>") if q.strip()]
        return queries[:num_queries] if queries else [topic]

    def propose_candidates(
        self,
        research_question: str,
        literature_context: str,
        num_candidates: int,
        candidate_kind: str,
        cycle: int = 1,
        prior_feedback: Optional[str] = None,
    ) -> List[Candidate]:
        """镜像 Robin candidates.py Step 3：基于检索到的文献提出候选项"""
        system_prompt = (
            "You are the Generation Agent in a scientific hypothesis-testing pipeline. "
            f"Your job is to propose {candidate_kind} candidates, grounded strictly in the "
            "literature evidence provided. Each candidate must be a specific, falsifiable, "
            "literature-supported proposal — not a vague generality.\n\n"
            f"{get_language_instruction()} (NAME/STATEMENT/REASONING field values must be in "
            "this language; the literal field labels NAME/STATEMENT/REASONING and the "
            "<CANDIDATE START>/<CANDIDATE END> delimiters must stay exactly as specified.)"
        )
        feedback_block = (
            f"\n\nFeedback from the previous cycle's Meta-Review Agent to address:\n{prior_feedback}"
            if prior_feedback else ""
        )
        user_prompt = (
            f"Research question: {research_question}\n\n"
            f"Literature evidence gathered:\n{literature_context}\n"
            f"{feedback_block}\n\n"
            f"Propose exactly {num_candidates} distinct {candidate_kind} candidates. "
            f"For each, output a block in exactly this format:\n\n"
            f"<CANDIDATE START>\n"
            f"NAME: <short candidate name>\n"
            f"STATEMENT: <the full candidate statement/hypothesis>\n"
            f"REASONING: <why the literature supports considering this candidate>\n"
            f"<CANDIDATE END>\n\n"
            f"Output only the candidate blocks, nothing else."
        )
        raw = self.llm_client.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=4096,
        )
        blocks = _parse_candidate_blocks(raw)
        candidates = [
            Candidate(
                name=b["NAME"],
                statement=b["STATEMENT"],
                reasoning=b.get("REASONING", ""),
                cycle=cycle,
            )
            for b in blocks
        ]
        if not candidates:
            logger.warning(f"Generation Agent 未能解析出候选项，原始输出: {raw[:300]}")
        return candidates[:num_candidates]


class ReflectionAgent:
    """Reflection Agent：对每个候选项做文献扎根的详细评审（镜像 Robin Step 4）"""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        literature_service: Optional[LiteratureSearchService] = None,
    ):
        self.llm_client = llm_client or LLMClient()
        self.literature_service = literature_service or LiteratureSearchService()

    def detailed_review(self, candidate: Candidate, research_question: str) -> DetailedReview:
        literature = self.literature_service.search(candidate.statement, limit=5)
        literature_text = "\n\n".join(
            f"- {r.title} ({r.year}, {r.source}): {r.abstract or 'no abstract'}"
            for r in literature
        ) or "No additional literature found for this candidate."

        system_prompt = (
            "You are the Reflection Agent, acting as a rigorous peer reviewer. "
            "Critically evaluate the candidate against the literature evidence: check "
            "plausibility, identify strengths and weaknesses, flag unsupported claims.\n\n"
            f"{get_language_instruction()} (the JSON values must be in this language; the "
            "literal key names critique/strengths/weaknesses/confidence must stay as specified.)"
        )
        user_prompt = (
            f"Research question: {research_question}\n"
            f"Candidate: {candidate.name}\n"
            f"Statement: {candidate.statement}\n\n"
            f"Literature evidence:\n{literature_text}\n\n"
            f"Respond in JSON with keys: critique (string), strengths (string), "
            f"weaknesses (string), confidence (one of \"high\", \"medium\", \"low\")."
        )
        result = self.llm_client.chat_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
        )
        review = DetailedReview(
            candidate_id=candidate.candidate_id,
            literature_reviewed=literature,
            critique=result.get("critique", ""),
            strengths=result.get("strengths", ""),
            weaknesses=result.get("weaknesses", ""),
            confidence=result.get("confidence", "medium"),
        )
        candidate.detailed_review = review
        return review


class ProximityAgent:
    """Proximity Agent：对候选池去重/聚类，避免锦标赛浪费在近似重复项上（Robin中不存在，新增）"""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    def cluster(self, candidates: List[Candidate]) -> Dict[str, List[str]]:
        """返回 {cluster_id: [candidate_id, ...]}；每个候选项恰好属于一个簇"""
        if len(candidates) <= 1:
            return {c.candidate_id: [c.candidate_id] for c in candidates}

        listing = "\n".join(f"{c.candidate_id}: {c.statement}" for c in candidates)
        system_prompt = (
            "You are the Proximity Agent. Group candidates that propose essentially the "
            "same idea into the same cluster, so redundant candidates don't waste tournament "
            "budget. Distinct ideas must be in distinct clusters."
            f"\n\n{get_language_instruction()}"
        )
        user_prompt = (
            f"Candidates:\n{listing}\n\n"
            f"Respond in JSON: {{\"clusters\": [[\"id1\", \"id2\"], [\"id3\"], ...]}} "
            f"where every candidate id appears in exactly one cluster."
        )
        try:
            result = self.llm_client.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
            )
            clusters = {}
            for group in result.get("clusters", []):
                if not group:
                    continue
                cluster_id = f"cluster_{uuid.uuid4().hex[:6]}"
                clusters[cluster_id] = group
            valid_ids = {c.candidate_id for c in candidates}
            clustered_ids = {cid for group in clusters.values() for cid in group}
            missing = valid_ids - clustered_ids
            for cid in missing:
                clusters[f"cluster_{uuid.uuid4().hex[:6]}"] = [cid]
            return clusters
        except Exception:
            logger.exception("Proximity Agent 聚类失败，回退为每个候选项独立成簇")
            return {c.candidate_id: [c.candidate_id] for c in candidates}


class TournamentAgent:
    """Tournament Agent：安排两两对战配对（替代 Robin 的 uniformly_random_pairs）"""

    def schedule_matches(self, candidates: List[Candidate], num_matches: Optional[int] = None) -> List[Tuple[str, str]]:
        if len(candidates) < 2:
            return []
        if num_matches is None:
            num_matches = max(len(candidates) * 3, len(candidates) * (len(candidates) - 1) // 2)

        # 按当前Elo排序，优先安排评分相近的候选项对战（比纯随机更具信息量）
        sorted_candidates = sorted(candidates, key=lambda c: c.elo_rating)
        pairs = []
        n = len(sorted_candidates)
        idx = 0
        while len(pairs) < num_matches:
            a = sorted_candidates[idx % n]
            b = sorted_candidates[(idx + 1) % n]
            if a.candidate_id != b.candidate_id:
                pairs.append((a.candidate_id, b.candidate_id))
            idx += 1
            if idx > num_matches * 4:  # 安全阀，避免死循环
                break
        return pairs


class RankingAgent:
    """Ranking Agent：LLM两两裁判 + 真实Elo更新（替代 Robin 的 choix.ilsr_pairwise）"""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    def judge_match(
        self,
        candidate_a: Candidate,
        candidate_b: Candidate,
        research_question: str,
    ) -> MatchResult:
        system_prompt = (
            "You are the Ranking Agent. Given two candidate hypotheses/evaluation-lenses "
            "and their literature-grounded reviews, judge which is better supported by "
            "evidence, more specific, and more useful for answering the research question.\n\n"
            f"{get_language_instruction()} (the rationale value must be in this language; the "
            "literal key names winner/rationale and \"A\"/\"B\" values must stay as specified.)"
        )
        review_a = candidate_a.detailed_review
        review_b = candidate_b.detailed_review
        user_prompt = (
            f"Research question: {research_question}\n\n"
            f"Candidate A ({candidate_a.candidate_id}): {candidate_a.statement}\n"
            f"Review A: {review_a.critique if review_a else 'not yet reviewed'}\n\n"
            f"Candidate B ({candidate_b.candidate_id}): {candidate_b.statement}\n"
            f"Review B: {review_b.critique if review_b else 'not yet reviewed'}\n\n"
            f"Respond in JSON: {{\"winner\": \"A\" or \"B\", \"rationale\": \"...\"}}"
        )
        result = self.llm_client.chat_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
        winner_letter = result.get("winner", "A")
        winner_id = candidate_a.candidate_id if winner_letter == "A" else candidate_b.candidate_id

        self._apply_elo_update(candidate_a, candidate_b, winner_id)

        match = MatchResult(
            candidate_a_id=candidate_a.candidate_id,
            candidate_b_id=candidate_b.candidate_id,
            winner_id=winner_id,
            rationale=result.get("rationale", ""),
        )
        candidate_a.match_history.append({"opponent": candidate_b.candidate_id, "won": winner_id == candidate_a.candidate_id})
        candidate_b.match_history.append({"opponent": candidate_a.candidate_id, "won": winner_id == candidate_b.candidate_id})
        return match

    @staticmethod
    def _apply_elo_update(candidate_a: Candidate, candidate_b: Candidate, winner_id: str) -> None:
        """标准Elo评分更新（K=32），替代Robin的choix.ilsr_pairwise"""
        rating_a = candidate_a.elo_rating
        rating_b = candidate_b.elo_rating

        expected_a = 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
        expected_b = 1 - expected_a

        score_a = 1.0 if winner_id == candidate_a.candidate_id else 0.0
        score_b = 1.0 - score_a

        candidate_a.elo_rating = rating_a + ELO_K_FACTOR * (score_a - expected_a)
        candidate_b.elo_rating = rating_b + ELO_K_FACTOR * (score_b - expected_b)


class EvolutionAgent:
    """Evolution Agent：精炼/合并顶尖候选项，支持多周期迭代（Robin中不存在，新增）"""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    def refine(self, top_candidates: List[Candidate], research_question: str) -> List[Candidate]:
        if not top_candidates:
            return []

        listing = "\n\n".join(
            f"[{c.candidate_id}] {c.statement}\n"
            f"Critique: {c.detailed_review.critique if c.detailed_review else 'n/a'}"
            for c in top_candidates
        )
        system_prompt = (
            "You are the Evolution Agent. Given the top-ranked candidates and their "
            "critiques, produce refined versions: simplify overly complex statements, "
            "combine complementary candidates, and address weaknesses raised in review.\n\n"
            f"{get_language_instruction()} (NAME/STATEMENT/REASONING field values must be in "
            "this language; the literal field labels and <CANDIDATE START>/<CANDIDATE END> "
            "delimiters must stay exactly as specified.)"
        )
        user_prompt = (
            f"Research question: {research_question}\n\n"
            f"Top candidates:\n{listing}\n\n"
            f"Produce refined candidate blocks in exactly this format:\n\n"
            f"<CANDIDATE START>\n"
            f"NAME: <short name>\n"
            f"STATEMENT: <refined statement>\n"
            f"REASONING: <what was refined and why>\n"
            f"<CANDIDATE END>\n\n"
            f"Output one block per input candidate (refine in place, or combine two into one "
            f"if they're complementary). Output only the blocks."
        )
        raw = self.llm_client.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.6,
        )
        blocks = _parse_candidate_blocks(raw)
        refined = [
            Candidate(
                name=b["NAME"],
                statement=b["STATEMENT"],
                reasoning=b.get("REASONING", ""),
                cycle=top_candidates[0].cycle + 1,
            )
            for b in blocks
        ]
        return refined or top_candidates


class MetaReviewAgent:
    """Meta-Review Agent：跨周期综合总结，直接为ReportAgent提供输入（Robin中不存在，新增）"""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    def synthesize(
        self,
        candidates: List[Candidate],
        matches: List[MatchResult],
        research_question: str,
    ) -> MetaReviewSummary:
        ranked = sorted(candidates, key=lambda c: c.elo_rating, reverse=True)
        listing = "\n\n".join(
            f"[Elo {c.elo_rating:.0f}] {c.statement}\n"
            f"Review: {c.detailed_review.critique if c.detailed_review else 'n/a'}"
            for c in ranked
        )
        system_prompt = (
            "You are the Meta-Review Agent. Synthesize patterns across all candidate "
            "reviews and tournament outcomes for this cycle. Identify what made winning "
            "candidates strong, what recurring weaknesses appeared, and what the next "
            "generation cycle should focus on.\n\n"
            f"{get_language_instruction()} (the JSON values must be in this language; the "
            "literal key names summary/key_patterns/recommendations_for_next_cycle must "
            "stay as specified.)"
        )
        user_prompt = (
            f"Research question: {research_question}\n\n"
            f"Ranked candidates (highest Elo first):\n{listing}\n\n"
            f"Total tournament matches played: {len(matches)}\n\n"
            f"Respond in JSON with keys: summary (string), key_patterns (list of strings), "
            f"recommendations_for_next_cycle (list of strings)."
        )
        result = self.llm_client.chat_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
        )
        return MetaReviewSummary(
            summary=result.get("summary", ""),
            key_patterns=result.get("key_patterns", []),
            recommendations_for_next_cycle=result.get("recommendations_for_next_cycle", []),
        )
