from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

from .models import AnswerItem, MappingItem, QuestionItem


def build_mappings(
    questions: List[QuestionItem],
    answers: List[AnswerItem],
) -> tuple[List[MappingItem], List[AnswerItem]]:
    a_map: Dict[Tuple[int, int, str, int], List[AnswerItem]] = defaultdict(list)
    for a in sorted(answers, key=lambda x: (x.exam_id, x.session_id, x.section_order, x.question_number)):
        key = (a.exam_id, a.session_id, a.section_name, a.question_number)
        a_map[key].append(a)

    mappings: List[MappingItem] = []
    for q in sorted(questions, key=lambda x: (x.exam_id, x.session_id, x.section_order, x.question_order, x.question_number)):
        key = (q.exam_id, q.session_id, q.section_name, q.question_number)
        ans = a_map[key].pop(0) if a_map.get(key) else None
        if ans:
            mappings.append(
                MappingItem(
                    exam_id=q.exam_id,
                    session_id=q.session_id,
                    section_name=q.section_name,
                    section_order=q.section_order,
                    question_number=q.question_number,
                    question_order=q.question_order,
                    question_text=q.question_text,
                    answer_text=ans.answer_text,
                    matched=True,
                    match_reason="exact exam/session/section/question_number",
                )
            )
        else:
            mappings.append(
                MappingItem(
                    exam_id=q.exam_id,
                    session_id=q.session_id,
                    section_name=q.section_name,
                    section_order=q.section_order,
                    question_number=q.question_number,
                    question_order=q.question_order,
                    question_text=q.question_text,
                    answer_text=None,
                    matched=False,
                    match_reason="missing answer",
                )
            )

    unmatched_answers: List[AnswerItem] = []
    for key in sorted(a_map.keys()):
        if a_map[key]:
            unmatched_answers.extend(a_map[key])
    return mappings, unmatched_answers


def apply_manual_assignment(
    mappings: List[MappingItem],
    unmatched_answers: List[AnswerItem],
    answer_idx: int,
    target_exam: int,
    target_session: int,
    target_section: str,
    target_question_number: int,
) -> tuple[List[MappingItem], List[AnswerItem], str]:
    if answer_idx < 0 or answer_idx >= len(unmatched_answers):
        return mappings, unmatched_answers, "Invalid unmatched answer selection"

    selected = unmatched_answers[answer_idx]
    target_section_norm = target_section.strip().title()

    target = None
    for m in mappings:
        if (
            m.exam_id == target_exam
            and m.session_id == target_session
            and m.section_name == target_section_norm
            and m.question_number == target_question_number
        ):
            target = m
            break

    if target is None:
        return mappings, unmatched_answers, "Target question not found"

    target.answer_text = selected.answer_text
    target.matched = True
    target.match_reason = "manual mapping"

    remaining = [a for i, a in enumerate(unmatched_answers) if i != answer_idx]
    return mappings, remaining, "Manual mapping applied"


def summarize_mapping(mappings: List[MappingItem], unmatched_answers: List[AnswerItem]) -> dict:
    total = len(mappings)
    matched = sum(1 for m in mappings if m.matched and m.answer_text)
    unanswered = total - matched

    by_exam = defaultdict(lambda: {"total": 0, "matched": 0})
    for m in mappings:
        key = f"Exam {m.exam_id} / Session {m.session_id}"
        by_exam[key]["total"] += 1
        if m.matched and m.answer_text:
            by_exam[key]["matched"] += 1

    return {
        "total_questions": total,
        "matched_questions": matched,
        "unanswered_questions": unanswered,
        "unmatched_answer_blocks": len(unmatched_answers),
        "by_exam_session": dict(by_exam),
    }


def find_duplicates(
    questions: List[QuestionItem],
    answers: List[AnswerItem],
) -> dict:
    q_keys: Dict[Tuple[int, int, str, int], List[QuestionItem]] = defaultdict(list)
    for q in questions:
        key = (q.exam_id, q.session_id, q.section_name, q.question_number)
        q_keys[key].append(q)

    a_keys: Dict[Tuple[int, int, str, int], List[AnswerItem]] = defaultdict(list)
    for a in answers:
        key = (a.exam_id, a.session_id, a.section_name, a.question_number)
        a_keys[key].append(a)

    dup_q = {k: v for k, v in q_keys.items() if len(v) > 1}
    dup_a = {k: v for k, v in a_keys.items() if len(v) > 1}

    return {
        "duplicate_question_keys": {
            _key_to_str(k): len(v) for k, v in sorted(dup_q.items())
        },
        "duplicate_answer_keys": {
            _key_to_str(k): len(v) for k, v in sorted(dup_a.items())
        },
        "duplicate_question_key_count": len(dup_q),
        "duplicate_answer_key_count": len(dup_a),
    }


def _key_to_str(key: Tuple[int, int, str, int]) -> str:
    exam, session, section, qnum = key
    return f"Exam {exam} / Session {session} / {section} / Q{qnum}"
