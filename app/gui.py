import customtkinter as ctk

from app.config import load_latest, load_version
from app.logger import logger
from app.services.business_logic import run_business_task
from app.version_checker import is_newer_version


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

        self._build_layout(metadata)
        self._append_log("GUI opened")

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

    def _check_update(self):
        current_version = self.metadata.get("version", "0.0.0")
        latest_data = load_latest()
        latest_version = latest_data.get("version", "0.0.0")

        if is_newer_version(current_version, latest_version):
            if latest_data.get("download_url"):
                message = f"Update available: {latest_version}"
            else:
                message = f"Update {latest_version} has no download URL"
                logger.warning(message)
        else:
            message = f"Current version {current_version} is up to date"

        self.status_var.set(message)
        logger.info("Update check: %s", message)
        self._append_log(message)

    def _append_log(self, message):
        self.output_box.configure(state="normal")
        self.output_box.insert("end", f"{message}\n")
        self.output_box.see("end")
        self.output_box.configure(state="disabled")

    def run(self):
        self.root.mainloop()
