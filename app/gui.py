import threading
from tkinter import messagebox

import customtkinter as ctk

from app.config import ROOT_DIR, get_latest_json_url, load_version
from app.logger import logger
from app.services.business_logic import run_business_task
from app.update_manager import download_update, get_update_status
from app.updater_launcher import launch_updater


class AppGUI:
    def __init__(self):
        metadata = load_version()

        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk()
        self.root.title(metadata.get("app_name", "BV Application"))
        self.root.geometry("1000x700")
        self.root.minsize(800, 520)

        self.status_var = ctk.StringVar(value="Ready")
        self.metadata = metadata
        self.update_check_running = False

        self._build_layout(metadata)
        self._append_log("GUI opened")
        self.root.after(800, self._check_update_on_startup)

    def _build_layout(self, metadata):
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self.root, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(
            header,
            text=metadata.get("app_name", "BV Application"),
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        title.grid(row=0, column=0, sticky="w", padx=20, pady=(16, 4))

        version = ctk.CTkLabel(
            header,
            text=f"Version {metadata.get('version', '0.0.0')}",
        )
        version.grid(row=1, column=0, sticky="w", padx=20, pady=(0, 16))

        body = ctk.CTkFrame(self.root, corner_radius=0)
        body.grid(row=1, column=0, sticky="nsew", padx=20, pady=20)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

        actions = ctk.CTkFrame(body)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 12))

        run_button = ctk.CTkButton(actions, text="Run", command=self._run_task)
        run_button.pack(side="left", padx=12, pady=12)

        update_button = ctk.CTkButton(actions, text="Check Update", command=self._check_update)
        update_button.pack(side="left", padx=(0, 12), pady=12)

        status = ctk.CTkLabel(actions, textvariable=self.status_var)
        status.pack(side="left", padx=12)

        self.output_box = ctk.CTkTextbox(body)
        self.output_box.grid(row=1, column=0, sticky="nsew")
        self.output_box.insert("end", "Application log\n")
        self.output_box.configure(state="disabled")

    def _run_task(self):
        result = run_business_task()
        self.status_var.set(result)
        logger.info(result)
        self._append_log(result)

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
            self._append_log("Checking for updates...")

        thread = threading.Thread(target=self._run_update_check, args=(is_manual,), daemon=True)
        thread.start()

    def _run_update_check(self, is_manual):
        current_version = self.metadata.get("version", "0.0.0")
        latest_json_url = get_latest_json_url(self.metadata)

        if not latest_json_url:
            message = "Update check skipped: latest.json URL is not configured"
            logger.warning(message)
        else:
            result = get_update_status(current_version, latest_json_url)
            message = result.get("message", "Update check finished")

        self.root.after(0, self._finish_update_check, result if latest_json_url else {"status": "missing_config", "message": message}, is_manual)

    def _finish_update_check(self, result, is_manual):
        self.update_check_running = False
        message = result.get("message", "Update check finished")
        self.status_var.set(message)
        logger.info("Update check: %s", message)

        if is_manual or result.get("status") == "available":
            self._append_log(message)

        if result.get("status") == "available":
            self._prompt_update(result.get("latest", {}))

    def _prompt_update(self, latest_data):
        latest_version = latest_data.get("version", "unknown")
        changelog = self._format_changelog(latest_data.get("changelog", []))
        prompt = (
            f"A new version is available: {latest_version}\n\n"
            f"What's new:\n{changelog}\n\n"
            "Do you want to update now?"
        )

        if messagebox.askyesno("Update Available", prompt, parent=self.root):
            self._download_and_launch_update(latest_data)

    def _download_and_launch_update(self, latest_data):
        download_url = latest_data.get("download_url")
        if not download_url:
            message = "Update skipped: download URL is missing"
            logger.warning(message)
            self.status_var.set(message)
            self._append_log(message)
            return

        self.status_var.set("Downloading update...")
        self._append_log("Downloading update...")
        thread = threading.Thread(target=self._run_update_download, args=(download_url,), daemon=True)
        thread.start()

    def _run_update_download(self, download_url):
        try:
            new_exe_path = download_update(
                download_url,
                ROOT_DIR / "temp",
                self.metadata.get("exe_name", "BV-App.exe"),
            )
            self.root.after(0, self._launch_downloaded_update, new_exe_path)
        except Exception as exc:
            self.root.after(0, self._show_update_download_error, str(exc))

    def _launch_downloaded_update(self, new_exe_path):
        try:
            launch_updater(new_exe_path)
            message = "Update downloaded. Closing app to apply update..."
            self.status_var.set(message)
            self._append_log(message)
            self.root.after(500, self.root.destroy)
        except Exception as exc:
            self._show_update_download_error(str(exc))

    def _show_update_download_error(self, error):
        message = f"Update failed: {error}"
        logger.warning(message)
        self.status_var.set(message)
        self._append_log(message)
        messagebox.showerror("Update Failed", message, parent=self.root)

    def _format_changelog(self, changelog):
        if isinstance(changelog, list):
            items = [str(item).strip() for item in changelog if str(item).strip()]
        elif changelog:
            items = [str(changelog).strip()]
        else:
            items = ["No changelog provided."]

        return "\n".join(f"- {item}" for item in items[:3])

    def _append_log(self, message):
        self.output_box.configure(state="normal")
        self.output_box.insert("end", f"{message}\n")
        self.output_box.see("end")
        self.output_box.configure(state="disabled")

    def run(self):
        self.root.mainloop()
