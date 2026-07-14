"""
文献检索服务
用于科学假设检验流程中的文献种子采集与循环内证据检索

主源：Semantic Scholar Graph API（免费，低量无需密钥）
备源：arXiv Atom API（Semantic Scholar 报错或结果不足时使用）
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional

import httpx

from ..config import Config
from ..utils.logger import get_logger
from ..utils.retry import retry_with_backoff

logger = get_logger('mirofish.literature_search')

# 非ASCII字符占比超过此阈值时，认为查询是非英文，需要先翻译成英文
# 原因：Semantic Scholar 对非英文查询的召回率明显更低，arXiv 的索引几乎全是英文文献，
# 非英文查询在 arXiv 上几乎总是返回 0 条结果
NON_ASCII_TRANSLATE_THRESHOLD = 0.3

# 英文停用词表，用于从查询中提取有实际检索意义的关键词
_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for",
    "with", "from", "into", "onto", "this", "that", "these", "those",
    "its", "it", "their", "his", "her", "our", "your", "what", "which",
    "who", "whom", "does", "did", "do", "is", "are", "was", "were", "be",
    "been", "being", "have", "has", "had", "can", "could", "will",
    "would", "should", "may", "might", "must", "about", "between", "as",
    "by", "such", "than", "how", "why", "not", "no",
}


def _extract_keywords(query: str) -> List[str]:
    """从查询中提取用于相关性校验的关键词（过滤停用词与过短词，保留最长的若干个）"""
    words = re.findall(r"[A-Za-z0-9]+", query)
    keywords = [w for w in words if len(w) >= 4 and w.lower() not in _STOPWORDS]
    keywords.sort(key=len, reverse=True)
    return keywords[:8]

SEMANTIC_SCHOLAR_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
SEMANTIC_SCHOLAR_FIELDS = "title,authors,year,venue,abstract,url,externalIds"
ARXIV_SEARCH_URL = "https://export.arxiv.org/api/query"
ARXIV_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


@dataclass
class LiteratureResult:
    """一篇检索到的文献"""
    title: str
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    venue: Optional[str] = None
    abstract: Optional[str] = None
    url: Optional[str] = None
    doi: Optional[str] = None
    source: str = "unknown"  # "semantic_scholar" | "arxiv"
    snippet: Optional[str] = None

    def to_seed_text(self) -> str:
        """将文献信息转换为可供本体生成/图谱构建使用的种子文本块"""
        authors_str = ", ".join(self.authors) if self.authors else "Unknown authors"
        year_str = str(self.year) if self.year else "n.d."
        venue_str = f" ({self.venue})" if self.venue else ""
        parts = [
            f"Title: {self.title}",
            f"Authors: {authors_str}",
            f"Year: {year_str}{venue_str}",
        ]
        if self.abstract:
            parts.append(f"Abstract: {self.abstract}")
        if self.url:
            parts.append(f"URL: {self.url}")
        return "\n".join(parts)


class LiteratureSearchService:
    """文献检索服务：封装 Semantic Scholar（主）与 arXiv（备）"""

    def __init__(self, timeout: Optional[int] = None):
        self.timeout = timeout or Config.LITERATURE_SEARCH_TIMEOUT

    @staticmethod
    def _is_mostly_non_ascii(text: str) -> bool:
        """粗略判断查询是否主要由非ASCII字符组成（如中文/日文/韩文等）"""
        if not text:
            return False
        non_ascii_count = sum(1 for ch in text if ord(ch) > 127)
        return (non_ascii_count / len(text)) > NON_ASCII_TRANSLATE_THRESHOLD

    def _to_english_query(self, query: str) -> str:
        """
        将非英文查询翻译/精炼为适合学术检索的英文查询

        Semantic Scholar 与 arXiv 的文献索引几乎全部是英文文献，非英文查询
        （尤其是 arXiv）几乎总是召回 0 条结果，因此在检索前先转换为英文查询。
        """
        try:
            from ..utils.llm_client import LLMClient
            llm_client = LLMClient()
            response = llm_client.chat(
                messages=[
                    {"role": "system", "content": (
                        "Translate the following research query into a concise English "
                        "academic search query suitable for querying Semantic Scholar or "
                        "arXiv. Output only the translated query, nothing else."
                    )},
                    {"role": "user", "content": query},
                ],
                temperature=0.2,
                max_tokens=800,  # 部分推理模型（如deepseek-v4-pro）需要更大的token预算完成思考过程
            )
            translated = response.strip().strip('"').strip()
            if translated:
                logger.info(f"文献检索查询已翻译为英文: {query!r} -> {translated!r}")
                return translated
        except Exception:
            logger.exception(f"查询翻译失败，回退使用原始查询: {query!r}")
        return query

    def to_seed_text_localized(self, result: LiteratureResult) -> str:
        """
        将文献标题与摘要翻译为当前语言环境后再生成种子文本块

        原始种子文本（标题/摘要）几乎总是英文（学术文献索引以英文为主），若不翻译，
        会作为 document_texts 直接送入 Zep 构图，导致图谱中出现纯英文的实体/事实内容。
        译文仅用于构图种子；作者/年份/期刊/URL 等引用元信息保持原样不翻译。
        """
        from ..utils.locale import get_locale

        if get_locale() == 'en' or not result.abstract:
            return result.to_seed_text()

        try:
            from ..utils.llm_client import LLMClient
            llm_client = LLMClient()
            response = llm_client.chat_json(
                messages=[
                    {"role": "system", "content": (
                        "Translate the following academic paper title and abstract into "
                        "fluent Chinese, preserving technical terms and meaning precisely. "
                        "Respond in JSON with keys \"title\" and \"abstract\", both translated."
                    )},
                    {"role": "user", "content": f"Title: {result.title}\nAbstract: {result.abstract}"},
                ],
                temperature=0.2,
                max_tokens=2000,
            )
            translated_title = response.get("title") or result.title
            translated_abstract = response.get("abstract") or result.abstract
        except Exception:
            logger.exception(f"文献种子文本翻译失败，回退使用原文: {result.title!r}")
            return result.to_seed_text()

        authors_str = ", ".join(result.authors) if result.authors else "Unknown authors"
        year_str = str(result.year) if result.year else "n.d."
        venue_str = f" ({result.venue})" if result.venue else ""
        parts = [
            f"标题: {translated_title}",
            f"作者: {authors_str}",
            f"年份: {year_str}{venue_str}",
            f"摘要: {translated_abstract}",
        ]
        if result.url:
            parts.append(f"URL: {result.url}")
        return "\n".join(parts)

    def search(self, query: str, limit: int = 10) -> List[LiteratureResult]:
        """
        检索与 query 相关的文献

        Args:
            query: 检索关键词/研究问题
            limit: 返回结果数量上限

        Returns:
            LiteratureResult 列表，若两个数据源均失败则返回空列表
        """
        limit = max(1, min(limit, Config.LITERATURE_SEARCH_MAX_RESULTS))

        if self._is_mostly_non_ascii(query):
            query = self._to_english_query(query)

        keywords = _extract_keywords(query)

        try:
            results = self._search_semantic_scholar(query, limit)
            results = self._filter_relevant(results, keywords, query)
            if len(results) >= limit:
                return results[:limit]
        except Exception:
            logger.exception(f"Semantic Scholar 检索失败，query={query!r}，回退到 arXiv")
            results = []

        if len(results) < limit:
            try:
                arxiv_results = self._search_arxiv(query, limit - len(results))
                arxiv_results = self._filter_relevant(arxiv_results, keywords, query)
                results.extend(arxiv_results)
            except Exception:
                logger.exception(f"arXiv 检索失败，query={query!r}")

        return results[:limit]

    @staticmethod
    def _filter_relevant(
        results: List[LiteratureResult], keywords: List[str], query: str
    ) -> List[LiteratureResult]:
        """
        过滤掉与查询完全不相关的检索结果

        背景：Semantic Scholar/arXiv 的公开检索接口在限流恢复后偶发返回与查询关键词
        零重合的"噪声"结果（例如查询 DreamerV3 世界模型，却返回外科手术感染研究），
        若不过滤会直接把无关文献当作种子文本喂给图谱构建，导致图谱实体与查询主题完全脱节。
        校验方式：结果标题+摘要必须至少命中一个查询关键词才保留。
        """
        if not keywords:
            return results
        relevant = []
        for r in results:
            haystack = f"{r.title} {r.abstract or ''}".lower()
            if any(kw.lower() in haystack for kw in keywords):
                relevant.append(r)
        dropped = len(results) - len(relevant)
        if dropped > 0:
            logger.warning(
                f"过滤掉 {dropped} 条与查询无关的检索结果（query={query!r}，"
                f"关键词={keywords}）"
            )
        return relevant

    @retry_with_backoff(max_retries=2, initial_delay=1.0, exceptions=(httpx.HTTPError,))
    def _search_semantic_scholar(self, query: str, limit: int) -> List[LiteratureResult]:
        headers = {}
        if Config.S2_API_KEY:
            headers["x-api-key"] = Config.S2_API_KEY

        response = httpx.get(
            SEMANTIC_SCHOLAR_SEARCH_URL,
            params={
                "query": query,
                "limit": limit,
                "fields": SEMANTIC_SCHOLAR_FIELDS,
            },
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()

        results = []
        for paper in data.get("data", []):
            authors = [a.get("name", "") for a in (paper.get("authors") or []) if a.get("name")]
            external_ids = paper.get("externalIds") or {}
            results.append(LiteratureResult(
                title=paper.get("title") or "Untitled",
                authors=authors,
                year=paper.get("year"),
                venue=paper.get("venue") or None,
                abstract=paper.get("abstract") or None,
                url=paper.get("url") or None,
                doi=external_ids.get("DOI"),
                source="semantic_scholar",
            ))
        return results

    @retry_with_backoff(max_retries=2, initial_delay=1.0, exceptions=(httpx.HTTPError,))
    def _search_arxiv(self, query: str, limit: int) -> List[LiteratureResult]:
        response = httpx.get(
            ARXIV_SEARCH_URL,
            params={
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": limit,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()

        root = ET.fromstring(response.text)
        results = []
        for entry in root.findall("atom:entry", ARXIV_ATOM_NS):
            title_el = entry.find("atom:title", ARXIV_ATOM_NS)
            summary_el = entry.find("atom:summary", ARXIV_ATOM_NS)
            published_el = entry.find("atom:published", ARXIV_ATOM_NS)
            id_el = entry.find("atom:id", ARXIV_ATOM_NS)

            authors = [
                (name_el.text or "").strip()
                for author_el in entry.findall("atom:author", ARXIV_ATOM_NS)
                for name_el in [author_el.find("atom:name", ARXIV_ATOM_NS)]
                if name_el is not None and name_el.text
            ]

            year = None
            if published_el is not None and published_el.text:
                try:
                    year = int(published_el.text[:4])
                except ValueError:
                    pass

            results.append(LiteratureResult(
                title=(title_el.text or "Untitled").strip() if title_el is not None else "Untitled",
                authors=authors,
                year=year,
                venue="arXiv",
                abstract=(summary_el.text or "").strip() if summary_el is not None else None,
                url=(id_el.text or "").strip() if id_el is not None else None,
                doi=None,
                source="arxiv",
            ))
        return results
