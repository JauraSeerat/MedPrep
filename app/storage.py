from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import List

from .models import AnswerItem, KnowledgeBase, MappingItem

APP_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = APP_ROOT / "data"
KB_DIR = DATA_DIR / "knowledge_bases"
USER_FEEDBACK_DIR = DATA_DIR / "user_feedback"


for p in [DATA_DIR, KB_DIR, USER_FEEDBACK_DIR]:
    p.mkdir(parents=True, exist_ok=True)


def save_knowledge_base(kb: KnowledgeBase) -> Path:
    slug = _slugify(kb.name)
    target = KB_DIR / f"{slug}.json"
    payload = {
        "name": kb.name,
        "questions_pdf": kb.questions_pdf,
        "answers_pdf": kb.answers_pdf,
        "session_stems": kb.session_stems,
        "mappings": [asdict(m) for m in kb.mappings],
        "unmatched_answers": [asdict(a) for a in kb.unmatched_answers],
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def load_knowledge_base(path: str) -> KnowledgeBase:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    mappings = [MappingItem(**_normalize_mapping_record(m)) for m in raw.get("mappings", [])]
    unmatched_answers = [AnswerItem(**_normalize_answer_record(a)) for a in raw.get("unmatched_answers", [])]
    return KnowledgeBase(
        name=raw["name"],
        questions_pdf=raw["questions_pdf"],
        answers_pdf=raw["answers_pdf"],
        session_stems=raw.get("session_stems", {}),
        mappings=mappings,
        unmatched_answers=unmatched_answers,
    )


def list_kbs() -> List[Path]:
    return sorted(KB_DIR.glob("*.json"))


def append_ai_error_report(line: str) -> Path:
    target = USER_FEEDBACK_DIR / "ai_error_reports.log"
    with target.open("a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")
    return target


def _slugify(s: str) -> str:
    out = []
    for ch in s.lower().strip():
        if ch.isalnum():
            out.append(ch)
        elif ch in {" ", "-", "_"}:
            out.append("-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "knowledge-base"


def _normalize_mapping_record(m: dict) -> dict:
    out = dict(m)
    out.setdefault("section_name", "Unknown")
    out.setdefault("section_order", 0)
    out.setdefault("question_number", out.get("question_index", 0))
    out.setdefault("question_order", out.get("question_index", 0))
    out.pop("question_index", None)
    return out


def _normalize_answer_record(a: dict) -> dict:
    out = dict(a)
    out.setdefault("section_name", "Unknown")
    out.setdefault("section_order", 0)
    out.setdefault("question_number", out.get("question_index", 0))
    out.pop("question_index", None)
    return out
