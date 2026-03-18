# MedPrepAI (MVP)

Lightweight offline Python desktop MVP for medical oral interview practice.

## Implemented in this MVP
- Local PDF ingestion for Questions + Answers
- Exam/session extraction from both filename and PDF headings
- Auto Q/A mapping by exam/session/question index
- Unmatched answers shown in manual mapping queue for user correction
- Local knowledge base save/load (`data/knowledge_bases/*.json`)
- Interview run (text-answer MVP)
- Interview AV mode:
  - Webcam recording during interview (local file)
  - Microphone answer capture per question part
  - Local whisper transcription (`faster-whisper`)
  - Spoken question prompts (TTS)
- Semantic feedback based on reference-answer meaning
  - Uses `all-MiniLM-L6-v2` sentence embeddings when available
  - Falls back to lexical similarity if embedding model is unavailable
- Fully local logging for AI error reports (`data/user_feedback/ai_error_reports.log`)

## Not yet in this MVP
- Audio/video recording, whisper STT, eye tracking, PDF export

## Run
```bash
cd /Users/seeratjaura/Documents/New\ project/medprepai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

## Windows Build (for distribution)
- Use `build_windows.ps1` on a Windows machine.
- User guide: `USER_GUIDE_WINDOWS.md`

## Notes
- Exam ID is detected from both filename and in-document headings.
- Mapping uses `exam + session + section + question_number` to avoid collisions when numbering restarts per section.
- Questions are stored as full multi-line text blocks (not first-line trimmed).
- If answer content exists for exams without question PDFs, those answers remain inactive until matching questions are uploaded.
- Unmatched answer blocks are available in a manual review queue in the UI.
