import os
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import requests

from app.config import ROOT_DIR, get_latest_json_url, load_version
from app.logger import logger
from app.services.pipeline import (
    DEFAULT_SOURCE_MASTER,
    build_fetch_plan,
    fetch_html,
    fetch_rss,
    refresh_trends,
    score_articles,
    summarize_articles,
    validate_sources,
    write_scan_brief,
)
from app.services.runtime_settings import (
    DEFAULT_FACEBOOK_INTRO_TEXT,
    facebook_brief_label_text,
    load_ai_env,
    load_runtime_settings,
    render_facebook_intro_text,
    save_ai_env,
    save_runtime_settings,
)
from app.services.combined_brief_source import (
    DEFAULT_COMBINED_BRIEF_PATH,
    build_combined_brief,
    format_empty_combined_message,
    format_combined_stats,
    get_sheet_run_status,
)
from app.services.facebook_publisher import check_page, publish_photo_post, validate_cards_publish_safety
from app.services.source_master import ALLOWED_VALUES, append_manual_source
from app.services.storage import DEFAULT_DB_PATH, count_rows, init_db, mark_items_published
from app.services.telegram_publisher import check_bot, check_chat, send_message, send_photos
from app.services.visual_brief_renderer import DEFAULT_VISUAL_BRIEF_DIR, generate_image_cards
from app.update_manager import download_update, get_update_status
from app.updater_launcher import launch_updater


BRIEF_DIR = ROOT_DIR / "output" / "briefs"
LOG_PATH = ROOT_DIR / "logs" / "application.log"
LATEST_BRIEF_MD = BRIEF_DIR / "latest_brief.md"
LATEST_BRIEF_JSON = BRIEF_DIR / "latest_brief.json"


class AppGUI:
    def __init__(self):
        metadata = load_version()
        self.metadata = metadata
        self.settings = load_runtime_settings()
        self.env = load_ai_env()
        self.task_running = False
        self.pause_requested = False
        self.stop_requested = False
        self.update_check_running = False
        self.last_schedule_key = None
        self.action_buttons = []

        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk()
        self.root.title(metadata.get("project_name", "Maritime Intelligence Hub"))
        self.root.geometry("1180x760")
        self.root.minsize(1020, 680)

        self._build_vars()
        self._build_layout()
        self._refresh_dashboard()
        self._load_latest_brief()
        self._load_sources_view()
        self._load_log_file()
        self._append_output("GUI opened")
        self.root.after(800, self._check_update_on_startup)
        self.root.after(30000, self._scheduler_tick)

    def _build_vars(self):
        scan = self.settings["scan"]
        ai = self.settings["ai"]
        visual = self.settings["visual"]
        publish = self.settings["publish"]

        self.status_var = ctk.StringVar(value="Ready")
        self.next_run_var = ctk.StringVar(value="Next run: calculating")
        self.summary_var = ctk.StringVar(value="No scan running")
        self.auto_run_var = ctk.BooleanVar(value=bool(scan.get("auto_run_enabled", False)))
        self.scan_label_var = ctk.StringVar(value=self._current_scan_label())
        self.priority_var = ctk.StringVar(value=scan["priority"])
        self.limit_var = ctk.StringVar(value=str(scan["limit_per_source"]))
        self.brief_limit_var = ctk.StringVar(value=str(scan["brief_limit"]))
        self.keywords_var = ctk.StringVar(value=scan["keywords"])
        self.runs_per_day_var = ctk.StringVar(value=str(scan["runs_per_day"]))
        self.schedule_times_var = ctk.StringVar(value=", ".join(scan["times"]))
        self.timezone_offset_var = ctk.StringVar(value=str(scan.get("timezone_offset", "+7")))
        self.retry_attempts_var = ctk.StringVar(value=str(scan.get("retry_attempts", 2)))

        self.provider_var = ctk.StringVar(value=ai["provider"])
        self.model_var = ctk.StringVar(value=ai["model"])
        self.api_key_var = ctk.StringVar(value=self._current_api_key())
        self.min_score_var = ctk.StringVar(value=str(ai["min_score"]))
        self.force_summary_var = ctk.BooleanVar(value=bool(ai["force_summary"]))
        self.ai_request_delay_var = ctk.StringVar(value=str(ai.get("request_delay_seconds", 1.5)))
        self.ai_retry_attempts_var = ctk.StringVar(value=str(ai.get("retry_attempts", 2)))

        self.font_var = ctk.StringVar(value=visual["font_family"])
        self.title_size_var = ctk.StringVar(value=str(visual["title_size"]))
        self.summary_size_var = ctk.StringVar(value=str(visual["summary_size"]))
        self.impact_size_var = ctk.StringVar(value=str(visual["impact_size"]))
        self.text_color_var = ctk.StringVar(value=visual["text_color"])
        self.accent_color_var = ctk.StringVar(value=visual["accent_color"])
        self.watermark_var = ctk.StringVar(value=visual["watermark"])
        self.show_title_var = ctk.BooleanVar(value=bool(visual["show_title"]))
        self.show_summary_var = ctk.BooleanVar(value=bool(visual["show_summary"]))
        self.show_impact_var = ctk.BooleanVar(value=bool(visual["show_impact"]))
        self.show_source_var = ctk.BooleanVar(value=bool(visual["show_source"]))
        self.show_url_var = ctk.BooleanVar(value=bool(visual["show_url"]))
        self.show_hot_keywords_var = ctk.BooleanVar(value=bool(visual["show_hot_keywords"]))
        self.visual_limit_var = ctk.StringVar(value=str(scan["brief_limit"]))
        self.visual_source_mode_var = ctk.StringVar(value=visual.get("source_mode", "combined"))
        self.sheet_url_var = ctk.StringVar(value=visual.get("sheet_url", ""))
        self.sheet_limit_max_var = ctk.BooleanVar(value=bool(visual.get("sheet_limit_max", True)))
        self.sheet_limit_var = ctk.StringVar(value=str(visual.get("sheet_limit", 20)))
        self.app_limit_max_var = ctk.BooleanVar(value=bool(visual.get("app_limit_max", True)))
        self.app_limit_var = ctk.StringVar(value=str(visual.get("app_limit", 20)))
        self.card_limit_max_var = ctk.BooleanVar(value=bool(visual.get("card_limit_max", True)))
        self.card_limit_var = ctk.StringVar(value=str(visual.get("card_limit", scan["brief_limit"])))

        self.create_markdown_var = ctk.BooleanVar(value=bool(publish["create_markdown"]))
        self.create_json_var = ctk.BooleanVar(value=bool(publish["create_json"]))
        self.create_image_cards_var = ctk.BooleanVar(value=bool(publish["create_image_cards"]))
        self.send_telegram_var = ctk.BooleanVar(value=bool(publish["send_telegram"]))
        self.post_facebook_var = ctk.BooleanVar(value=bool(publish["post_facebook"]))
        self.telegram_chat_id_var = ctk.StringVar(value=self._initial_telegram_chat_ids(publish))
        self.telegram_bot_token_var = ctk.StringVar(value=self.env.get("TELEGRAM_BOT_TOKEN", ""))
        self.telegram_intro_text_var = ctk.StringVar(value=publish.get("telegram_intro_text") or "{date}")
        self.facebook_page_id_var = ctk.StringVar(value=self.env.get("FACEBOOK_PAGE_ID", ""))
        self.facebook_page_access_token_var = ctk.StringVar(value=self.env.get("FACEBOOK_PAGE_ACCESS_TOKEN", ""))
        self.facebook_intro_text_var = ctk.StringVar(
            value=publish.get("facebook_intro_text") or DEFAULT_FACEBOOK_INTRO_TEXT
        )
        self.facebook_dry_run_var = ctk.BooleanVar(value=bool(publish.get("facebook_dry_run", True)))

    def _build_layout(self):
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self.root, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(
            header,
            text=self.metadata.get("project_name", "Maritime Intelligence Hub"),
            font=ctk.CTkFont(size=23, weight="bold"),
        )
        title.grid(row=0, column=0, sticky="w", padx=20, pady=(12, 2))

        subtitle = ctk.CTkLabel(
            header,
            text=f"{self.metadata.get('app_name', 'BV Application')} | Version {self.metadata.get('version', '0.0.0')}",
            text_color=("gray35", "gray75"),
        )
        subtitle.grid(row=1, column=0, sticky="w", padx=20, pady=(0, 12))

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=1, rowspan=2, sticky="e", padx=20, pady=10)
        run_button = ctk.CTkButton(actions, text="Run Now", width=120, command=self._run_scan_now)
        run_button.pack(side="left", padx=(0, 8))
        self.action_buttons.append(run_button)
        self.pause_button = ctk.CTkButton(actions, text="Pause", width=80, state="disabled", command=self._toggle_pause)
        self.pause_button.pack(side="left", padx=(0, 8))
        self.stop_button = ctk.CTkButton(actions, text="Stop", width=80, state="disabled", command=self._request_stop)
        self.stop_button.pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Check Update", width=120, command=self._check_update).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(actions, textvariable=self.status_var, width=250, anchor="e").pack(side="left")

        tabs = ctk.CTkTabview(self.root)
        tabs.grid(row=1, column=0, sticky="nsew", padx=16, pady=16)
        self.overview_tab = tabs.add("Tổng quan")
        self.scan_tab = tabs.add("Lịch quét")
        self.ai_tab = tabs.add("AI & API")
        self.sources_tab = tabs.add("Nguồn tin")
        self.visual_tab = tabs.add("Ảnh bản tin")
        self.publish_tab = tabs.add("Hoàn thành")
        self.logs_tab = tabs.add("Log")

        self._build_overview_tab()
        self._build_scan_tab()
        self._build_ai_tab()
        self._build_sources_tab()
        self._build_visual_tab()
        self._build_publish_tab()
        self._build_logs_tab()

    def _build_overview_tab(self):
        tab = self.overview_tab
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        metrics = ctk.CTkFrame(tab)
        metrics.grid(row=0, column=0, sticky="ew", pady=8)
        metrics.grid_columnconfigure((0, 1, 2, 3, 4, 5), weight=1)
        self.stat_labels = {}
        for column, key in enumerate(["sources", "articles", "summaries", "briefs", "fetch_logs", "trends"]):
            box = ctk.CTkFrame(metrics)
            box.grid(row=0, column=column, sticky="ew", padx=4, pady=4)
            ctk.CTkLabel(box, text=key.replace("_", " ").title(), text_color=("gray35", "gray75")).pack(pady=(10, 0))
            value = ctk.CTkLabel(box, text="0", font=ctk.CTkFont(size=20, weight="bold"))
            value.pack(pady=(0, 10))
            self.stat_labels[key] = value

        quick = ctk.CTkFrame(tab)
        quick.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(quick, textvariable=self.next_run_var, anchor="w").pack(side="left", padx=12, pady=10)
        ctk.CTkButton(quick, text="Open Output", width=120, command=lambda: self._open_path(BRIEF_DIR)).pack(side="right", padx=8)
        ctk.CTkButton(quick, text="Refresh", width=100, command=self._refresh_all).pack(side="right", padx=8)

        body = ctk.CTkFrame(tab)
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure((0, 1), weight=1)
        body.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(body, textvariable=self.summary_var, anchor="w", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 4)
        )
        self.output_box = ctk.CTkTextbox(body, wrap="word", height=180)
        self.output_box.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(0, 12))
        self.output_box.configure(state="disabled")
        self.latest_brief_box = ctk.CTkTextbox(body, wrap="word", height=180)
        self.latest_brief_box.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(0, 12))
        self.latest_brief_box.configure(state="disabled")

    def _build_scan_tab(self):
        tab = self.scan_tab
        tab.grid_columnconfigure(0, weight=1)
        form = ctk.CTkFrame(tab)
        form.grid(row=0, column=0, sticky="new", pady=8)
        form.grid_columnconfigure(1, weight=1)

        self._entry(form, "Keyword cần tìm sâu", self.keywords_var, 0, width=620)
        self._entry(form, "Số tin tối đa mỗi nguồn", self.limit_var, 1)
        self._entry(form, "Số tin tối đa mỗi brief", self.brief_limit_var, 2)
        self._entry(form, "Số lần lấy tin/ngày", self.runs_per_day_var, 3)
        self._entry(form, "Thời gian chạy", self.schedule_times_var, 4, width=260)
        self._option(form, "Time zone", self.timezone_offset_var, self._timezone_options(), 5)
        self._entry(form, "Retry mỗi bước", self.retry_attempts_var, 6, width=90)
        self._option(form, "Ưu tiên nguồn", self.priority_var, ["P1", "P2", "P3"], 7)
        self._option(form, "Nhãn chạy thủ công", self.scan_label_var, ["morning", "evening"], 8)
        ctk.CTkCheckBox(form, text="Tự chạy vòng lặp theo lịch", variable=self.auto_run_var).grid(
            row=9, column=1, sticky="w", padx=10, pady=8
        )

        hint = ctk.CTkLabel(
            form,
            text="Nhập thời gian dạng HH:MM, phân tách bằng dấu phẩy. Mặc định: 07:15, 19:15.",
            text_color=("gray35", "gray75"),
            anchor="w",
        )
        hint.grid(row=10, column=1, sticky="w", padx=10, pady=(0, 10))
        ctk.CTkButton(form, text="Save Scan Settings", width=170, command=self._save_settings).grid(
            row=11, column=1, sticky="w", padx=10, pady=12
        )
        ctk.CTkButton(form, text="Test Source Plan", width=150, command=self._load_sources_view).grid(
            row=11, column=1, sticky="w", padx=(190, 10), pady=12
        )

    def _build_ai_tab(self):
        tab = self.ai_tab
        tab.grid_columnconfigure(0, weight=1)
        form = ctk.CTkFrame(tab)
        form.grid(row=0, column=0, sticky="new", pady=8)
        form.grid_columnconfigure(1, weight=1)
        self._option(form, "Provider", self.provider_var, ["mock", "openai", "gemini"], 0, command=self._provider_changed)
        self._entry(form, "Model", self.model_var, 1, width=260)
        self._entry(form, "API Key", self.api_key_var, 2, width=420, show="*")
        self._entry(form, "Min score để tóm tắt", self.min_score_var, 3)
        self._entry(form, "Delay giữa request AI", self.ai_request_delay_var, 4, width=90)
        self._entry(form, "Retry AI", self.ai_retry_attempts_var, 5, width=90)
        ctk.CTkCheckBox(form, text="Force summarize lại tin cũ", variable=self.force_summary_var).grid(
            row=6, column=1, sticky="w", padx=10, pady=8
        )
        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=7, column=1, sticky="w", padx=10, pady=12)
        ctk.CTkButton(actions, text="Check API Key", width=140, command=self._check_api_key).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Save AI Settings", width=150, command=self._save_settings).pack(side="left")

    def _build_sources_tab(self):
        tab = self.sources_tab
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        toolbar = ctk.CTkFrame(tab)
        toolbar.grid(row=0, column=0, sticky="ew", pady=8)
        for text, command in [
            ("Add Source", self._open_add_source_dialog),
            ("Validate", self._validate_sources),
            ("Plan P1", self._load_sources_view),
            ("Open CSV", lambda: self._open_path(DEFAULT_SOURCE_MASTER)),
            ("Export Copy", self._export_sources),
        ]:
            ctk.CTkButton(toolbar, text=text, width=110, command=command).pack(side="left", padx=8, pady=10)
        self.sources_box = ctk.CTkTextbox(tab, wrap="none")
        self.sources_box.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        self.sources_box.configure(state="disabled")

    def _build_visual_tab(self):
        tab = self.visual_tab
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        def compact_entry(parent, label, variable, row, width=120, show=None):
            ctk.CTkLabel(parent, text=label, width=160, anchor="w").grid(row=row, column=0, sticky="w", padx=10, pady=4)
            ctk.CTkEntry(parent, textvariable=variable, width=width, show=show).grid(
                row=row, column=1, sticky="w", padx=8, pady=4
            )

        def compact_option(parent, label, variable, values, row, command=None):
            ctk.CTkLabel(parent, text=label, width=160, anchor="w").grid(row=row, column=0, sticky="w", padx=10, pady=4)
            ctk.CTkOptionMenu(parent, variable=variable, values=values, width=140, command=command).grid(
                row=row, column=1, sticky="w", padx=8, pady=4
            )

        def compact_limit_control(parent, label, variable, max_variable, row):
            ctk.CTkLabel(parent, text=label, width=160, anchor="w").grid(row=row, column=0, sticky="w", padx=10, pady=4)
            holder = ctk.CTkFrame(parent, fg_color="transparent")
            holder.grid(row=row, column=1, sticky="w", padx=8, pady=4)
            entry = ctk.CTkEntry(holder, textvariable=variable, width=90)
            entry.pack(side="left")
            checkbox = ctk.CTkCheckBox(
                holder,
                text="Lay toi da",
                variable=max_variable,
                command=self._update_visual_limit_states,
            )
            checkbox.pack(side="left", padx=(10, 0))
            if not hasattr(self, "_limit_entries"):
                self._limit_entries = []
            self._limit_entries.append((entry, max_variable))

        settings_scroll = ctk.CTkScrollableFrame(tab, height=390)
        settings_scroll.grid(row=0, column=0, sticky="nsew", pady=(8, 8))
        settings_scroll.grid_columnconfigure(0, weight=1)

        source_panel = ctk.CTkFrame(settings_scroll)
        source_panel.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        source_panel.grid_columnconfigure(1, weight=1)
        compact_option(
            source_panel,
            "Source mode",
            self.visual_source_mode_var,
            ["combined", "app", "sheet"],
            0,
            command=lambda _value: self._update_visual_limit_states(),
        )
        compact_entry(source_panel, "Google Sheet URL", self.sheet_url_var, 1, width=620)
        compact_limit_control(source_panel, "Sheet items", self.sheet_limit_var, self.sheet_limit_max_var, 2)
        compact_limit_control(source_panel, "App items", self.app_limit_var, self.app_limit_max_var, 3)
        compact_limit_control(source_panel, "Image cards", self.card_limit_var, self.card_limit_max_var, 4)

        source_actions = ctk.CTkFrame(source_panel, fg_color="transparent")
        source_actions.grid(row=5, column=1, sticky="w", padx=8, pady=6)
        check_button = ctk.CTkButton(source_actions, text="Check Sources", width=130, command=self._check_combined_sources)
        check_button.pack(side="left", padx=(0, 8))
        self.action_buttons.append(check_button)
        test_button = ctk.CTkButton(source_actions, text="Generate Test", width=130, command=self._generate_combined_cards)
        test_button.pack(side="left", padx=(0, 8))
        self.action_buttons.append(test_button)
        send_button = ctk.CTkButton(source_actions, text="Generate & Send", width=150, command=self._generate_and_send_combined_cards)
        send_button.pack(side="left", padx=(0, 8))
        self.action_buttons.append(send_button)

        form = ctk.CTkFrame(settings_scroll)
        form.grid(row=1, column=0, sticky="ew")
        for column in range(4):
            form.grid_columnconfigure(column, weight=1)
        compact_entry(form, "Font", self.font_var, 0, width=180)
        compact_entry(form, "Title size", self.title_size_var, 1, width=90)
        compact_entry(form, "Summary size", self.summary_size_var, 2, width=90)
        compact_entry(form, "Impact size", self.impact_size_var, 3, width=90)
        compact_entry(form, "Text color", self.text_color_var, 4, width=110)
        compact_entry(form, "Accent color", self.accent_color_var, 5, width=110)
        compact_entry(form, "Chữ nổi góc phải", self.watermark_var, 6, width=360)

        checks = ctk.CTkFrame(form, fg_color="transparent")
        checks.grid(row=7, column=1, columnspan=3, sticky="w", padx=8, pady=4)
        for text, var in [
            ("Title", self.show_title_var),
            ("Summary", self.show_summary_var),
            ("Impact", self.show_impact_var),
            ("Source", self.show_source_var),
            ("URL", self.show_url_var),
            ("Hot keywords", self.show_hot_keywords_var),
        ]:
            ctk.CTkCheckBox(checks, text=text, variable=var).pack(side="left", padx=(0, 10))

        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=8, column=1, columnspan=3, sticky="w", padx=8, pady=(6, 8))
        generate = ctk.CTkButton(
            actions,
            text="Generate Cards From Selected Source",
            width=240,
            command=self._generate_combined_cards,
        )
        generate.pack(side="left", padx=(0, 8))
        self.action_buttons.append(generate)
        ctk.CTkButton(actions, text="Save Image Settings", width=170, command=self._save_settings).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Open Visual Folder", width=150, command=lambda: self._open_path(DEFAULT_VISUAL_BRIEF_DIR)).pack(side="left")

        self.visual_box = ctk.CTkTextbox(tab, wrap="word")
        self.visual_box.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        self.visual_box.configure(state="disabled")
        self._set_text(self.visual_box, "Image card output will appear here.", disabled=True)
        self._update_visual_limit_states()

    def _build_publish_tab(self):
        tab = self.publish_tab
        tab.grid_columnconfigure(0, weight=1)
        panel = ctk.CTkFrame(tab)
        panel.grid(row=0, column=0, sticky="new", pady=8)
        panel.grid_columnconfigure(1, weight=1)
        ctk.CTkCheckBox(panel, text="Tạo Markdown", variable=self.create_markdown_var).grid(
            row=0, column=1, sticky="w", padx=10, pady=6
        )
        ctk.CTkCheckBox(panel, text="Tạo JSON", variable=self.create_json_var).grid(
            row=1, column=1, sticky="w", padx=10, pady=6
        )
        ctk.CTkCheckBox(panel, text="Tạo ảnh bản tin", variable=self.create_image_cards_var).grid(
            row=2, column=1, sticky="w", padx=10, pady=6
        )
        telegram_row = ctk.CTkFrame(panel, fg_color="transparent")
        telegram_row.grid(row=3, column=1, sticky="w", padx=10, pady=6)
        ctk.CTkCheckBox(telegram_row, text="Gửi Telegram", variable=self.send_telegram_var).pack(side="left")
        ctk.CTkButton(telegram_row, text="⚙", width=44, command=self._open_telegram_dialog).pack(side="left", padx=(10, 0))
        facebook_row = ctk.CTkFrame(panel, fg_color="transparent")
        facebook_row.grid(row=4, column=1, sticky="w", padx=10, pady=6)
        ctk.CTkCheckBox(facebook_row, text="Đăng Facebook", variable=self.post_facebook_var).pack(side="left")
        ctk.CTkButton(facebook_row, text="FB", width=44, command=self._open_facebook_dialog).pack(side="left", padx=(10, 0))
        ctk.CTkButton(panel, text="Save Publish Settings", width=180, command=self._save_settings).grid(
            row=6, column=1, sticky="w", padx=10, pady=12
        )
        ctk.CTkButton(panel, text="Send Selected Source Cards", width=210, command=self._send_latest_cards).grid(
            row=6, column=1, sticky="w", padx=(210, 10), pady=12
        )
        ctk.CTkButton(panel, text="Preview Facebook", width=160, command=self._preview_facebook_post).grid(
            row=7, column=1, sticky="w", padx=10, pady=(0, 12)
        )
        ctk.CTkButton(panel, text="Post Facebook", width=150, command=self._post_facebook_now).grid(
            row=7, column=1, sticky="w", padx=(185, 10), pady=(0, 12)
        )

    def _build_logs_tab(self):
        tab = self.logs_tab
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        toolbar = ctk.CTkFrame(tab)
        toolbar.grid(row=0, column=0, sticky="ew", pady=8)
        ctk.CTkLabel(toolbar, text=str(LOG_PATH.relative_to(ROOT_DIR))).pack(side="left", padx=12, pady=10)
        ctk.CTkButton(toolbar, text="Reload", width=100, command=self._load_log_file).pack(side="right", padx=8, pady=10)
        ctk.CTkButton(toolbar, text="Open Folder", width=110, command=lambda: self._open_path(LOG_PATH.parent)).pack(
            side="right", padx=8, pady=10
        )
        self.log_box = ctk.CTkTextbox(tab, wrap="word")
        self.log_box.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        self.log_box.configure(state="disabled")

    def _entry(self, parent, label, variable, row, width=120, show=None):
        ctk.CTkLabel(parent, text=label, width=170, anchor="w").grid(row=row, column=0, sticky="w", padx=12, pady=7)
        ctk.CTkEntry(parent, textvariable=variable, width=width, show=show).grid(
            row=row, column=1, sticky="w", padx=10, pady=7
        )

    def _option(self, parent, label, variable, values, row, command=None):
        ctk.CTkLabel(parent, text=label, width=170, anchor="w").grid(row=row, column=0, sticky="w", padx=12, pady=7)
        ctk.CTkOptionMenu(parent, variable=variable, values=values, width=140, command=command).grid(
            row=row, column=1, sticky="w", padx=10, pady=7
        )

    def _limit_control(self, parent, label, variable, max_variable, row):
        ctk.CTkLabel(parent, text=label, width=170, anchor="w").grid(row=row, column=0, sticky="w", padx=12, pady=7)
        holder = ctk.CTkFrame(parent, fg_color="transparent")
        holder.grid(row=row, column=1, sticky="w", padx=10, pady=7)
        entry = ctk.CTkEntry(holder, textvariable=variable, width=90)
        entry.pack(side="left")
        checkbox = ctk.CTkCheckBox(
            holder,
            text="Lay toi da",
            variable=max_variable,
            command=self._update_visual_limit_states,
        )
        checkbox.pack(side="left", padx=(10, 0))
        if not hasattr(self, "_limit_entries"):
            self._limit_entries = []
        self._limit_entries.append((entry, max_variable))

    def _update_visual_limit_states(self):
        for entry, max_variable in getattr(self, "_limit_entries", []):
            entry.configure(state="disabled" if max_variable.get() else "normal")

    def _save_settings(self):
        self.settings = {
            "scan": {
                "auto_run_enabled": self.auto_run_var.get(),
                "priority": self.priority_var.get(),
                "limit_per_source": self._int_var(self.limit_var, 10),
                "brief_limit": self._int_var(self.brief_limit_var, 12),
                "keywords": self.keywords_var.get().strip(),
                "runs_per_day": self._int_var(self.runs_per_day_var, 2),
                "times": self._schedule_times(),
                "timezone_offset": self._timezone_offset_text(),
                "retry_attempts": self._int_var(self.retry_attempts_var, 2),
                "preferred_sources": self.settings.get("scan", {}).get("preferred_sources", []),
            },
            "ai": {
                "provider": self.provider_var.get(),
                "model": self.model_var.get().strip() or self._default_model(self.provider_var.get()),
                "min_score": self._int_var(self.min_score_var, 6),
                "force_summary": self.force_summary_var.get(),
                "request_delay_seconds": self._float_var(self.ai_request_delay_var, 1.5),
                "retry_attempts": self._int_var(self.ai_retry_attempts_var, 2),
            },
            "visual": self._visual_settings(),
            "publish": {
                "create_markdown": self.create_markdown_var.get(),
                "create_json": self.create_json_var.get(),
                "create_image_cards": self.create_image_cards_var.get(),
                "send_telegram": self.send_telegram_var.get(),
                "post_facebook": self.post_facebook_var.get(),
                "telegram_chat_id": self._telegram_chat_ids()[0] if self._telegram_chat_ids() else "",
                "telegram_chat_ids": self._telegram_chat_ids(),
                "telegram_intro_text": self.telegram_intro_text_var.get().strip() or "{date}",
                "facebook_intro_text": self.facebook_intro_text_var.get().strip() or DEFAULT_FACEBOOK_INTRO_TEXT,
                "facebook_dry_run": self.facebook_dry_run_var.get(),
            },
        }
        save_runtime_settings(self.settings)
        provider = self.provider_var.get()
        env_values = {"AI_PROVIDER": provider}
        if provider == "openai":
            env_values.update({"OPENAI_MODEL": self.model_var.get(), "OPENAI_API_KEY": self.api_key_var.get()})
        elif provider == "gemini":
            env_values.update({"GEMINI_MODEL": self.model_var.get(), "GEMINI_API_KEY": self.api_key_var.get()})
        env_values["TELEGRAM_BOT_TOKEN"] = self.telegram_bot_token_var.get().strip()
        env_values["FACEBOOK_PAGE_ID"] = self.facebook_page_id_var.get().strip()
        env_values["FACEBOOK_PAGE_ACCESS_TOKEN"] = self.facebook_page_access_token_var.get().strip()
        env_values["AI_REQUEST_DELAY_SECONDS"] = str(self._float_var(self.ai_request_delay_var, 1.5))
        env_values["AI_RETRY_ATTEMPTS"] = str(self._int_var(self.ai_retry_attempts_var, 2))
        save_ai_env(env_values)
        self.status_var.set("Settings saved")
        self._update_next_run_label()

    def _run_scan_now(self):
        self._save_settings()
        self._run_background("Waiting for scheduled news scan", self._task_wait_for_scheduled_scan)

    def _task_wait_for_scheduled_scan(self):
        due_label, due_key = self._due_schedule()
        if due_label:
            self.last_schedule_key = due_key
            self.scan_label_var.set(due_label)
            return self._task_run_scan(due_label)

        next_run = self._next_scheduled_run()
        if not next_run:
            return "No valid schedule time found. Please check scan schedule settings.", False

        now = self._schedule_now()
        wait_seconds = max(1, int((next_run - now).total_seconds()))
        label = self._scan_label_for_time(next_run)
        wait_message = (
            f"Not scheduled time yet. Current schedule time is {now.strftime('%Y-%m-%d %H:%M:%S')} "
            f"UTC{self._timezone_offset_text()}.\n"
            f"Sleeping until {next_run.strftime('%Y-%m-%d %H:%M:%S')} UTC{self._timezone_offset_text()} "
            f"for {label} scan."
        )
        self.root.after(0, self._set_text, self.output_box, wait_message, True)
        self.root.after(0, self.summary_var.set, f"Waiting until {next_run.strftime('%Y-%m-%d %H:%M')} UTC{self._timezone_offset_text()}")
        logger.info(wait_message.replace("\n", " "))

        end_time = time.time() + wait_seconds
        while time.time() < end_time:
            self._checkpoint("scheduled wait")
            remaining = end_time - time.time()
            time.sleep(min(30, max(0.2, remaining)))

        self.last_schedule_key = f"{next_run.date()}-{next_run.strftime('%H:%M')}"
        self.scan_label_var.set(label)
        output, ok = self._task_run_scan(label)
        return f"{wait_message}\n\n{output}", ok

    def _task_run_scan(self, label):
        self._checkpoint("refresh trends")
        trend_result = self._retry_gui_step("refresh_trends", lambda: refresh_trends())

        self._checkpoint("fetch RSS")
        fetch_result = self._retry_gui_step(
            "fetch_rss",
            lambda: fetch_rss(
                priority=self.priority_var.get(),
                limit=self._int_var(self.limit_var, 10),
                source_master=DEFAULT_SOURCE_MASTER,
            ),
        )
        if not fetch_result["ok"]:
            return self._format_errors(fetch_result["validation"].errors), False

        self._checkpoint("fetch HTML")
        html_result = self._retry_gui_step(
            "fetch_html",
            lambda: fetch_html(
                priority=self.priority_var.get(),
                limit=self._int_var(self.limit_var, 10),
                source_master=DEFAULT_SOURCE_MASTER,
            ),
        )

        self._checkpoint("score articles")
        scored = self._retry_gui_step("score_articles", score_articles)

        self._checkpoint("summarize articles")
        summaries = self._retry_gui_step(
            "summarize_articles",
            lambda: summarize_articles(
                min_score=self._int_var(self.min_score_var, 6),
                force=self.force_summary_var.get(),
                limit=self._int_var(self.brief_limit_var, 12),
            ),
        )

        self._checkpoint("write brief")
        brief = self._retry_gui_step(
            "write_scan_brief",
            lambda: write_scan_brief(scan_label=label, limit=self._int_var(self.brief_limit_var, 12)),
        )
        rss_inserted = sum(item["inserted"] for item in fetch_result["results"])
        html_inserted = sum(item["inserted"] for item in html_result["results"])
        lines = [
            f"Scan complete: {label}",
            (
                f"Trends: seeded={trend_result['seeded']} imported={trend_result['imported']} "
                f"fetched={trend_result['fetched']}"
            ),
            f"RSS inserted articles: {rss_inserted}",
            f"HTML inserted articles: {html_inserted}",
            f"Scored articles: {len(scored)}",
            f"AI summaries: {len(summaries)}",
            f"Brief items: {brief['items']}",
            f"Markdown: {brief['markdown_path']}",
            f"Latest: {brief['latest_markdown_path']}",
        ]
        send_telegram = self._var_bool("send_telegram_var", False)
        post_facebook = self._var_bool("post_facebook_var", False)
        card_result = None
        if self.create_image_cards_var.get() or send_telegram or post_facebook:
            self._checkpoint("generate image cards")
            card_result = self._retry_gui_step(
                "generate_selected_source_image_cards",
                self._generate_selected_source_cards_result,
            )
            lines.extend(["", self._format_selected_source_cards_result(card_result)])
        if send_telegram:
            if not card_result:
                return "\n".join(lines + ["Telegram skipped: no image cards generated"]), False
            self._checkpoint("send Telegram")
            send_output, send_ok = self._retry_gui_step(
                "send_telegram",
                lambda: self._task_send_cards(card_result["cards_result"]["cards"], card_result.get("brief_label", label)),
            )
            lines.extend(["", send_output])
            if not send_ok:
                return "\n".join(lines), False
        if post_facebook:
            if not card_result:
                return "\n".join(lines + ["Facebook skipped: no image cards generated"]), False
            self._checkpoint("post Facebook")
            facebook_output, facebook_ok = self._retry_gui_step(
                "post_facebook",
                lambda: self._task_post_facebook_cards(
                    card_result["cards_result"]["cards"],
                    card_result.get("brief_label", label),
                    dry_run=self._var_bool("facebook_dry_run_var", True),
                ),
            )
            lines.extend(["", facebook_output])
            if not facebook_ok:
                return "\n".join(lines), False
        return "\n".join(lines), True

    def _generate_latest_cards(self):
        self._save_settings()
        self._run_background("Generating image cards", self._task_generate_latest_cards)

    def _task_generate_latest_cards(self):
        result = self._generate_latest_cards_result()
        return self._format_card_result(result), True

    def _generate_latest_cards_result(self):
        if not LATEST_BRIEF_JSON.exists():
            raise FileNotFoundError(f"Latest brief JSON not found: {LATEST_BRIEF_JSON}")
        return generate_image_cards(
            self.scan_label_var.get(),
            limit=self._int_var(self.visual_limit_var, 12),
            source_brief_path=LATEST_BRIEF_JSON,
            style_settings=self._visual_settings(),
        )

    def _generate_selected_source_cards_result(self):
        return self._generate_combined_cards_result()

    def _format_selected_source_cards_result(self, result):
        return "\n".join(
            [
                format_combined_stats(result["source_stats"], result["brief_path"]),
                "",
                self._format_card_result(result["cards_result"]),
            ]
        )

    def _check_combined_sources(self):
        self._save_settings()
        self._run_background("Checking combined sources", self._task_check_combined_sources)

    def _task_check_combined_sources(self):
        result = build_combined_brief(
            source_mode=self.visual_source_mode_var.get(),
            sheet_url=self.sheet_url_var.get().strip(),
            sheet_limit=self._optional_limit(self.sheet_limit_var, self.sheet_limit_max_var),
            app_limit=self._optional_limit(self.app_limit_var, self.app_limit_max_var),
            card_limit=None,
        )
        return format_combined_stats(result.stats, result.brief_path), True

    def _generate_combined_cards(self):
        self._save_settings()
        self._run_background("Generating combined image cards", self._task_generate_combined_cards)

    def _task_generate_combined_cards(self):
        result = self._generate_combined_cards_result()
        lines = [
            format_combined_stats(result["source_stats"], result["brief_path"]),
            "",
            self._format_card_result(result["cards_result"]),
        ]
        return "\n".join(lines), True

    def _generate_and_send_combined_cards(self):
        self._save_settings()
        self._run_background("Generating and sending combined cards", self._task_generate_and_send_combined_cards)

    def _task_generate_and_send_combined_cards(self):
        result = self._retry_gui_step("generate_combined_image_cards", self._generate_combined_cards_result)
        cards = result["cards_result"]["cards"]
        send_output, send_ok = self._retry_gui_step(
            "send_telegram",
            lambda: self._task_send_cards(cards, result.get("brief_label")),
        )
        lines = [
            format_combined_stats(result["source_stats"], result["brief_path"]),
            "",
            self._format_card_result(result["cards_result"]),
            "",
            send_output,
        ]
        return "\n".join(lines), send_ok

    def _generate_combined_cards_result(self):
        source_mode = self.visual_source_mode_var.get().strip().lower()
        sheet_ready = self._wait_for_sheet_if_needed(source_mode)
        sheet_limit = None if source_mode == "sheet" else self._optional_limit(self.sheet_limit_var, self.sheet_limit_max_var)
        card_limit = None if source_mode == "sheet" else self._optional_limit(self.card_limit_var, self.card_limit_max_var)
        source_result = build_combined_brief(
            source_mode=source_mode,
            sheet_url=self.sheet_url_var.get().strip(),
            sheet_limit=sheet_limit,
            app_limit=self._optional_limit(self.app_limit_var, self.app_limit_max_var),
            card_limit=card_limit,
            brief_path=DEFAULT_COMBINED_BRIEF_PATH,
        )
        if sheet_ready:
            source_result.stats.setdefault("sheet_source", {}).update(sheet_ready)
        if not source_result.payload.get("items"):
            raise RuntimeError(format_empty_combined_message(source_result.stats, source_result.brief_path))
        cards_result = generate_image_cards(
            "combined",
            limit=card_limit,
            source_brief_path=source_result.brief_path,
            style_settings=self._visual_settings(),
        )
        brief_label = (source_result.stats.get("sheet_source") or {}).get("run_label") or self._current_scan_label()
        return {
            "brief_path": source_result.brief_path,
            "source_stats": source_result.stats,
            "cards_result": cards_result,
            "brief_label": brief_label,
        }

    def _format_card_result(self, result):
        return "\n".join([
            f"Generated {result['items']} image cards",
            f"Output: {result['output_dir']}",
            f"Manifest: {result['manifest_path']}",
            f"Preview: {result['preview_path']}",
        ])

    def _validate_sources(self):
        self._run_background("Validating source master", self._task_validate_sources)

    def _task_validate_sources(self):
        result, report = validate_sources(DEFAULT_SOURCE_MASTER)
        return report, result.ok

    def _load_sources_view(self):
        try:
            _, report = build_fetch_plan(DEFAULT_SOURCE_MASTER, priority=self.priority_var.get())
        except Exception:
            report = traceback.format_exc()
        if hasattr(self, "sources_box"):
            self._set_text(self.sources_box, report, disabled=True)

    def _export_sources(self):
        target = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export source master",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if not target:
            return
        Path(target).write_text(DEFAULT_SOURCE_MASTER.read_text(encoding="utf-8-sig"), encoding="utf-8")
        self.status_var.set("Source CSV exported")

    def _open_add_source_dialog(self):
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Add Manual Source")
        dialog.geometry("560x620")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.grid_columnconfigure(1, weight=1)

        variables = {
            "Source Name": ctk.StringVar(),
            "Website": ctk.StringVar(),
            "Country": ctk.StringVar(value="Global"),
            "Language": ctk.StringVar(value="EN"),
            "Category": ctk.StringVar(value="Shipping News"),
            "Priority": ctk.StringVar(value="P2"),
            "RSS": ctk.StringVar(value="Unknown"),
            "Crawl Method": ctk.StringVar(value="HTML"),
            "Copyright Risk": ctk.StringVar(value="Medium"),
            "AI Summary Enabled": ctk.StringVar(value="Yes"),
        }
        rows = [
            ("Source Name", "entry"),
            ("Website", "entry"),
            ("Country", "entry"),
            ("Language", "entry"),
            ("Category", "entry"),
            ("Priority", "option"),
            ("RSS", "option"),
            ("Crawl Method", "entry"),
            ("Copyright Risk", "option"),
            ("AI Summary Enabled", "option"),
        ]
        for row_index, (label, kind) in enumerate(rows):
            ctk.CTkLabel(dialog, text=label, width=150, anchor="w").grid(row=row_index, column=0, sticky="w", padx=16, pady=8)
            if kind == "option":
                values = sorted(ALLOWED_VALUES.get(label, {"Yes", "No"}))
                ctk.CTkOptionMenu(dialog, variable=variables[label], values=values, width=180).grid(
                    row=row_index, column=1, sticky="w", padx=10, pady=8
                )
            else:
                ctk.CTkEntry(dialog, textvariable=variables[label], width=320).grid(
                    row=row_index, column=1, sticky="w", padx=10, pady=8
                )

        note = ctk.CTkLabel(
            dialog,
            text="Manual sources are appended to NEWS_SOURCE_MASTER.csv and validated before use.",
            text_color=("gray35", "gray75"),
            anchor="w",
            wraplength=500,
        )
        note.grid(row=len(rows), column=0, columnspan=2, sticky="ew", padx=16, pady=(8, 4))

        actions = ctk.CTkFrame(dialog, fg_color="transparent")
        actions.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="e", padx=16, pady=18)
        ctk.CTkButton(actions, text="Cancel", width=90, fg_color=("gray70", "gray30"), command=dialog.destroy).pack(
            side="right", padx=(8, 0)
        )
        ctk.CTkButton(
            actions,
            text="Add Source",
            width=120,
            command=lambda: self._save_manual_source(dialog, variables),
        ).pack(side="right")

    def _save_manual_source(self, dialog, variables):
        values = {key: variable.get().strip() for key, variable in variables.items()}
        if not values["Source Name"] or not values["Website"]:
            messagebox.showwarning("Missing Source", "Source Name and Website are required.", parent=dialog)
            return
        try:
            row = append_manual_source(DEFAULT_SOURCE_MASTER, values)
        except ValueError as exc:
            messagebox.showwarning("Invalid Source", str(exc), parent=dialog)
            return
        result, report = validate_sources(DEFAULT_SOURCE_MASTER)
        if not result.ok:
            self._set_text(self.sources_box, report, disabled=True)
            messagebox.showwarning("Source Added With Validation Errors", report, parent=dialog)
            return
        dialog.destroy()
        self.status_var.set(f"Added source {row['ID']}")
        self._load_sources_view()

    def _open_telegram_dialog(self):
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Telegram Setup")
        dialog.geometry("660x390")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.grid_columnconfigure(1, weight=1)

        self._dialog_entry(dialog, "Bot token", self.telegram_bot_token_var, 0, width=360, show="*")
        self._dialog_entry(dialog, "Chat IDs", self.telegram_chat_id_var, 1, width=360)
        self._dialog_entry(dialog, "Intro text", self.telegram_intro_text_var, 2, width=420)

        help_text = (
            "Create a bot with BotFather, copy the bot token, add the bot to your channel/group, "
            "then paste chat IDs here. Intro text is sent once before photos. "
            "Use {date} for dd/mm/yyyy or {datetime} for dd/mm/yyyy HH:MM."
        )
        ctk.CTkLabel(dialog, text=help_text, text_color=("gray35", "gray75"), wraplength=560, justify="left").grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=(12, 4)
        )

        actions = ctk.CTkFrame(dialog, fg_color="transparent")
        actions.grid(row=4, column=0, columnspan=2, sticky="e", padx=16, pady=18)
        ctk.CTkButton(actions, text="Test Bot", width=100, command=self._test_telegram_bot).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Test Chat", width=100, command=self._test_telegram_chat).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Send Selected Source", width=150, command=self._send_latest_cards).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(actions, text="Save", width=90, command=lambda: self._save_telegram_dialog(dialog)).pack(
            side="right"
        )
        ctk.CTkButton(actions, text="Close", width=90, fg_color=("gray70", "gray30"), command=dialog.destroy).pack(
            side="right", padx=(0, 8)
        )

    def _dialog_entry(self, parent, label, variable, row, width=120, show=None):
        ctk.CTkLabel(parent, text=label, width=150, anchor="w").grid(row=row, column=0, sticky="w", padx=16, pady=8)
        ctk.CTkEntry(parent, textvariable=variable, width=width, show=show).grid(
            row=row, column=1, sticky="w", padx=10, pady=8
        )

    def _save_telegram_dialog(self, dialog):
        self._save_settings()
        dialog.destroy()

    def _test_telegram_bot(self):
        token = self.telegram_bot_token_var.get().strip()
        if not token:
            messagebox.showwarning("Telegram", "Bot token is empty.", parent=self.root)
            return
        self._run_background("Checking Telegram bot", lambda: self._task_check_telegram_bot(token))

    def _task_check_telegram_bot(self, token):
        try:
            user = check_bot(token)
            return f"Telegram bot OK: @{user.get('username') or user.get('first_name')}", True
        except Exception as exc:
            return f"Telegram bot check failed: {exc}", False

    def _test_telegram_chat(self):
        token = self.telegram_bot_token_var.get().strip()
        chat_ids = self._telegram_chat_ids()
        if not token or not chat_ids:
            messagebox.showwarning("Telegram", "Bot token or Chat IDs is empty.", parent=self.root)
            return
        self._run_background("Checking Telegram chats", lambda: self._task_check_telegram_chats(token, chat_ids))

    def _task_check_telegram_chats(self, token, chat_ids):
        lines = []
        ok = True
        for chat_id in chat_ids:
            try:
                chat = check_chat(token, chat_id)
                title = chat.get("title") or chat.get("username") or chat.get("first_name") or chat.get("id")
                lines.append(f"{chat_id}: OK - {title}")
            except Exception as exc:
                ok = False
                lines.append(f"{chat_id}: FAILED - {exc}")
        return "\n".join(lines), ok

    def _send_latest_cards(self):
        self._save_settings()
        self._run_background("Sending selected source cards to Telegram", self._task_send_latest_cards)

    def _task_send_latest_cards(self):
        result = self._retry_gui_step(
            "generate_selected_source_image_cards",
            self._generate_selected_source_cards_result,
        )
        output, ok = self._retry_gui_step(
            "send_telegram",
            lambda: self._task_send_cards(result["cards_result"]["cards"], result.get("brief_label")),
        )
        return f"{self._format_selected_source_cards_result(result)}\n\n{output}", ok

    def _task_send_cards(self, cards, brief_label=None):
        token = self.telegram_bot_token_var.get().strip()
        chat_ids = self._telegram_chat_ids()
        if not token or not chat_ids:
            return "Telegram skipped: Bot token or Chat IDs is empty.", False
        lines = []
        ok = True
        total_sent = 0
        successful_chat_ids = []
        intro_text = self._telegram_intro_text(brief_label)
        for chat_id in chat_ids:
            try:
                if intro_text:
                    send_message(token, chat_id, intro_text)
                sent = send_photos(token, chat_id, cards)
                total_sent += len(sent)
                successful_chat_ids.append(chat_id)
                intro_label = "with intro text" if intro_text else "without intro text"
                lines.append(f"{chat_id}: sent photos {len(sent)} {intro_label}")
            except Exception as exc:
                ok = False
                lines.append(f"{chat_id}: send failed - {exc}")
        if successful_chat_ids:
            saved = mark_items_published(cards, telegram_chat_id=", ".join(successful_chat_ids))
            lines.append(f"Published ledger updated: {saved} items")
        lines.insert(0, f"Telegram total sent photos: {total_sent}")
        return "\n".join(lines), ok

    def _open_facebook_dialog(self):
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Facebook Setup")
        dialog.geometry("720x430")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.grid_columnconfigure(1, weight=1)

        self._dialog_entry(dialog, "Page ID", self.facebook_page_id_var, 0, width=360)
        self._dialog_entry(dialog, "Page access token", self.facebook_page_access_token_var, 1, width=420, show="*")
        self._dialog_entry(dialog, "Caption template", self.facebook_intro_text_var, 2, width=520)
        ctk.CTkCheckBox(dialog, text="Dry-run only", variable=self.facebook_dry_run_var).grid(
            row=3, column=1, sticky="w", padx=10, pady=8
        )

        help_text = (
            "Use a Facebook Page access token with publishing permission. "
            "Dry-run creates cards and shows the payload without posting."
        )
        ctk.CTkLabel(dialog, text=help_text, text_color=("gray35", "gray75"), wraplength=620, justify="left").grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=16, pady=(12, 4)
        )

        actions = ctk.CTkFrame(dialog, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=2, sticky="e", padx=16, pady=18)
        ctk.CTkButton(actions, text="Test Page", width=100, command=self._test_facebook_page).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Preview", width=100, command=self._preview_facebook_post).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Post", width=90, command=self._post_facebook_now).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Save", width=90, command=lambda: self._save_facebook_dialog(dialog)).pack(
            side="right"
        )
        ctk.CTkButton(actions, text="Close", width=90, fg_color=("gray70", "gray30"), command=dialog.destroy).pack(
            side="right", padx=(0, 8)
        )

    def _save_facebook_dialog(self, dialog):
        self._save_settings()
        dialog.destroy()

    def _test_facebook_page(self):
        page_id = self.facebook_page_id_var.get().strip()
        token = self.facebook_page_access_token_var.get().strip()
        if not page_id or not token:
            messagebox.showwarning("Facebook", "Page ID or Page access token is empty.", parent=self.root)
            return
        self._run_background("Checking Facebook page", lambda: self._task_check_facebook_page(page_id, token))

    def _task_check_facebook_page(self, page_id, token):
        token_warning = self._facebook_token_warning(token)
        try:
            page = check_page(page_id, token)
            lines = [f"Facebook page OK: {page.get('name') or page.get('id')}"]
            if token_warning:
                lines.append(token_warning)
            return "\n".join(lines), True
        except Exception as exc:
            return "Facebook page check failed:\n" + self._format_facebook_error(exc, token), False

    def _preview_facebook_post(self):
        self._save_settings()
        self._run_background("Previewing Facebook post", lambda: self._task_preview_facebook_post())

    def _task_preview_facebook_post(self):
        result, error = self._generate_facebook_cards_or_error()
        if error:
            return error, False
        output, ok = self._task_post_facebook_cards(
            result["cards_result"]["cards"],
            result.get("brief_label"),
            dry_run=True,
        )
        return f"{self._format_selected_source_cards_result(result)}\n\n{output}", ok

    def _post_facebook_now(self):
        self._save_settings()
        self._run_background("Posting Facebook cards", lambda: self._task_post_facebook_now())

    def _task_post_facebook_now(self):
        result, error = self._generate_facebook_cards_or_error()
        if error:
            return error, False
        output, ok = self._task_post_facebook_cards(
            result["cards_result"]["cards"],
            result.get("brief_label"),
            dry_run=self.facebook_dry_run_var.get(),
        )
        return f"{self._format_selected_source_cards_result(result)}\n\n{output}", ok

    def _generate_facebook_cards_or_error(self):
        try:
            result = self._retry_gui_step(
                "generate_selected_source_image_cards",
                self._generate_selected_source_cards_result,
            )
            return result, None
        except Exception as exc:
            return None, self._format_facebook_card_generation_error(exc)

    def _format_facebook_card_generation_error(self, exc):
        message = str(exc or "").strip() or exc.__class__.__name__
        return "Facebook skipped: could not generate image cards.\n" + message

    def _task_post_facebook_cards(self, cards, brief_label=None, dry_run=None):
        page_id = self.facebook_page_id_var.get().strip()
        token = self.facebook_page_access_token_var.get().strip()
        if not page_id or not token:
            return "Facebook skipped: Page ID or Page access token is empty.", False

        safety = validate_cards_publish_safety(cards)
        if not safety["ready"]:
            return "Facebook skipped: publish safety failed.\n" + self._format_errors(safety["errors"]), False

        effective_dry_run = self.facebook_dry_run_var.get() if dry_run is None else bool(dry_run)
        message = self._facebook_intro_text(brief_label)
        try:
            result = publish_photo_post(page_id, token, cards, message, dry_run=effective_dry_run)
        except Exception as exc:
            uploaded = getattr(exc, "uploaded_photo_ids", None)
            suffix = f"\nUploaded photo IDs before failure: {', '.join(uploaded)}" if uploaded else ""
            return f"Facebook post failed:\n{self._format_facebook_error(exc, token)}{suffix}", False

        if result.get("dry_run"):
            lines = [
                "Facebook dry-run: no post created",
                f"Page ID: {result['page_id']}",
                f"Photos: {len(result['image_paths'])}",
                "Caption:",
                result.get("message") or "",
                "Images:",
            ]
            lines.extend(f"- {path}" for path in result["image_paths"])
            return "\n".join(lines), True

        post_id = result.get("post_id") or ""
        saved = mark_items_published(cards, facebook_page_id=page_id, facebook_post_id=post_id)
        mode = "fallback single-photo posts" if result.get("fallback") else "multi-photo post"
        lines = [
            f"Facebook published: {mode}",
            f"Post ID: {post_id or 'unknown'}",
            f"Uploaded photos: {len(result.get('uploaded_photo_ids') or [])}",
            f"Published ledger updated: {saved} items",
        ]
        return "\n".join(lines), True

    def _check_api_key(self):
        self._save_settings()
        provider = self.provider_var.get()
        api_key = self.api_key_var.get().strip()
        if provider == "mock":
            messagebox.showinfo("API Key", "Mock mode does not need an API key.", parent=self.root)
            return
        if not api_key:
            messagebox.showwarning("API Key", "API key is empty.", parent=self.root)
            return
        self._run_background("Checking API key", lambda: self._task_check_api_key(provider, api_key))

    def _task_check_api_key(self, provider, api_key):
        try:
            if provider == "openai":
                response = requests.get(
                    "https://api.openai.com/v1/models",
                    timeout=20,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            else:
                response = requests.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    timeout=20,
                    params={"key": api_key},
                )
            response.raise_for_status()
            return f"{provider} API key is valid.", True
        except Exception as exc:
            return f"{provider} API key check failed: {exc}", False

    def _scheduler_tick(self):
        if self.auto_run_var.get() and not self.task_running:
            due_label, due_key = self._due_schedule()
            if due_label and due_key != self.last_schedule_key:
                self.last_schedule_key = due_key
                self.scan_label_var.set(due_label)
                self._run_background(f"Scheduled {due_label} scan", lambda: self._task_run_scan(due_label))
        self._update_next_run_label()
        self.root.after(30000, self._scheduler_tick)

    def _due_schedule(self):
        now = self._schedule_now()
        for time_text in self._schedule_times():
            hour, minute = [int(part) for part in time_text.split(":", 1)]
            scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            age_seconds = (now - scheduled).total_seconds()
            if 0 <= age_seconds < 60:
                label = self._scan_label_for_time(scheduled)
                return label, f"{scheduled.date()}-{time_text}"
        return None, None

    def _wait_for_sheet_if_needed(self, source_mode):
        if str(source_mode or "").strip().lower() != "sheet":
            return {}
        sheet_url = self.sheet_url_var.get().strip()
        if not sheet_url:
            return {}

        expected_label = self._current_scan_label()
        while True:
            self._checkpoint("wait for Google Sheet L1")
            now = self._vietnam_now()
            status = get_sheet_run_status(sheet_url)
            marker = status.get("run_marker", "")
            sheet_label = status.get("run_label", "")
            ready = sheet_label == expected_label
            wait_status = {
                "run_marker": marker,
                "run_label": sheet_label,
                "expected_run_label": expected_label,
                "vietnam_now": now.strftime("%Y-%m-%d %H:%M:%S +07"),
                "sheet_ready": ready,
            }
            if ready:
                return wait_status

            message = (
                "Google Sheet is not ready for the current brief window.\n"
                f"Vietnam time: {wait_status['vietnam_now']} ({expected_label})\n"
                f"Sheet L1: {marker} ({sheet_label})\n"
                "Waiting 60 seconds before checking again."
            )
            logger.info(message.replace("\n", " "))
            if hasattr(self, "root") and hasattr(self, "output_box"):
                self.root.after(0, self._set_text, self.output_box, message, True)
                self.root.after(0, self.summary_var.set, "Waiting for Google Sheet L1")
            self._sleep_with_controls(60)

    def _update_next_run_label(self):
        if hasattr(self, "auto_run_var") and not self.auto_run_var.get():
            self.next_run_var.set("Auto run: off")
            return
        next_run = self._next_scheduled_run()
        if next_run:
            self.next_run_var.set(f"Next run: {next_run.strftime('%Y-%m-%d %H:%M')} UTC{self._timezone_offset_text()}")

    def _next_scheduled_run(self):
        now = self._schedule_now()
        upcoming = []
        for time_text in self._schedule_times():
            hour, minute = [int(part) for part in time_text.split(":", 1)]
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            upcoming.append(candidate)
        if upcoming:
            return min(upcoming)
        return None

    def _retry_gui_step(self, name, action):
        attempts = self._int_var(self.retry_attempts_var, 2)
        last_error = None
        for attempt in range(1, attempts + 1):
            self._checkpoint(name)
            try:
                return action()
            except Exception as exc:
                last_error = exc
                logger.warning("GUI step %s attempt %s/%s failed: %s", name, attempt, attempts, exc)
                if attempt < attempts:
                    self._sleep_with_controls(min(30, 2 * attempt))
        raise last_error

    def _toggle_pause(self):
        if not self.task_running:
            return
        self.pause_requested = not self.pause_requested
        if self.pause_requested:
            self.pause_button.configure(text="Resume")
            self.status_var.set("Paused after current step")
        else:
            self.pause_button.configure(text="Pause")
            self.status_var.set("Running...")

    def _request_stop(self):
        if not self.task_running:
            return
        self.stop_requested = True
        self.pause_requested = False
        self.pause_button.configure(text="Pause")
        self.status_var.set("Stopping after current step")

    def _checkpoint(self, step_name):
        if self.stop_requested:
            raise RuntimeError(f"Stopped by user before {step_name}")
        while self.pause_requested and not self.stop_requested:
            time.sleep(0.2)
        if self.stop_requested:
            raise RuntimeError(f"Stopped by user before {step_name}")

    def _sleep_with_controls(self, seconds):
        end_time = time.time() + seconds
        while time.time() < end_time:
            self._checkpoint("retry wait")
            time.sleep(min(0.2, end_time - time.time()))

    def _run_background(self, title, task):
        if self.task_running:
            return
        self.task_running = True
        self.pause_requested = False
        self.stop_requested = False
        self._set_actions_enabled(False)
        self.pause_button.configure(state="normal", text="Pause")
        self.stop_button.configure(state="normal")
        self.status_var.set("Running...")
        self.summary_var.set(title)
        self._set_text(self.output_box, f"{title}...\n", disabled=True)
        logger.info("GUI task started: %s", title)
        threading.Thread(target=self._worker, args=(title, task), daemon=True).start()

    def _worker(self, title, task):
        try:
            output, ok = task()
        except RuntimeError as exc:
            if "Stopped by user" in str(exc):
                output = str(exc)
                ok = None
            else:
                logger.exception("GUI task failed: %s", title)
                output = traceback.format_exc()
                ok = False
        except Exception:
            logger.exception("GUI task failed: %s", title)
            output = traceback.format_exc()
            ok = False
        self.root.after(0, self._finish_task, title, output, ok)

    def _finish_task(self, title, output, ok):
        self.task_running = False
        self.pause_requested = False
        self.stop_requested = False
        self._set_actions_enabled(True)
        self.pause_button.configure(state="disabled", text="Pause")
        self.stop_button.configure(state="disabled")
        status_text = "Stopped" if ok is None else ("Done" if ok else "Failed")
        self.status_var.set(status_text)
        self.summary_var.set(f"{title}: {status_text}")
        self._set_text(self.output_box, output, disabled=True)
        self._append_output(f"{title}: {status_text}")
        self._refresh_all()
        if "image cards" in title.lower() or "combined cards" in title.lower() or "combined sources" in title.lower():
            self._set_text(self.visual_box, output, disabled=True)
        if ok is False:
            messagebox.showwarning("Task Failed", self._task_failure_popup_message(title, output), parent=self.root)

    def _set_actions_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for button in self.action_buttons:
            button.configure(state=state)

    def _refresh_all(self):
        self._refresh_dashboard()
        self._load_latest_brief()
        self._load_log_file()
        self._update_next_run_label()

    def _refresh_dashboard(self):
        try:
            init_db(DEFAULT_DB_PATH)
            counts = {
                "sources": count_rows("sources"),
                "articles": count_rows("articles"),
                "summaries": count_rows("article_summaries"),
                "briefs": count_rows("briefs"),
                "fetch_logs": count_rows("fetch_logs"),
                "trends": count_rows("trend_keywords"),
            }
        except Exception as exc:
            logger.warning("Dashboard refresh failed: %s", exc)
            counts = {key: "!" for key in self.stat_labels}
        for key, value in counts.items():
            self.stat_labels[key].configure(text=str(value))

    def _load_latest_brief(self):
        text = LATEST_BRIEF_MD.read_text(encoding="utf-8", errors="replace") if LATEST_BRIEF_MD.exists() else "No latest brief yet."
        if hasattr(self, "latest_brief_box"):
            self._set_text(self.latest_brief_box, text, disabled=True)

    def _load_log_file(self):
        text = "No log file yet."
        if LOG_PATH.exists():
            text = "\n".join(LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-300:])
        if hasattr(self, "log_box"):
            self._set_text(self.log_box, text, disabled=True)

    def _open_path(self, path):
        path = Path(path)
        if not path.exists():
            messagebox.showwarning("Not Found", f"Path does not exist:\n{path}", parent=self.root)
            return
        os.startfile(path)

    def _set_text(self, widget, text, disabled):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.see("1.0")
        if disabled:
            widget.configure(state="disabled")

    def _append_output(self, message):
        if not hasattr(self, "output_box"):
            return
        self.output_box.configure(state="normal")
        self.output_box.insert("end", f"{message}\n")
        self.output_box.see("end")
        self.output_box.configure(state="disabled")

    def _schedule_times(self):
        raw = self.schedule_times_var.get() if hasattr(self, "schedule_times_var") else ""
        values = []
        for item in raw.replace(";", ",").split(","):
            text = item.strip()
            if not text:
                continue
            try:
                hour, minute = [int(part) for part in text.split(":", 1)]
            except (ValueError, TypeError):
                continue
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                values.append(f"{hour:02d}:{minute:02d}")
        return values or ["07:15", "19:15"]

    def _timezone_options(self):
        return [f"{offset:+d}" for offset in range(-12, 15)]

    def _timezone_offset_text(self):
        raw = self.timezone_offset_var.get() if hasattr(self, "timezone_offset_var") else "+7"
        try:
            offset = int(str(raw).strip().replace("UTC", ""))
        except (TypeError, ValueError):
            offset = 7
        offset = max(-12, min(14, offset))
        return f"{offset:+d}"

    def _schedule_timezone(self):
        return timezone(timedelta(hours=int(self._timezone_offset_text())))

    def _schedule_now(self):
        return datetime.now(timezone.utc).astimezone(self._schedule_timezone())

    def _vietnam_now(self):
        return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=7)))

    def _scan_label_for_time(self, scheduled_time):
        return "morning" if scheduled_time.hour < 12 else "evening"

    def _visual_settings(self):
        return {
            "source_mode": self.visual_source_mode_var.get(),
            "sheet_url": self.sheet_url_var.get().strip(),
            "sheet_limit_max": self.sheet_limit_max_var.get(),
            "sheet_limit": self._int_var(self.sheet_limit_var, 20),
            "app_limit_max": self.app_limit_max_var.get(),
            "app_limit": self._int_var(self.app_limit_var, 20),
            "card_limit_max": self.card_limit_max_var.get(),
            "card_limit": self._int_var(self.card_limit_var, 12),
            "font_family": self.font_var.get(),
            "title_size": self._int_var(self.title_size_var, 54),
            "summary_size": self._int_var(self.summary_size_var, 31),
            "impact_size": self._int_var(self.impact_size_var, 27),
            "text_color": self.text_color_var.get(),
            "accent_color": self.accent_color_var.get(),
            "watermark": self.watermark_var.get(),
            "show_title": self.show_title_var.get(),
            "show_summary": self.show_summary_var.get(),
            "show_impact": self.show_impact_var.get(),
            "show_source": self.show_source_var.get(),
            "show_url": self.show_url_var.get(),
            "show_hot_keywords": self.show_hot_keywords_var.get(),
        }

    def _provider_changed(self, value):
        self.model_var.set(self._default_model(value))
        self.api_key_var.set(self._current_api_key(value))

    def _current_api_key(self, provider=None):
        provider = provider or self.settings["ai"]["provider"]
        if provider == "openai":
            return self.env.get("OPENAI_API_KEY", "")
        if provider == "gemini":
            return self.env.get("GEMINI_API_KEY", "")
        return ""

    def _default_model(self, provider):
        if provider == "openai":
            return self.env.get("OPENAI_MODEL") or "gpt-4o-mini"
        if provider == "gemini":
            return self.env.get("GEMINI_MODEL") or "gemini-2.5-flash-lite"
        return "rule-based-mock"

    def _current_scan_label(self):
        return self._scan_label_for_time(self._vietnam_now())

    def _int_var(self, variable, default):
        try:
            return max(1, int(variable.get()))
        except (TypeError, ValueError):
            return default

    def _optional_limit(self, variable, max_variable):
        if max_variable.get():
            return None
        return self._int_var(variable, 12)

    def _float_var(self, variable, default):
        try:
            return max(0, float(variable.get()))
        except (TypeError, ValueError):
            return default

    def _var_bool(self, attr_name, default=False):
        variable = getattr(self, attr_name, None)
        if variable is None:
            return default
        try:
            return bool(variable.get())
        except AttributeError:
            return bool(variable)

    def _format_errors(self, errors):
        return "\n".join(f"- {error}" for error in errors)

    def _task_failure_popup_message(self, title, output):
        if "facebook" in str(title or "").strip().lower():
            text = self._facebook_popup_text(output)
            if text:
                return self._truncate_popup_text(text)
        return "The task did not complete successfully. See output for details."

    def _facebook_popup_text(self, output):
        text = str(output or "").strip()
        for marker in [
            "Facebook page check failed:",
            "Facebook post failed:",
            "Facebook skipped:",
        ]:
            index = text.find(marker)
            if index >= 0:
                return text[index:].strip()
        return text

    def _truncate_popup_text(self, text, max_length=900):
        text = str(text or "").strip()
        if len(text) <= max_length:
            return text
        return text[: max_length - 3].rstrip() + "..."

    def _format_facebook_error(self, exc, token=None):
        lines = [str(exc)]
        details = []
        for label, attr in [
            ("status_code", "status_code"),
            ("error_type", "error_type"),
            ("error_code", "error_code"),
        ]:
            value = getattr(exc, attr, None)
            if value not in (None, ""):
                details.append(f"{label}={value}")
        if details:
            lines.append("Facebook details: " + ", ".join(details))

        warning = self._facebook_token_warning(token)
        if warning:
            lines.append(warning)
        if getattr(exc, "error_code", None) == 190:
            lines.append(
                "Action: create a new Facebook Page access token for this Page, "
                "then update FACEBOOK_PAGE_ACCESS_TOKEN in .env or Facebook Setup."
            )
        return "\n".join(lines)

    def _facebook_token_warning(self, token):
        token_text = str(token or "").strip()
        if token_text and len(token_text) < 50:
            return "Warning: Facebook Page access token looks too short to be valid."
        return ""

    def _initial_telegram_chat_ids(self, publish):
        chat_ids = publish.get("telegram_chat_ids") or []
        if isinstance(chat_ids, list) and chat_ids:
            return "\n".join(str(item) for item in chat_ids if str(item).strip())
        return str(publish.get("telegram_chat_id") or "")

    def _telegram_chat_ids(self):
        raw = self.telegram_chat_id_var.get()
        normalized = raw.replace(";", ",").replace("\n", ",")
        chat_ids = []
        for item in normalized.split(","):
            value = item.strip()
            if value and value not in chat_ids:
                chat_ids.append(value)
        return chat_ids

    def _brief_label_text(self, brief_label=None):
        return facebook_brief_label_text(brief_label or self._current_scan_label(), now=self._vietnam_now())

    def _telegram_intro_text(self, brief_label=None):
        template = (self.telegram_intro_text_var.get() or "{date}").strip()
        if not template:
            return ""
        now = self._vietnam_now()
        brief_text = self._brief_label_text(brief_label)
        rendered = (
            template.replace("{date}", now.strftime("%d/%m/%Y"))
            .replace("{datetime}", now.strftime("%d/%m/%Y %H:%M"))
            .replace("{brief_label}", brief_text)
        )
        if "{brief_label}" not in template and brief_text not in rendered:
            rendered = f"{brief_text}\n{rendered}"
        return rendered

    def _facebook_intro_text(self, brief_label=None):
        return render_facebook_intro_text(
            self.facebook_intro_text_var.get(),
            brief_label=brief_label,
            now=self._vietnam_now(),
        )

    def _check_update_on_startup(self):
        self._start_update_check(is_manual=False)

    def _check_update(self):
        self._start_update_check(is_manual=True)

    def _start_update_check(self, is_manual):
        if self.update_check_running:
            return
        self.update_check_running = True
        if is_manual:
            self.status_var.set("Checking for updates...")
        threading.Thread(target=self._run_update_check, args=(is_manual,), daemon=True).start()

    def _run_update_check(self, is_manual):
        current_version = self.metadata.get("version", "0.0.0")
        latest_json_url = get_latest_json_url(self.metadata)
        if not latest_json_url:
            result = {"status": "missing_config", "message": "Update check skipped: latest.json URL is not configured"}
        else:
            result = get_update_status(current_version, latest_json_url)
        self.root.after(0, self._finish_update_check, result, is_manual)

    def _finish_update_check(self, result, is_manual):
        self.update_check_running = False
        message = result.get("message", "Update check finished")
        self.status_var.set(message)
        if is_manual or result.get("status") == "available":
            self._append_output(message)
        if result.get("status") == "available":
            self._prompt_update(result.get("latest", {}))

    def _prompt_update(self, latest_data):
        latest_version = latest_data.get("version", "unknown")
        changelog = latest_data.get("changelog") or ["No changelog provided."]
        if isinstance(changelog, list):
            change_text = "\n".join(f"- {item}" for item in changelog[:3])
        else:
            change_text = f"- {changelog}"
        prompt = f"A new version is available: {latest_version}\n\nWhat's new:\n{change_text}\n\nDo you want to update now?"
        if messagebox.askyesno("Update Available", prompt, parent=self.root):
            self._download_and_launch_update(latest_data)

    def _download_and_launch_update(self, latest_data):
        download_url = latest_data.get("download_url")
        if not download_url:
            self.status_var.set("Update skipped: download URL is missing")
            return
        threading.Thread(target=self._run_update_download, args=(download_url,), daemon=True).start()

    def _run_update_download(self, download_url):
        try:
            new_exe_path = download_update(download_url, ROOT_DIR / "temp", self.metadata.get("exe_name", "BV-App.exe"))
            self.root.after(0, self._launch_downloaded_update, new_exe_path)
        except Exception as exc:
            self.root.after(0, self._show_update_download_error, str(exc))

    def _launch_downloaded_update(self, new_exe_path):
        try:
            launch_updater(new_exe_path)
            self.root.after(500, self.root.destroy)
        except Exception as exc:
            self._show_update_download_error(str(exc))

    def _show_update_download_error(self, error):
        message = f"Update failed: {error}"
        self.status_var.set(message)
        messagebox.showerror("Update Failed", message, parent=self.root)

    def run(self):
        self.root.mainloop()
