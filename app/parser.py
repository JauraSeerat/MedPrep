from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

from .models import AnswerItem, QuestionItem

SECTION_PATTERNS = [
    (r"\bpre[-\s]?operative\b.*\bevaluation\b", "Preoperative Evaluation", 1),
    (r"\bintra[-\s]?operative\b.*\bmanagement\b", "Intraoperative Management", 2),
    (r"\bintra[-\s]?operative\b.*\bevaluation\b", "Intraoperative Management", 2),
    (r"\bpost[-\s]?operative\b.*\bcare\b", "Postoperative Care", 3),
    (r"\bpost[-\s]?operative\b.*\bmanagement\b", "Postoperative Care", 3),
    (r"\badditional\s+topics\b", "Additional Topics", 4),
]

def _detect_exam_from_line(normalized_line: str) -> int | None:
    # Strong header patterns
    m = re.search(r"sample\s+oral\s+examination\s*(\d+)", normalized_line)
    if m:
        return int(m.group(1))

    m = re.search(r"oral\s+examination\s+question\s*[-–—]?\s*sample\s*(\d+)", normalized_line)
    if m:
        return int(m.group(1))

    # Weaker patterns: only trust if the line looks like a header
    if any(token in normalized_line for token in ("oral", "examination", "question", "aba")):
        m = re.search(r"\bexam\s*(\d+)\b", normalized_line)
        if m:
            return int(m.group(1))
        m = re.search(r"\bsample\s*(\d+)\b", normalized_line)
        if m:
            return int(m.group(1))

    # Short standalone "Sample 3" headers
    if normalized_line.startswith("sample"):
        parts = normalized_line.split()
        if len(parts) <= 3:
            m = re.search(r"\bsample\s*(\d+)\b", normalized_line)
            if m:
                return int(m.group(1))

    return None


def detect_exam_id_from_filename(path: str) -> int | None:
    name = Path(path).stem.lower()
    roman_match = re.search(r"\bexam\s*([ivx]+)\b", name)
    if roman_match:
        return _roman_to_int(roman_match.group(1))

    digit_match = re.search(r"\bexam\s*(\d+)\b", name)
    if digit_match:
        return int(digit_match.group(1))

    if "mock oral exams i" in name:
        return 1

    trailing_roman = re.search(r"\b([ivx]+)\b", name)
    if trailing_roman:
        n = _roman_to_int(trailing_roman.group(1))
        if n:
            return n

    return None


def parse_questions(text: str, source_path: str) -> List[QuestionItem]:
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]

    current_exam = detect_exam_id_from_filename(source_path) or 1
    current_session = 1
    current_section = ""
    current_section_order = 0
    in_questions_block = False
    question_order_by_session: Dict[Tuple[int, int], int] = {}

    items: List[QuestionItem] = []
    pending_q_num = 0
    pending_q_lines: List[str] = []

    def flush_question() -> None:
        nonlocal pending_q_lines, pending_q_num
        if pending_q_num <= 0 or not current_section:
            pending_q_lines = []
            pending_q_num = 0
            return
        text_block = " ".join(pending_q_lines).strip()
        text_block = re.sub(r"\s+", " ", text_block)
        if not text_block:
            pending_q_lines = []
            pending_q_num = 0
            return
        key = (current_exam, current_session)
        question_order_by_session[key] = question_order_by_session.get(key, 0) + 1
        items.append(
            QuestionItem(
                exam_id=current_exam,
                session_id=current_session,
                section_name=current_section,
                section_order=current_section_order,
                question_number=pending_q_num,
                question_order=question_order_by_session[key],
                question_text=text_block,
                source=source_path,
            )
        )
        pending_q_lines = []
        pending_q_num = 0

    for line in lines:
        stripped = line.strip()
        low = _normalize(stripped)

        exam_id = _detect_exam_from_line(low)
        if exam_id:
            flush_question()
            current_exam = exam_id
            current_section = ""
            current_section_order = 0
            in_questions_block = False
            continue

        session_match = re.search(r"session\s*(\d+)\s*[-–]\s*\d+\s*minutes", low)
        if not session_match:
            session_match = re.search(r"session\s*(\d+)", low)
        if session_match:
            flush_question()
            current_session = int(session_match.group(1))
            current_section = ""
            current_section_order = 0
            in_questions_block = False

        section = _parse_section_header(low)
        if section:
            flush_question()
            current_section, current_section_order = section
            in_questions_block = True
            continue

        if not in_questions_block:
            continue

        q_match = re.match(r"^\s*(\d+)[\).]\s+(.+)", line)
        if q_match:
            candidate = int(q_match.group(1))
            is_top_level = (pending_q_num == 0 and 1 <= candidate <= 20) or (pending_q_num > 0 and candidate == pending_q_num + 1)
            if is_top_level:
                flush_question()
                pending_q_num = candidate
                pending_q_lines = [q_match.group(2).strip()]
                continue

        if pending_q_num > 0:
            pending_q_lines.append(stripped)

    flush_question()
    return items


def parse_answers(text: str, source_path: str) -> List[AnswerItem]:
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]

    current_exam = 1
    current_session = 1
    current_section = ""
    current_section_order = 0
    current_q = 0
    buffer: List[str] = []
    items: List[AnswerItem] = []

    def flush() -> None:
        nonlocal buffer, current_q
        if current_q <= 0 or not current_section:
            buffer = []
            return
        joined = "\n".join(buffer).strip()
        if joined:
            items.append(
                AnswerItem(
                    exam_id=current_exam,
                    session_id=current_session,
                    section_name=current_section,
                    section_order=current_section_order,
                    question_number=current_q,
                    answer_text=joined,
                    source=source_path,
                )
            )
        buffer = []

    for line in lines:
        stripped = line.strip()
        low = _normalize(stripped)

        exam_hdr = re.search(r"exam\s*(\d+)\s*session\s*(\d+)", low)
        if exam_hdr:
            flush()
            current_exam = int(exam_hdr.group(1))
            current_session = int(exam_hdr.group(2))
            current_section = ""
            current_section_order = 0
            current_q = 0
            continue

        if "exam" in low and "session" in low:
            maybe_exam = re.findall(r"\d+", low)
            if len(maybe_exam) >= 2:
                flush()
                current_exam = int(maybe_exam[0])
                current_session = int(maybe_exam[1])
                current_section = ""
                current_section_order = 0
                current_q = 0
                continue

        section = _parse_section_header(low)
        if section:
            flush()
            current_section, current_section_order = section
            current_q = 0
            continue

        q_match = re.match(r"^\s*(\d+)[\).]\s+(.+)", line)
        if q_match:
            candidate = int(q_match.group(1))
            is_top_level = (current_q == 0 and 1 <= candidate <= 20) or (current_q > 0 and candidate == current_q + 1)
            if is_top_level:
                flush()
                current_q = candidate
                buffer = [q_match.group(2).strip()]
                continue

        if current_q > 0:
            buffer.append(stripped)

    flush()
    return items


def extract_session_stems(text: str, source_path: str) -> Dict[str, str]:
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    stems: Dict[str, str] = {}

    current_exam = detect_exam_id_from_filename(source_path) or 1
    current_session = 1
    capture = False
    bucket: List[str] = []

    def flush() -> None:
        nonlocal bucket
        if not bucket:
            return
        key = f"exam{current_exam}_session{current_session}"
        joined = "\n".join(bucket).strip()
        if joined:
            stems[key] = joined
        bucket = []

    for line in lines:
        low = _normalize(line)

        exam_match = re.search(r"sample oral examination\s*(\d+)", low)
        if exam_match:
            flush()
            current_exam = int(exam_match.group(1))
            capture = False
            continue

        session_match = re.search(r"session\s*(\d+)\s*[-–]\s*\d+\s*minutes", low)
        if session_match:
            flush()
            current_session = int(session_match.group(1))
            capture = True
            bucket = [line.strip()]
            continue

        if _parse_section_header(low):
            flush()
            capture = False
            continue

        if capture:
            bucket.append(line.strip())

    flush()
    return stems


def _roman_to_int(token: str) -> int | None:
    mapping = {"i": 1, "v": 5, "x": 10}
    token = token.lower()
    if any(ch not in mapping for ch in token):
        return None
    total = 0
    prev = 0
    for ch in reversed(token):
        val = mapping[ch]
        if val < prev:
            total -= val
        else:
            total += val
        prev = val
    return total if total > 0 else None


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _parse_section_header(normalized_line: str) -> tuple[str, int] | None:
    for pattern, canonical, order in SECTION_PATTERNS:
        if re.search(pattern, normalized_line):
            return canonical, order
    return None
