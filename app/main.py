from __future__ import annotations

import io
import json
import time
from datetime import datetime
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .av import InterviewAV
from .mapping import apply_manual_assignment, build_mappings, find_duplicates, summarize_mapping
from .models import KnowledgeBase, MappingItem
from .parser import extract_session_stems, parse_answers, parse_questions
from .pdf_extract import extract_pdf_text, extract_pdf_text_candidates
from .semantic_feedback import SemanticEvaluator
from .storage import append_ai_error_report, list_kbs, load_knowledge_base, save_knowledge_base


class MedPrepApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MedPrepAI")
        self.geometry("1260x860")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.current_kb: KnowledgeBase | None = None
        self.mappings: list[MappingItem] = []
        self.unmatched = []
        self.duplicate_report: dict = {}
        self.last_parse_counts: dict = {}

        self.interview_kb: KnowledgeBase | None = None
        self.interview_questions: list[MappingItem] = []
        self.interview_idx = 0
        self.interview_parts: list[str] = []
        self.interview_part_idx = 0
        self.student_answers: dict[str, str] = {}
        self.case_panel_open = False
        self.av = InterviewAV(Path(__file__).resolve().parents[1] / "data" / "interviews")
        self.attempt_paths = None
        self.current_audio_path: Path | None = None
        self.mic_recording = False
        self.camera_job = None

        # Section timing
        self.section_start_time: float | None = None
        self.current_section_name: str = ""
        self.timer_job = None

        use_embeddings = None
        if sys.platform == "darwin":
            use_embeddings = False
            print("MedPrepAI: embeddings disabled on macOS (set MEDPREPAI_ENABLE_EMBEDDINGS=1 to opt in).")
        self.evaluator = SemanticEvaluator(use_embeddings=use_embeddings)

        self._build_ui()
        self._refresh_kb_choices()

    # ── UI BUILD ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True)

        self.tab_kb = ttk.Frame(tabs)
        self.tab_interview = ttk.Frame(tabs)
        tabs.add(self.tab_kb, text="  Knowledge Base  ")
        tabs.add(self.tab_interview, text="  Interview  ")

        self._build_kb_tab()
        self._build_interview_tab()

    def _build_kb_tab(self):
        # Header with guided upload button
        header = ttk.Frame(self.tab_kb)
        header.pack(fill="x", padx=12, pady=10)

        ttk.Label(header, text="Knowledge Base Setup", font=("", 13, "bold")).pack(side="left")
        ttk.Button(header, text="+ Guided Upload", command=self._guided_upload_modal).pack(side="right")

        top = ttk.LabelFrame(self.tab_kb, text="Manual Upload")
        top.pack(fill="x", padx=12, pady=(0, 6))

        self.q_pdf_var = tk.StringVar()
        self.a_pdf_var = tk.StringVar()
        self.kb_name_var = tk.StringVar(value="Mock Oral Exam")

        ttk.Label(top, text="Questions PDF").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(top, textvariable=self.q_pdf_var, width=75).grid(row=0, column=1, padx=6)
        ttk.Button(top, text="Browse", command=self._pick_q_pdf).grid(row=0, column=2, padx=4)

        ttk.Label(top, text="Answers PDF").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(top, textvariable=self.a_pdf_var, width=75).grid(row=1, column=1, padx=6)
        ttk.Button(top, text="Browse", command=self._pick_a_pdf).grid(row=1, column=2, padx=4)

        ttk.Label(top, text="KB Name").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(top, textvariable=self.kb_name_var, width=40).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Button(top, text="Parse + Auto Map", command=self._parse_and_map).grid(row=2, column=2, padx=4)

        middle = ttk.Frame(self.tab_kb)
        middle.pack(fill="both", expand=True, padx=12, pady=4)

        left = ttk.Frame(middle)
        left.pack(side="left", fill="both", expand=True)

        self.summary_text = tk.Text(left, height=8, wrap="word")
        self.summary_text.pack(fill="x")

        columns = ("exam", "session", "section", "q_num", "matched", "reason", "question")
        self.mapping_table = ttk.Treeview(left, columns=columns, show="headings", height=18)
        for col in columns:
            self.mapping_table.heading(col, text=col)
        self.mapping_table.column("exam", width=60)
        self.mapping_table.column("session", width=60)
        self.mapping_table.column("section", width=220)
        self.mapping_table.column("q_num", width=70)
        self.mapping_table.column("matched", width=70)
        self.mapping_table.column("reason", width=230)
        self.mapping_table.column("question", width=580)
        self.mapping_table.pack(fill="both", expand=True, pady=6)
        self.mapping_table.bind("<<TreeviewSelect>>", self._on_mapping_selected)

        self.mapping_detail = tk.Text(left, height=9, wrap="word")
        self.mapping_detail.pack(fill="x", pady=(4, 0))

        right = ttk.LabelFrame(middle, text="Manual Mapping Queue (Unmatched Answers)")
        right.pack(side="right", fill="both", expand=False, padx=(8, 0))

        self.unmatched_list = tk.Listbox(right, width=70, height=18)
        self.unmatched_list.pack(padx=8, pady=6)

        form = ttk.Frame(right)
        form.pack(fill="x", padx=8)
        self.target_exam = tk.StringVar(value="1")
        self.target_session = tk.StringVar(value="1")
        self.target_section = tk.StringVar(value="Intraoperative Management")
        self.target_qnum = tk.StringVar(value="1")

        ttk.Label(form, text="Target Exam").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.target_exam, width=8).grid(row=0, column=1, padx=4)
        ttk.Label(form, text="Session").grid(row=0, column=2, sticky="w")
        ttk.Entry(form, textvariable=self.target_session, width=8).grid(row=0, column=3, padx=4)
        ttk.Label(form, text="Section").grid(row=0, column=4, sticky="w")
        ttk.Entry(form, textvariable=self.target_section, width=24).grid(row=0, column=5, padx=4)
        ttk.Label(form, text="Question #").grid(row=0, column=6, sticky="w")
        ttk.Entry(form, textvariable=self.target_qnum, width=8).grid(row=0, column=7, padx=4)

        ttk.Button(right, text="Apply Manual Mapping", command=self._apply_manual_mapping).pack(padx=8, pady=6, fill="x")

        bottom = ttk.Frame(self.tab_kb)
        bottom.pack(fill="x", padx=12, pady=8)
        ttk.Button(bottom, text="Save Knowledge Base", command=self._save_kb).pack(side="left")
        self.kb_status_label = ttk.Label(bottom, text="", foreground="green")
        self.kb_status_label.pack(side="left", padx=12)

    def _build_interview_tab(self):
        top = ttk.Frame(self.tab_interview)
        top.pack(fill="x", padx=12, pady=10)

        self.kb_choice_var = tk.StringVar()
        ttk.Label(top, text="Knowledge Base:").pack(side="left")
        self.kb_dropdown = ttk.Combobox(top, textvariable=self.kb_choice_var, width=50, state="readonly")
        self.kb_dropdown.pack(side="left", padx=6)
        ttk.Button(top, text="Load", command=self._load_selected_kb).pack(side="left", padx=4)
        ttk.Button(top, text="Start Interview", command=self._start_interview).pack(side="left", padx=4)
        self.case_toggle_btn = ttk.Button(top, text="Case ▶", command=self._toggle_case_panel)
        self.case_toggle_btn.pack(side="left", padx=8)

        # Section timer display
        self.timer_label = ttk.Label(top, text="", font=("", 11))
        self.timer_label.pack(side="right", padx=12)

        main = ttk.Frame(self.tab_interview)
        main.pack(fill="both", expand=True, padx=12)

        self.case_frame = ttk.LabelFrame(main, text="Case Description")
        self.case_text = tk.Text(self.case_frame, height=12, wrap="word")
        self.case_text.pack(fill="both", expand=True)
        self.case_text.config(state="disabled")

        self.camera_frame = ttk.LabelFrame(main, text="Interview Camera")
        self.camera_label = ttk.Label(self.camera_frame, text="Camera feed unavailable")
        self.camera_label.pack(fill="both", expand=True)
        self.camera_frame.pack(fill="x", pady=(0, 6))

        self.question_label = ttk.Label(main, text="Question will appear here", wraplength=1180, font=("", 11))
        self.question_label.pack(anchor="w", pady=(0, 4))

        # Progress bar
        progress_frame = ttk.Frame(main)
        progress_frame.pack(fill="x", pady=(0, 4))
        self.progress_bar = ttk.Progressbar(progress_frame, mode="determinate", length=800)
        self.progress_bar.pack(side="left", fill="x", expand=True)
        self.progress_label = ttk.Label(progress_frame, text="0/0", width=10)
        self.progress_label.pack(side="left", padx=8)

        answer_header = ttk.Frame(main)
        answer_header.pack(fill="x")
        ttk.Label(answer_header, text="Your Answer:").pack(side="left")
        self.char_count_label = ttk.Label(answer_header, text="0 chars", foreground="gray")
        self.char_count_label.pack(side="right")

        self.answer_box = tk.Text(main, height=8, wrap="word")
        self.answer_box.pack(fill="x", pady=4)
        self.answer_box.bind("<KeyRelease>", self._update_char_count)
        self.mic_status_label = ttk.Label(main, text="Mic: idle", foreground="gray")
        self.mic_status_label.pack(anchor="w")

        nav = ttk.Frame(main)
        nav.pack(fill="x", pady=6)
        ttk.Button(nav, text="Start Mic", command=self._start_mic_capture).pack(side="right")
        ttk.Button(nav, text="Stop Mic + Transcribe", command=self._stop_mic_capture).pack(side="right", padx=6)
        self.next_btn = ttk.Button(nav, text="Next Question ▶", command=self._next_question)
        self.next_btn.pack(side="right")
        ttk.Button(nav, text="Finish + Generate Feedback", command=self._finish_interview).pack(side="right", padx=6)

        # Feedback area
        self.feedback_frame = ttk.LabelFrame(main, text="Interview Feedback")
        feedback_scroll = ttk.Scrollbar(self.feedback_frame)
        feedback_scroll.pack(side="right", fill="y")
        self.feedback_text = tk.Text(self.feedback_frame, height=24, wrap="word", yscrollcommand=feedback_scroll.set)
        self.feedback_text.pack(fill="both", expand=True)
        feedback_scroll.config(command=self.feedback_text.yview)

        # Configure feedback text tags for color coding
        self.feedback_text.tag_configure("header", font=("", 12, "bold"))
        self.feedback_text.tag_configure("question_tag", font=("", 10, "bold"), foreground="#2255AA")
        self.feedback_text.tag_configure("ref_label", font=("", 10, "bold"), foreground="#228833")
        self.feedback_text.tag_configure("ref_text", foreground="#226622")
        self.feedback_text.tag_configure("missing_label", font=("", 10, "bold"), foreground="#AA2222")
        self.feedback_text.tag_configure("missing_item", foreground="#882222")
        self.feedback_text.tag_configure("ok_label", font=("", 10, "bold"), foreground="#228833")
        self.feedback_text.tag_configure("gaze_label", font=("", 10, "bold"), foreground="#885500")
        self.feedback_text.tag_configure("divider", foreground="#AAAAAA")

        report_btns = ttk.Frame(main)
        report_btns.pack(fill="x", pady=6)
        ttk.Button(report_btns, text="Report AI Error", command=self._report_ai_error).pack(side="left")
        self.restart_btn = ttk.Button(report_btns, text="↺ Restart Interview", command=self._restart_interview)
        self.restart_btn.pack(side="left", padx=8)
        self.restart_btn.pack_forget()

        self._interview_main = main

    # ── GUIDED UPLOAD MODAL ───────────────────────────────────────────────────

    def _guided_upload_modal(self):
        win = tk.Toplevel(self)
        win.title("Upload Knowledge Base — Step by Step")
        win.geometry("700x480")
        win.transient(self)
        win.grab_set()

        ttk.Label(win, text="Upload Your Question & Answer PDFs", font=("", 13, "bold")).pack(pady=(16, 4))
        ttk.Label(
            win,
            text="You need two separate PDF files:\n"
                 "  • Questions PDF  — contains the exam questions\n"
                 "  • Answers PDF    — contains the reference answers\n\n"
                 "Both files must follow the same exam/session structure so questions\n"
                 "can be matched to their answers automatically.",
            justify="left",
            wraplength=640,
        ).pack(padx=24, anchor="w")

        ttk.Separator(win, orient="horizontal").pack(fill="x", padx=24, pady=12)

        # Step 1
        step1 = ttk.LabelFrame(win, text="Step 1 — Questions PDF")
        step1.pack(fill="x", padx=24, pady=4)
        guided_q_var = tk.StringVar(value=self.q_pdf_var.get())
        ttk.Entry(step1, textvariable=guided_q_var, width=60).pack(side="left", padx=8, pady=8)
        ttk.Button(step1, text="Browse", command=lambda: self._browse_into(guided_q_var)).pack(side="left")
        ttk.Label(step1, text="e.g. mock_oral_exam_questions.pdf", foreground="gray", font=("", 9)).pack(side="left", padx=8)

        # Step 2
        step2 = ttk.LabelFrame(win, text="Step 2 — Answers PDF")
        step2.pack(fill="x", padx=24, pady=4)
        guided_a_var = tk.StringVar(value=self.a_pdf_var.get())
        ttk.Entry(step2, textvariable=guided_a_var, width=60).pack(side="left", padx=8, pady=8)
        ttk.Button(step2, text="Browse", command=lambda: self._browse_into(guided_a_var)).pack(side="left")
        ttk.Label(step2, text="e.g. mock_oral_exam_answers.pdf", foreground="gray", font=("", 9)).pack(side="left", padx=8)

        # Step 3
        step3 = ttk.LabelFrame(win, text="Step 3 — Give your Knowledge Base a name")
        step3.pack(fill="x", padx=24, pady=4)
        guided_name_var = tk.StringVar(value=self.kb_name_var.get())
        ttk.Entry(step3, textvariable=guided_name_var, width=40).pack(side="left", padx=8, pady=8)
        ttk.Label(step3, text="e.g. FRCPC Mock Oral Exam 2025", foreground="gray", font=("", 9)).pack(side="left", padx=8)

        status_label = ttk.Label(win, text="", foreground="red")
        status_label.pack(pady=4)

        def _confirm():
            q = guided_q_var.get().strip()
            a = guided_a_var.get().strip()
            name = guided_name_var.get().strip()
            if not q:
                status_label.config(text="Please select the Questions PDF.")
                return
            if not a:
                status_label.config(text="Please select the Answers PDF.")
                return
            if not name:
                status_label.config(text="Please enter a name for this knowledge base.")
                return
            self.q_pdf_var.set(q)
            self.a_pdf_var.set(a)
            self.kb_name_var.set(name)
            win.destroy()
            # status_label.config(text="")
            self._parse_and_map()

        ttk.Button(win, text="Parse + Build Knowledge Base", command=_confirm).pack(pady=12)

    def _browse_into(self, var: tk.StringVar):
        path = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if path:
            var.set(path)

    # ── KB ACTIONS ────────────────────────────────────────────────────────────
    def _page_range_modal(self, q_pdf: str, a_pdf: str) -> tuple[tuple[int,int], tuple[int,int]] | None:
        """Show a dialog asking user for page ranges. Returns ((q_start, q_end), (a_start, a_end)) or None if cancelled."""
        from .pdf_extract import get_pdf_page_count

        q_pages = get_pdf_page_count(q_pdf)
        a_pages = get_pdf_page_count(a_pdf)

        win = tk.Toplevel(self)
        win.title("Specify Page Ranges")
        win.geometry("560x340")
        win.transient(self)
        win.grab_set()

        result = {"value": None}

        ttk.Label(win, text="Specify Page Ranges", font=("", 13, "bold")).pack(pady=(16, 4))
        ttk.Label(
            win,
            text="Enter the page ranges that contain the questions and answers.\n"
                "These ranges will be used exclusively for parsing.",
            justify="left", foreground="gray", wraplength=500,
        ).pack(padx=24, anchor="w", pady=(0, 12))

        form = ttk.Frame(win)
        form.pack(padx=24, fill="x")

        # Questions page range
        ttk.Label(form, text=f"Questions PDF  ({q_pages} pages total)", font=("", 10, "bold")).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
        ttk.Label(form, text="From page:").grid(row=1, column=0, sticky="w")
        q_start_var = tk.StringVar(value="1")
        ttk.Entry(form, textvariable=q_start_var, width=8).grid(row=1, column=1, padx=8)
        ttk.Label(form, text="To page:").grid(row=1, column=2, sticky="w")
        q_end_var = tk.StringVar(value=str(q_pages or 999))
        ttk.Entry(form, textvariable=q_end_var, width=8).grid(row=1, column=3, padx=8)

        ttk.Separator(form, orient="horizontal").grid(row=2, column=0, columnspan=4, sticky="ew", pady=12)

        # Answers page range
        ttk.Label(form, text=f"Answers PDF  ({a_pages} pages total)", font=("", 10, "bold")).grid(row=3, column=0, columnspan=4, sticky="w", pady=(0, 4))
        ttk.Label(form, text="From page:").grid(row=4, column=0, sticky="w")
        a_start_var = tk.StringVar(value="1")
        ttk.Entry(form, textvariable=a_start_var, width=8).grid(row=4, column=1, padx=8)
        ttk.Label(form, text="To page:").grid(row=4, column=2, sticky="w")
        a_end_var = tk.StringVar(value=str(a_pages or 999))
        ttk.Entry(form, textvariable=a_end_var, width=8).grid(row=4, column=3, padx=8)

        status = ttk.Label(win, text="", foreground="red")
        status.pack(pady=8)

        def _confirm():
            try:
                qs = int(q_start_var.get())
                qe = int(q_end_var.get())
                as_ = int(a_start_var.get())
                ae = int(a_end_var.get())
            except ValueError:
                status.config(text="Page numbers must be whole numbers.")
                return
            if qs < 1 or qe < qs:
                status.config(text="Questions: 'From' must be at least 1 and less than 'To'.")
                return
            if q_pages and qs > q_pages:
                status.config(text=f"Questions: 'From' cannot exceed {q_pages}.")
                return
            if q_pages and qe > q_pages:
                status.config(text=f"Questions: 'To' cannot exceed {q_pages}.")
                return
            if as_ < 1 or ae < as_:
                status.config(text="Answers: 'From' must be at least 1 and less than 'To'.")
                return
            if a_pages and as_ > a_pages:
                status.config(text=f"Answers: 'From' cannot exceed {a_pages}.")
                return
            if a_pages and ae > a_pages:
                status.config(text=f"Answers: 'To' cannot exceed {a_pages}.")
                return
            result["value"] = ((qs, qe), (as_, ae))
            win.destroy()

        btn_frame = ttk.Frame(win)
        btn_frame.pack(pady=4)
        ttk.Button(btn_frame, text="Apply Page Ranges", command=_confirm).pack(side="left", padx=8)

        self.wait_window(win)
        return result["value"]
    
    def _pick_q_pdf(self):
        path = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if path:
            self.q_pdf_var.set(path)

    def _pick_a_pdf(self):
        path = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if path:
            self.a_pdf_var.set(path)

    def _parse_and_map(self):
        q_pdf = self.q_pdf_var.get().strip()
        a_pdf = self.a_pdf_var.get().strip()
        if not q_pdf or not a_pdf:
            messagebox.showerror("Missing files", "Please select both a Questions PDF and an Answers PDF before parsing.")
            return

        # Ask user for page ranges
        ranges = self._page_range_modal(q_pdf, a_pdf)
        if ranges is None:
            return  # user closed the window
        (q_start, q_end), (a_start, a_end) = ranges

        self.kb_status_label.config(text="Parsing PDFs…", foreground="blue")
        self.update_idletasks()

        try:
            from .pdf_extract import extract_pdf_text_by_pages

            # Use page-range extraction exclusively
            q_text = extract_pdf_text_by_pages(q_pdf, q_start, q_end)
            a_text = extract_pdf_text_by_pages(a_pdf, a_start, a_end)
            q_engine = f"page_range_{q_start}-{q_end}"
            a_engine = f"page_range_{a_start}-{a_end}"

            if not q_text.strip():
                messagebox.showerror(
                    "No text extracted",
                    f"No text was extracted from the Questions PDF pages {q_start}-{q_end}.\n\n"
                    "Please check:\n"
                    "• The page numbers are correct\n"
                    "• The PDF pages contain selectable text (not just scanned images)"
                )
                self.kb_status_label.config(text="Parse failed.", foreground="red")
                return

            if not a_text.strip():
                messagebox.showerror(
                    "No text extracted",
                    f"No text was extracted from the Answers PDF pages {a_start}-{a_end}.\n\n"
                    "Please check:\n"
                    "• The page numbers are correct\n"
                    "• The PDF pages contain selectable text (not just scanned images)"
                )
                self.kb_status_label.config(text="Parse failed.", foreground="red")
                return

            questions = parse_questions(q_text, q_pdf)
            answers = parse_answers(a_text, a_pdf)

            # ... rest of the method stays exactly the same from here
    

            if not questions:
                messagebox.showerror(
                    "No questions found",
                    "Could not extract any questions from the Questions PDF.\n\n"
                    "Please check:\n"
                    "• You selected the correct file\n"
                    "• The PDF contains numbered questions (1. or 1))\n"
                    "• The sections are labeled (e.g. Preoperative Evaluation)"
                )
                self.kb_status_label.config(text="Parse failed.", foreground="red")
                return

            if not answers:
                messagebox.showerror(
                    "No answers found",
                    "Could not extract any answers from the Answers PDF.\n\n"
                    "Please check:\n"
                    "• You selected the correct file\n"
                    "• The PDF contains numbered answers matching the questions"
                )
                self.kb_status_label.config(text="Parse failed.", foreground="red")
                return

            session_stems = extract_session_stems(q_text, q_pdf)
            mappings, unmatched = build_mappings(questions, answers)
            self.duplicate_report = find_duplicates(questions, answers)
            self.last_parse_counts = {
                "parsed_question_items": len(questions),
                "parsed_answer_items": len(answers),
                "parsed_session_stems": len(session_stems),
                "question_extractor_engine": q_engine,
                "answer_extractor_engine": a_engine,
                "questions_page_range": f"{q_start}-{q_end}",
                "answers_page_range": f"{a_start}-{a_end}",
            }

            matched_count = sum(1 for m in mappings if m.answer_text)
            self.mappings = mappings
            self.unmatched = unmatched

            self.current_kb = KnowledgeBase(
                name=self.kb_name_var.get().strip() or "Untitled KB",
                questions_pdf=q_pdf,
                answers_pdf=a_pdf,
                session_stems=session_stems,
                mappings=mappings,
                unmatched_answers=unmatched,
            )
            self.interview_kb = self.current_kb
            self._render_mapping_state()

            if matched_count == 0 and questions and answers:
                messagebox.showwarning(
                    "No matches found",
                    "Questions and answers were parsed, but none were matched.\n\n"
                    "This usually means the exam/session/section headers don't align between\n"
                    "the two PDFs (e.g., 'Sample 3' vs 'Exam 3 Session 2').\n"
                    "Double-check the page ranges and header formats."
                )

            self.kb_status_label.config(
                text=f"Parsed: {len(questions)} questions, {matched_count} matched. Ready to save.",
                foreground="green"
            )

            if self.duplicate_report.get("duplicate_question_key_count", 0) > 0 or self.duplicate_report.get("duplicate_answer_key_count", 0) > 0:
                messagebox.showwarning("Duplicates detected", "Duplicate mapping keys were detected.\nReview the summary panel before saving.")

        except Exception as e:
            messagebox.showerror("Parse error", f"An error occurred while parsing:\n\n{e}\n\nPlease check that both PDFs are valid and not password-protected.")
            self.kb_status_label.config(text="Parse failed.", foreground="red")

    def _select_best_question_text(self, pdf_path: str) -> tuple[str, str]:
        candidates = extract_pdf_text_candidates(pdf_path)
        scored = []
        for engine, text in candidates.items():
            q_count = len(parse_questions(text or "", pdf_path))
            scored.append((q_count, len((text or "").strip()), engine, text or ""))
        scored.sort(reverse=True)
        best = scored[0]
        return best[3], best[2]

    def _select_best_answer_text(self, pdf_path: str) -> tuple[str, str]:
        candidates = extract_pdf_text_candidates(pdf_path)
        scored = []
        for engine, text in candidates.items():
            a_count = len(parse_answers(text or "", pdf_path))
            scored.append((a_count, len((text or "").strip()), engine, text or ""))
        scored.sort(reverse=True)
        best = scored[0]
        return best[3], best[2]

    def _render_mapping_state(self):
        for i in self.mapping_table.get_children():
            self.mapping_table.delete(i)
        for idx, m in enumerate(self.mappings):
            self.mapping_table.insert(
                "", "end", iid=str(idx),
                values=(m.exam_id, m.session_id, m.section_name, m.question_number,
                        "yes" if m.matched and m.answer_text else "no", m.match_reason, m.question_text),
            )
        self.mapping_detail.delete("1.0", tk.END)
        self.unmatched_list.delete(0, tk.END)
        for idx, a in enumerate(self.unmatched):
            snippet = a.answer_text.replace("\n", " ")[:100]
            self.unmatched_list.insert(tk.END, f"#{idx+1} Exam {a.exam_id}/S{a.session_id}/{a.section_name}/Q{a.question_number}: {snippet}")

        summary = summarize_mapping(self.mappings, self.unmatched)
        summary["parser_counts"] = self.last_parse_counts
        summary["duplicates"] = self.duplicate_report
        self.summary_text.delete("1.0", tk.END)
        self.summary_text.insert(tk.END, json.dumps(summary, indent=2))

        if self.current_kb and self.current_kb.session_stems:
            self.summary_text.insert(tk.END, "\n\nSession stems extracted:\n")
            for key in sorted(self.current_kb.session_stems):
                self.summary_text.insert(tk.END, f"- {key}\n")

    def _apply_manual_mapping(self):
        sel = self.unmatched_list.curselection()
        if not sel:
            messagebox.showinfo("Select item", "Choose an unmatched answer from the list first.")
            return
        try:
            e = int(self.target_exam.get())
            s = int(self.target_session.get())
            section = self.target_section.get().strip()
            q = int(self.target_qnum.get())
        except ValueError:
            messagebox.showerror("Invalid target", "Exam, Session, and Question # must all be numbers.")
            return
        self.mappings, self.unmatched, msg = apply_manual_assignment(
            self.mappings, self.unmatched,
            answer_idx=sel[0], target_exam=e, target_session=s,
            target_section=section, target_question_number=q,
        )
        if self.current_kb:
            self.current_kb.mappings = self.mappings
            self.current_kb.unmatched_answers = self.unmatched
        self._render_mapping_state()
        messagebox.showinfo("Manual mapping", msg)

    def _on_mapping_selected(self, _event=None):
        sel = self.mapping_table.selection()
        if not sel:
            return
        try:
            idx = int(sel[0])
        except ValueError:
            return
        if idx < 0 or idx >= len(self.mappings):
            return
        m = self.mappings[idx]
        self.mapping_detail.delete("1.0", tk.END)
        self.mapping_detail.insert("1.0", (
            f"Exam {m.exam_id} / Session {m.session_id} / {m.section_name} / Q{m.question_number}\n"
            f"Matched: {'yes' if m.answer_text else 'no'} ({m.match_reason})\n\n"
            f"Question:\n{m.question_text}\n\nAnswer:\n{m.answer_text or '[No mapped answer]'}"
        ))

    def _save_kb(self):
        if not self.current_kb:
            messagebox.showerror("Nothing to save", "Please parse PDFs first before saving.")
            return
        self.current_kb.mappings = self.mappings
        self.current_kb.unmatched_answers = self.unmatched
        out = save_knowledge_base(self.current_kb)
        self.interview_kb = self.current_kb
        self._refresh_kb_choices()
        self.kb_choice_var.set(str(out))
        self.kb_status_label.config(text=f"Saved: {out.name}", foreground="green")
        messagebox.showinfo("Saved", f"Knowledge base saved:\n{out}")

    def _refresh_kb_choices(self):
        files = list_kbs()
        vals = [str(p) for p in files]
        self.kb_dropdown["values"] = vals
        if vals and not self.kb_choice_var.get():
            self.kb_choice_var.set(vals[0])

    def _load_selected_kb(self):
        path = self.kb_choice_var.get().strip()
        if not path:
            messagebox.showerror("Nothing selected", "Please select a knowledge base from the dropdown.")
            return
        try:
            self.interview_kb = load_knowledge_base(path)
            self._rebuild_kb_if_stale(self.interview_kb)
            self._ensure_session_stems(self.interview_kb)
            matched = sum(1 for m in self.interview_kb.mappings if m.answer_text)
            messagebox.showinfo("Loaded", f"Knowledge base loaded:\n{self.interview_kb.name}\n{matched} matched Q&A pairs ready.")
        except Exception as e:
            messagebox.showerror("Load error", f"Could not load knowledge base:\n{e}")

    # ── INTERVIEW ─────────────────────────────────────────────────────────────

    def _start_interview(self):
        if not self.interview_kb:
            messagebox.showerror("No knowledge base", "Please load a knowledge base before starting the interview.")
            return
        self._rebuild_kb_if_stale(self.interview_kb)
        self._ensure_session_stems(self.interview_kb)

        active = [m for m in self.interview_kb.mappings if m.answer_text]
        active.sort(key=lambda m: (m.exam_id, m.session_id, m.section_order, m.question_number))

        if not active:
            messagebox.showerror(
                "No questions available",
                "No matched Q&A pairs found in this knowledge base.\n\n"
                "Please check that your PDFs were parsed correctly in the Knowledge Base tab."
            )
            return

        self.interview_questions = active
        self.interview_idx = 0
        self.interview_parts = []
        self.interview_part_idx = 0
        self.student_answers = {}
        self.section_start_time = time.time()
        self.current_section_name = active[0].section_name if active else ""

        self._stop_camera_preview()
        self._stop_timer()
        self.av.stop_attempt()
        self.attempt_paths = self.av.start_attempt(start_video=False)
        self.current_audio_path = None

        if self.feedback_frame.winfo_ismapped():
            self.feedback_frame.pack_forget()
        try:
            self.restart_btn.pack_forget()
        except Exception:
            pass

        first = self.interview_questions[0]
        self._show_note_taking_modal(first.exam_id, first.session_id)
        self._set_case_text_for(first.exam_id, first.session_id)
        self._start_timer()
        self._render_current_question()

    def _restart_interview(self):
        if messagebox.askyesno("Restart Interview", "Start a new interview session with the same knowledge base?"):
            self._start_interview()

    def _show_note_taking_modal(self, exam_id: int, session_id: int):
        if not self.interview_kb:
            return
        key = self._session_key(exam_id, session_id)
        stem = self.interview_kb.session_stems.get(key, "No case description found for this session.")

        win = tk.Toplevel(self)
        win.title(f"Session {session_id} — Case Description")
        win.geometry("900x620")
        win.transient(self)
        win.grab_set()

        ttk.Label(win, text=f"Session {session_id} Case Description", font=("", 13, "bold")).pack(anchor="w", padx=12, pady=(12, 2))
        ttk.Label(
            win,
            text="Read the case carefully and take notes. The interview will begin when you click 'I'm Ready'.",
            wraplength=860, foreground="gray",
        ).pack(anchor="w", padx=12, pady=(0, 8))

        text = tk.Text(win, wrap="word", font=("", 11))
        text.pack(fill="both", expand=True, padx=12, pady=8)
        text.insert("1.0", stem)
        text.config(state="disabled")

        ttk.Button(win, text="I'm Ready — Begin Interview", command=win.destroy).pack(pady=12)
        self.wait_window(win)

    def _toggle_case_panel(self):
        self.case_panel_open = not self.case_panel_open
        if self.case_panel_open:
            self.case_frame.pack(fill="x", pady=(0, 8))
            self.case_toggle_btn.config(text="◀ Case")
        else:
            self.case_frame.pack_forget()
            self.case_toggle_btn.config(text="Case ▶")

    def _set_case_text_for(self, exam_id: int, session_id: int):
        if not self.interview_kb:
            return
        self._ensure_case_widgets()
        key = self._session_key(exam_id, session_id)
        stem = self.interview_kb.session_stems.get(key, "No case description found for this session.")
        if not self.case_text.winfo_exists():
            return
        self.case_text.config(state="normal")
        self.case_text.delete("1.0", tk.END)
        self.case_text.insert("1.0", stem)
        self.case_text.config(state="disabled")

    def _render_current_question(self):
        total = len(self.interview_questions)
        if self.interview_idx >= total:
            self.question_label.config(text="All questions answered. Click 'Finish + Generate Feedback' to see your results.")
            self.progress_label.config(text=f"{total}/{total}")
            self.progress_bar["value"] = 100
            self.answer_box.delete("1.0", tk.END)
            return

        current = self.interview_questions[self.interview_idx]

        # Reset section timer when section changes
        if current.section_name != self.current_section_name:
            self.current_section_name = current.section_name
            self.section_start_time = time.time()

        self._set_case_text_for(current.exam_id, current.session_id)
        self.interview_parts = self._split_question_into_parts(current.question_text)
        if not self.interview_parts:
            self.interview_parts = [current.question_text]
        if self.interview_part_idx >= len(self.interview_parts):
            self.interview_part_idx = 0

        self.question_label.config(
            text=(
                f"Exam {current.exam_id}  •  Session {current.session_id}  •  {current.section_name}  •  Q{current.question_number}\n"
                f"Part {self.interview_part_idx + 1}/{len(self.interview_parts)}: "
                f"{self.interview_parts[self.interview_part_idx]}"
            )
        )
        self.av.speak(
            self.interview_parts[self.interview_part_idx],
            on_done=lambda: self.after(0, self._auto_start_mic),
        )
        self._start_camera_preview()

        # Update progress bar
        pct = int((self.interview_idx / total) * 100)
        self.progress_bar["value"] = pct
        self.progress_label.config(text=f"Q {self.interview_idx + 1}/{total}  •  Part {self.interview_part_idx + 1}/{len(self.interview_parts)}")

        prior = self.student_answers.get(self._part_key(current, self.interview_part_idx), "")
        self.answer_box.delete("1.0", tk.END)
        self.answer_box.insert("1.0", prior)
        self._update_char_count()
        self.mic_status_label.config(text="Mic: idle", foreground="gray")

    def _next_question(self):
        if not self.interview_questions:
            return
        current = self.interview_questions[self.interview_idx]
        key = self._part_key(current, self.interview_part_idx)
        self.student_answers[key] = self.answer_box.get("1.0", tk.END).strip()

        if self.interview_part_idx + 1 < len(self.interview_parts):
            self.interview_part_idx += 1
        else:
            self.interview_part_idx = 0
            if self.interview_idx < len(self.interview_questions):
                self.interview_idx += 1
        self._render_current_question()

    def _finish_interview(self):
        print("DEBUG 1: _finish_interview called")
        if not self.interview_questions:
            return

        print("DEBUG 2: saving current answer")
        if self.interview_idx < len(self.interview_questions):
            current = self.interview_questions[self.interview_idx]
            self.student_answers[self._part_key(current, self.interview_part_idx)] = (
                self.answer_box.get("1.0", tk.END).strip()
            )

        print("DEBUG 3: stopping mic if recording")
        if self.mic_recording:
            try:
                self.av.stop_mic_only()
            except Exception as e:
                print(f"DEBUG mic stop error: {e}")
            self.mic_recording = False

        print("DEBUG 4: stopping camera + timer")
        gaze = self.av.get_gaze_status()
        self._stop_camera_preview()
        self._stop_timer()

        print("DEBUG 5: stopping av attempt")
        self.av.stop_attempt()

        print("DEBUG 6: starting evaluation loop")
        all_feedback = []
        for i, q in enumerate(self.interview_questions):
            print(f"DEBUG 6.{i}: evaluating Q{q.question_number}")
            student = self._combined_answer_for_question(q)
            fb = self.evaluator.evaluate(q, student)
            all_feedback.append((q, fb, student))

        print("DEBUG 7: building rich feedback report")
        self._render_feedback(all_feedback, gaze)

        print("DEBUG 8: hiding camera frame")
        try:
            if self.camera_frame.winfo_ismapped():
                self.camera_frame.pack_forget()
        except Exception as e:
            print(f"DEBUG camera frame error: {e}")

        print("DEBUG 9: showing feedback + restart button")
        if not self.feedback_frame.winfo_ismapped():
            self.feedback_frame.pack(fill="both", expand=True)
        self.restart_btn.pack(side="left", padx=8)

        print("DEBUG 10: done")
        _dlg = tk.Toplevel(self)
        _dlg.title("Interview Complete")
        _dlg.geometry("400x140")
        _dlg.transient(self)
        _dlg.grab_set()
        ttk.Label(_dlg, text="Your feedback report is ready!", font=("", 12, "bold")).pack(pady=(20, 4))
        ttk.Label(_dlg, text="Scroll down in the Feedback panel to review.", foreground="gray").pack()
        ttk.Button(_dlg, text="View Feedback", command=_dlg.destroy).pack(pady=12)
        self.wait_window(_dlg)

    def _render_feedback(self, all_feedback, gaze):
        self.feedback_text.config(state="normal")
        self.feedback_text.delete("1.0", tk.END)

        self.feedback_text.insert(tk.END, "INTERVIEW FEEDBACK REPORT\n", "header")
        self.feedback_text.insert(tk.END, f"Generated: {datetime.now().strftime('%B %d, %Y  %H:%M')}\n\n")

        # Gaze summary
        self.feedback_text.insert(tk.END, "EYE CONTACT SUMMARY\n", "gaze_label")
        if gaze:
            self.feedback_text.insert(tk.END,
                f"  Looked away {gaze['look_away_count']} time(s), "
                f"total {round(gaze['total_look_away_sec'])}s away from camera.\n"
            )
            if gaze['look_away_count'] > 0:
                self.feedback_text.insert(tk.END, "  Tip: Maintain eye contact to appear more confident in real interviews.\n")
        else:
            self.feedback_text.insert(tk.END, "  No gaze data available (camera may have been off).\n")
        self.feedback_text.insert(tk.END, "\n" + "─" * 80 + "\n\n", "divider")

        # Per-question feedback
        for q, fb, student in all_feedback:
            # Question header
            self.feedback_text.insert(tk.END,
                f"Exam {q.exam_id}  •  Session {q.session_id}  •  {q.section_name}  •  Q{q.question_number}\n",
                "question_tag"
            )
            self.feedback_text.insert(tk.END, f"{q.question_text}\n\n")

            # Reference answer
            self.feedback_text.insert(tk.END, "REFERENCE ANSWER:\n", "ref_label")
            ref_text = (q.answer_text or "No reference answer available.").strip()
            self.feedback_text.insert(tk.END, f"{ref_text}\n\n", "ref_text")

            # Missing points
            if not student:
                self.feedback_text.insert(tk.END, "YOUR ANSWER: (no answer recorded)\n\n", "missing_label")
            elif fb.missing_points:
                self.feedback_text.insert(tk.END, "WHAT YOU MISSED:\n", "missing_label")
                for p in fb.missing_points:
                    self.feedback_text.insert(tk.END, f"  • {p.missed_concept}\n", "missing_item")
                self.feedback_text.insert(tk.END, "\n")
            else:
                self.feedback_text.insert(tk.END, "RESULT: Great — all key points covered!\n\n", "ok_label")

            self.feedback_text.insert(tk.END, "─" * 80 + "\n\n", "divider")

        self.feedback_text.config(state="disabled")
        self.feedback_text.see("1.0")

    # ── TIMER ─────────────────────────────────────────────────────────────────

    def _start_timer(self):
        self._tick_timer()

    def _stop_timer(self):
        if self.timer_job is not None:
            try:
                self.after_cancel(self.timer_job)
            except Exception:
                pass
            self.timer_job = None
        self.timer_label.config(text="")

    def _tick_timer(self):
        if self.section_start_time is None:
            return
        elapsed = int(time.time() - self.section_start_time)
        mins, secs = divmod(elapsed, 60)
        section = self.current_section_name or "Interview"
        self.timer_label.config(text=f"{section}:  {mins:02d}:{secs:02d}")
        self.timer_job = self.after(1000, self._tick_timer)

    # ── MIC / CAMERA ──────────────────────────────────────────────────────────

    def _update_char_count(self, _event=None):
        try:
            text = self.answer_box.get("1.0", tk.END).strip()
            count = len(text)
            self.char_count_label.config(text=f"{count} chars", foreground="gray" if count < 20 else "black")
        except Exception:
            pass

    def _report_ai_error(self):
        selected = None
        if self.interview_questions and 0 <= self.interview_idx < len(self.interview_questions):
            selected = self.interview_questions[self.interview_idx]
        payload = {
            "timestamp": datetime.now().isoformat(),
            "question": {
                "exam_id": selected.exam_id if selected else None,
                "session_id": selected.session_id if selected else None,
                "section_name": selected.section_name if selected else None,
                "question_number": selected.question_number if selected else None,
                "question_text": selected.question_text if selected else "",
            },
            "feedback_snapshot": self.feedback_text.get("1.0", tk.END).strip()[:2500],
            "report_type": "manual_user_flag",
        }
        path = append_ai_error_report(json.dumps(payload, ensure_ascii=True))
        messagebox.showinfo("Error Logged", f"Thank you! Your report has been saved locally:\n{path}")

    def _start_mic_capture(self):
        if not self.interview_questions:
            return
        if not self.attempt_paths:
            return
        if self.mic_recording:
            return
        current = self.interview_questions[self.interview_idx]
        part_num = self.interview_part_idx + 1
        self.current_audio_path = (
            self.attempt_paths.audio_dir
            / f"exam{current.exam_id}_s{current.session_id}_{current.section_name.replace(' ', '_')}_q{current.question_number}_p{part_num}.wav"
        )
        ok, msg = self.av.start_mic_recording(self.current_audio_path)
        if ok:
            self.mic_status_label.config(text="● Mic: recording", foreground="red")
            self.mic_recording = True
        else:
            self.mic_status_label.config(text="Mic: unavailable", foreground="gray")
            messagebox.showwarning("Microphone unavailable", f"{msg}\n\nYou can type your answer manually instead.")

    def _stop_mic_capture(self):
        ok, transcript_or_reason = self.av.stop_mic_and_transcribe()
        self.mic_status_label.config(text="Mic: idle", foreground="gray")
        self.mic_recording = False
        if not ok:
            messagebox.showwarning(
                "Transcription unavailable",
                f"{transcript_or_reason or 'Could not transcribe audio.'}\n\nYou can type your answer manually."
            )
            return
        transcript = transcript_or_reason
        current_text = self.answer_box.get("1.0", tk.END).strip()
        merged = (current_text + "\n" + transcript).strip() if current_text else transcript
        self.answer_box.delete("1.0", tk.END)
        self.answer_box.insert("1.0", merged)
        self._update_char_count()

    def _auto_start_mic(self):
        try:
            if not self.winfo_exists():
                return
            self.after(0, self._start_mic_capture)
        except Exception:
            pass

    def _start_camera_preview(self):
        if self.av.disable_camera:
            self.camera_label.config(text="Camera preview disabled (MEDPREPAI_DISABLE_CAMERA=1).")
            return
        if self.camera_job is not None:
            return
        if not self.camera_frame.winfo_ismapped():
            self.camera_frame.pack(fill="x", pady=(0, 6), before=self.question_label)
        self._camera_tick()

    def _stop_camera_preview(self):
        if self.camera_job is not None:
            try:
                self.after_cancel(self.camera_job)
            except Exception:
                pass
            self.camera_job = None
        try:
            self.camera_label.config(image="", text="Camera feed unavailable")
        except Exception:
            pass

    def _camera_tick(self):
        if not self.winfo_exists():
            return
        try:
            from PIL import Image, ImageTk
        except Exception:
            self.camera_label.config(text="Install Pillow for camera preview: pip install Pillow")
            self.camera_job = self.after(1000, self._camera_tick)
            return
        try:
            frame_bytes = self.av.get_camera_frame()
            if not frame_bytes:
                status, err = self.av.preview_status()
                if status in {"not_running", "exited"}:
                    self.camera_label.config(text=self._format_camera_error(err))
                    self.camera_job = self.after(500, self._camera_tick)
                    return
                self.camera_label.config(text="Camera feed unavailable")
                self.camera_job = self.after(200, self._camera_tick)
                return
            img = Image.open(io.BytesIO(frame_bytes))
            img = img.resize((1120, 420))
            self._camera_imgtk = ImageTk.PhotoImage(img)
            self.camera_label.config(image=self._camera_imgtk, text="")
        except Exception:
            self.camera_label.config(text="Camera preview error")
            self.camera_job = self.after(500, self._camera_tick)
            return
        self.camera_job = self.after(100, self._camera_tick)

    @staticmethod
    def _format_camera_error(err: str | None) -> str:
        if not err:
            return "Camera preview unavailable"
        low = err.lower()
        if "cv2 import failed" in low:
            return "Install opencv-python for camera preview: pip install opencv-python"
        if "camera open failed" in low:
            return "Camera could not be opened. Check permissions in System Settings → Privacy → Camera."
        snippet = err.strip()[:140]
        return f"Camera preview error: {snippet}"

    def _on_close(self):
        try:
            self._stop_camera_preview()
            self._stop_timer()
            self.av.close()
        finally:
            self.destroy()

    # ── HELPERS ───────────────────────────────────────────────────────────────

    @staticmethod
    def _qkey(m: MappingItem) -> str:
        return f"{m.exam_id}:{m.session_id}:{m.section_name}:{m.question_number}"

    @staticmethod
    def _part_key(m: MappingItem, part_idx: int) -> str:
        return f"{m.exam_id}:{m.session_id}:{m.section_name}:{m.question_number}:part{part_idx + 1}"

    @staticmethod
    def _session_key(exam_id: int, session_id: int) -> str:
        return f"exam{exam_id}_session{session_id}"

    def _combined_answer_for_question(self, q: MappingItem) -> str:
        parts = self._split_question_into_parts(q.question_text) or [q.question_text]
        values = [self.student_answers.get(self._part_key(q, idx), "").strip() for idx in range(len(parts))]
        return "\n".join(v for v in values if v).strip()

    def _split_question_into_parts(self, question_text: str) -> list[str]:
        import re
        text = " ".join((question_text or "").split())
        if not text:
            return []
        raw = [m.group(0).strip() for m in re.finditer(r"[^?.]+[?.]?", text)]
        parts: list[str] = []
        for clause in raw:
            c = clause.strip()
            if not c:
                continue
            low = c.lower()
            if parts and (low in {"why?", "why not?", "why/why not?"} or low.startswith("why/why not") or low.startswith("why not")):
                parts[-1] = f"{parts[-1]} {c}".strip()
            else:
                parts.append(c)
        merged: list[str] = []
        for part in parts:
            if merged:
                prev = merged[-1]
                part_low = part.lower()
                short_followup = len(part.split()) <= 4
                if prev.endswith(".") and (short_followup or part_low.startswith("you respond") or part_low.startswith("you do")):
                    merged[-1] = f"{prev} {part}".strip()
                    continue
            merged.append(part)
        return merged

    def _ensure_session_stems(self, kb: KnowledgeBase | None):
        if not kb or kb.session_stems:
            return
        try:
            q_text, _ = self._select_best_question_text(kb.questions_pdf)
            kb.session_stems = extract_session_stems(q_text, kb.questions_pdf)
        except Exception:
            kb.session_stems = {}

    def _ensure_case_widgets(self):
        if hasattr(self, "case_text") and self.case_text.winfo_exists():
            return
        parent = getattr(self, "_interview_main", None)
        if parent is None:
            return
        self.case_frame = ttk.LabelFrame(parent, text="Case Description")
        self.case_text = tk.Text(self.case_frame, height=12, wrap="word")
        self.case_text.pack(fill="both", expand=True)
        self.case_text.config(state="disabled")

    def _rebuild_kb_if_stale(self, kb: KnowledgeBase | None):
        if not kb or not kb.mappings:
            return
        looks_stale = any((m.section_name in {"", "Unknown"}) or ("index" in (m.match_reason or "")) for m in kb.mappings)
        if not looks_stale:
            return
        try:
            q_text, _ = self._select_best_question_text(kb.questions_pdf)
            a_text, _ = self._select_best_answer_text(kb.answers_pdf)
            questions = parse_questions(q_text, kb.questions_pdf)
            answers = parse_answers(a_text, kb.answers_pdf)
            mappings, unmatched = build_mappings(questions, answers)
            kb.mappings = mappings
            kb.unmatched_answers = unmatched
            kb.session_stems = extract_session_stems(q_text, kb.questions_pdf)
        except Exception:
            return


def main():
    app = MedPrepApp()
    app.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--camera-preview":
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from .camera_preview import main as preview_main
        raise SystemExit(preview_main())
    main()
