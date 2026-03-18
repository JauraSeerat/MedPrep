from __future__ import annotations

import math
import os
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    import numpy as np

from .models import MappingItem


@dataclass
class MissingPoint:
    missed_concept: str
    reference_excerpt: str
    confidence: str


@dataclass
class QuestionFeedback:
    question_id: str
    missing_points: List[MissingPoint]
    evaluation_notes: str
    hallucination_risk: str


class SemanticEvaluator:
    """Lightweight semantic evaluator for local/offline use.

    If sentence-transformers is unavailable, falls back to lexical overlap.
    """

    def __init__(self, sim_threshold: float = 0.58, use_embeddings: bool | None = None):
        self.sim_threshold = sim_threshold
        if use_embeddings is False:
            self._model = None
        else:
            self._model = _load_model_or_none()

    def evaluate(self, mapping: MappingItem, student_answer: str) -> QuestionFeedback:
        qid = (
            f"exam{mapping.exam_id}_s{mapping.session_id}_"
            f"{mapping.section_name.lower().replace(' ', '_')}_q{mapping.question_number}"
        )
        try:
            if not (student_answer or "").strip():
                return QuestionFeedback(
                    question_id=qid,
                    missing_points=[],
                    evaluation_notes="no_answer",
                    hallucination_risk="low",
                )

            if not (mapping.answer_text or "").strip():
                return QuestionFeedback(
                    question_id=qid,
                    missing_points=[],
                    evaluation_notes="No reference answer available",
                    hallucination_risk="high",
                )

            ref_points = _split_reference_points(mapping.answer_text)
            student_sentences = _split_student_sentences(student_answer)

            missing: List[MissingPoint] = []
            for point in ref_points:
                sim = self._max_similarity(point, student_sentences)
                if sim < self.sim_threshold:
                    confidence = _confidence_from_similarity(sim)
                    missing.append(
                        MissingPoint(
                            missed_concept=_short_label(point),
                            reference_excerpt=point,
                            confidence=confidence,
                        )
                    )

            risk = "low"
            if not ref_points:
                risk = "high"
            elif len(missing) > max(8, len(ref_points) * 0.8):
                risk = "medium"

            return QuestionFeedback(
                question_id=qid,
                missing_points=missing,
                evaluation_notes="",
                hallucination_risk=risk,
            )
        except Exception as e:
            return QuestionFeedback(
                question_id=qid,
                missing_points=[],
                evaluation_notes=f"evaluation_error: {type(e).__name__}",
                hallucination_risk="high",
            )

    def _max_similarity(self, ref_point: str, student_sentences: List[str]) -> float:
        if not student_sentences:
            return 0.0

        if self._model is not None:
            try:
                ref_emb = _embed(self._model, [ref_point])[0]
                stu_embs = _embed(self._model, student_sentences)
                sims = [_cosine(ref_emb, e) for e in stu_embs]
                return float(max(sims)) if sims else 0.0
            except Exception:
                self._model = None

        return max(_token_overlap(ref_point, s) for s in student_sentences)


def _split_reference_points(text: str) -> List[str]:
    raw = re.split(r"\n+|\s(?=\d+[\)\.])|\s(?=[-*•])", text)
    pts = [re.sub(r"\s+", " ", p.strip(" -\t")) for p in raw]
    pts = [p for p in pts if len(p) >= 24]

    # Keep list manageable for low-resource devices.
    return pts[:30]


def _split_student_sentences(text: str) -> List[str]:
    s = re.sub(r"\s+", " ", text.strip())
    if not s:
        return []
    parts = re.split(r"(?<=[\.!?])\s+", s)
    return [p.strip() for p in parts if len(p.strip()) > 2]


def _short_label(text: str) -> str:
    words = text.split()
    return " ".join(words[:8]).strip()


def _confidence_from_similarity(sim: float) -> str:
    if sim < 0.35:
        return "high"
    if sim < 0.5:
        return "medium"
    return "low"


@lru_cache(maxsize=1)
def _load_model_or_none():
    disable_env = os.environ.get("MEDPREPAI_DISABLE_EMBEDDINGS", "").strip().lower()
    if disable_env in {"1", "true", "yes"}:
        return None
    if sys.platform == "darwin":
        enable_env = os.environ.get("MEDPREPAI_ENABLE_EMBEDDINGS", "").strip().lower()
        if enable_env not in {"1", "true", "yes"}:
            return None
    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer("all-MiniLM-L6-v2")
    except Exception:
        return None


def _embed(model, texts: List[str]):
    return model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)


def _cosine(a, b) -> float:
    try:
        import numpy as np
    except Exception:
        # Fallback: simple dot/len for sequences.
        denom = (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b)))
        if denom == 0:
            return 0.0
        return float(sum(x * y for x, y in zip(a, b)) / denom)

    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def _token_overlap(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-zA-Z0-9]+", a.lower()))
    tb = set(re.findall(r"[a-zA-Z0-9]+", b.lower()))
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return inter / max(1, len(ta))
