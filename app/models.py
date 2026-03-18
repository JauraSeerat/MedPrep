from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class QuestionItem:
    exam_id: int
    session_id: int
    section_name: str
    section_order: int
    question_number: int
    question_order: int
    question_text: str
    source: str


@dataclass
class AnswerItem:
    exam_id: int
    session_id: int
    section_name: str
    section_order: int
    question_number: int
    answer_text: str
    source: str


@dataclass
class MappingItem:
    exam_id: int
    session_id: int
    section_name: str
    section_order: int
    question_number: int
    question_order: int
    question_text: str
    answer_text: Optional[str] = None
    matched: bool = False
    match_reason: str = ""


@dataclass
class KnowledgeBase:
    name: str
    questions_pdf: str
    answers_pdf: str
    session_stems: Dict[str, str] = field(default_factory=dict)
    mappings: List[MappingItem] = field(default_factory=list)
    unmatched_answers: List[AnswerItem] = field(default_factory=list)
