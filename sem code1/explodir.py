#!/usr/bin/env python3
from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from queue import Empty
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from explodir_core import (
    AccountRecord,
    AccountViewState,
    AppLogger,
    AutoRefreshController,
    CredentialStore,
    EventBus,
    ExplodirService,
    OpenAIAPIError,
    OpenAIAuthClient,
    SerializedRunner,
    format_remaining,
    format_reset_abs,
)


class DeviceLoginDialog:
    def __init__(
        self,
        parent: tk.Misc,
        cancel_event: threading.Event,
        start_callback,
    ) -> None:
        self.cancel_event = cancel_event
        self.start_callback = start_callback
        self.launch_url: str | None = None
        self.started = False
        self.window = tk.Toplevel(parent)
        self.window.title("Adicionar conta")
        self.window.geometry("540x360")
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.mode_var = tk.StringVar(value="device")
        self.code_var = tk.StringVar(value="----")
        self.status_var = tk.StringVar(value="A preparar autenticacao...")
        self.progress_var = tk.IntVar(value=10)

        container = ttk.Frame(self.window, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(container, text="Adicionar conta", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W)
        ttk.Label(
            container,
            text="Escolha se quer entrar por codigo ou por redirecionamento automatico.",
            wraplength=470,
        ).pack(anchor=tk.W, pady=(12, 8))

        mode_frame = ttk.Frame(container)
        mode_frame.pack(fill=tk.X, pady=(0, 8))
        self.device_radio = ttk.Radiobutton(
            mode_frame,
            text="Usar codigo",
            value="device",
            variable=self.mode_var,
            command=self._refresh_mode,
        )
        self.device_radio.pack(side=tk.LEFT)
        self.browser_radio = ttk.Radiobutton(
            mode_frame,
            text="Redirecionamento automatico",
            value="browser",
            variable=self.mode_var,
            command=self._refresh_mode,
        )
        self.browser_radio.pack(side=tk.LEFT, padx=(12, 0))

        self.code_frame = ttk.Frame(container)
        self.code_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(self.code_frame, text="Codigo", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, pady=(0, 4))
        code_row = ttk.Frame(self.code_frame)
        code_row.pack(fill=tk.X)
        self.code_label = ttk.Label(code_row, textvariable=self.code_var, font=("Consolas", 22, "bold"))
        self.code_label.pack(side=tk.LEFT)
        self.copy_button = ttk.Button(code_row, text="Copiar", command=self.copy_code, state=tk.DISABLED)
        self.copy_button.pack(side=tk.LEFT, padx=(12, 0))

        action_frame = ttk.Frame(container)
        action_frame.pack(fill=tk.X)
        self.start_button = ttk.Button(action_frame, text="Iniciar", command=self.start_login)
        self.start_button.pack(side=tk.LEFT)
        self.open_button = ttk.Button(action_frame, text="Abrir browser", command=self.open_browser, state=tk.DISABLED)
        self.open_button.pack(side=tk.LEFT)
        ttk.Button(action_frame, text="Cancelar", command=self.close).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(container, textvariable=self.status_var, wraplength=470).pack(anchor=tk.W, pady=(12, 6))
        self.progress = ttk.Progressbar(container, mode="determinate", maximum=100, variable=self.progress_var)
        self.progress.pack(fill=tk.X)

        self.info = ScrolledText(container, height=8, wrap=tk.WORD)
        self.info.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        self.info.insert(tk.END, "Aguardando inicio do login...\n")
        self.info.configure(state=tk.DISABLED)
        self._refresh_mode()

    def _refresh_mode(self) -> None:
        if self.mode_var.get() == "device":
            if not self.code_frame.winfo_manager():
                self.code_frame.pack(fill=tk.X, pady=(0, 8), before=self.start_button.master)
        elif self.code_frame.winfo_manager():
            self.code_frame.pack_forget()

    def start_login(self) -> None:
        if self.started:
            return
        if self.start_callback(self.mode_var.get()):
            self.started = True
            self.start_button.configure(state=tk.DISABLED)
            self.device_radio.configure(state=tk.DISABLED)
            self.browser_radio.configure(state=tk.DISABLED)
            self.status_var.set("A iniciar autenticacao...")

    def copy_code(self) -> None:
        code = self.code_var.get()
        if not code or code == "----":
            return
        self.window.clipboard_clear()
        self.window.clipboard_append(code)
        self.set_status("Codigo copiado para a area de transferencia.")

    def open_browser(self) -> None:
        if self.launch_url:
            webbrowser.open(self.launch_url)

    def append_info(self, text: str) -> None:
        self.info.configure(state=tk.NORMAL)
        self.info.insert(tk.END, text + "\n")
        self.info.see(tk.END)
        self.info.configure(state=tk.DISABLED)

    def set_status(self, text: str) -> None:
        self.status_var.set(text)
        self.append_info(text)

    def set_device_code(self, code: str, url: str) -> None:
        self.code_var.set(code)
        self.launch_url = url
        self.copy_button.configure(state=tk.NORMAL)
        self.open_button.configure(state=tk.NORMAL)
        self.progress_var.set(max(self.progress_var.get(), 25))

    def set_launch_url(self, url: str, open_now: bool = False) -> None:
        self.launch_url = url
        self.open_button.configure(state=tk.NORMAL)
        self.progress_var.set(max(self.progress_var.get(), 25))
        if open_now:
            self.open_browser()

    def set_progress(self, value: int) -> None:
        self.progress_var.set(max(0, min(100, value)))

    def close(self) -> None:
        self.cancel_event.set()
        self.window.destroy()

    def exists(self) -> bool:
        return bool(self.window.winfo_exists())


class ExplodirApp(tk.Tk):
    POLL_INTERVAL_MS = 150

    def __init__(self) -> None:
        super().__init__()
        self.title("Explodir - inspector tecnico")
        self.geometry("1450x860")
        self.minsize(1180, 720)

        self.bus = EventBus()
        self.logger = AppLogger(Path.cwd() / "hermes_monitor.log", emit=lambda line: self.bus.emit("log", line=line))
        self.store = CredentialStore(logger=self.logger)
        self.client = OpenAIAuthClient()
        self.service = ExplodirService(store=self.store, client=self.client, logger=self.logger)
        self.runner = SerializedRunner(self.bus)
        self.auto_refresh = AutoRefreshController(interval_seconds=10)
        self.view_state = AccountViewState()

        self.directory_var = tk.StringVar(value=str(Path.cwd()))
        self.filter_text_var = tk.StringVar()
        self.filter_status_var = tk.StringVar(value="Todos")
        self.filter_workspace_var = tk.StringVar(value="Todos")
        self.auto_refresh_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Pronto.")
        self.count_var = tk.StringVar(value="0 conta(s)")

        self.device_dialog: DeviceLoginDialog | None = None
        self.device_cancel_event: threading.Event | None = None
        self.status_tags = {"OK", "RENOVADO", "ESGOTADO", "EXPIRADO", "MORTO", "ERRO_REDE", "SEM_TOKEN"}
        self.column_map = {
            "source": "Ficheiro",
            "label": "Label",
            "status": "Status",
            "http": "HTTP",
            "five_hour": "Cota 5H",
            "reset5h": "Reset 5H",
            "weekly": "Semanal",
            "workspace": "Workspace",
            "email": "Email",
            "expiry": "Expira",
        }

        self._build_ui()
        self._bind_events()
        self._build_context_menu()
        self._refresh_controls()
        self.after(self.POLL_INTERVAL_MS, self._process_bus)
        self.queue_scan("manual")

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        toolbar = ttk.Frame(self, padding=(12, 12, 12, 8))
        toolbar.grid(row=0, column=0, sticky="nsew")
        toolbar.columnconfigure(1, weight=1)

        ttk.Label(toolbar, text="Diretorio ativo").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.directory_entry = ttk.Entry(toolbar, textvariable=self.directory_var)
        self.directory_entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(toolbar, text="Escolher...", command=self.choose_directory).grid(row=0, column=2, padx=(8, 0))
        self.refresh_button = ttk.Button(toolbar, text="Atualizar", command=lambda: self.queue_scan("manual"))
        self.refresh_button.grid(row=0, column=3, padx=(8, 0))
        ttk.Checkbutton(
            toolbar,
            text="Autoatualizar",
            variable=self.auto_refresh_var,
            command=self.toggle_auto_refresh,
        ).grid(row=0, column=4, padx=(12, 0))
        self.renew_selected_button = ttk.Button(toolbar, text="Renovar selecionada", command=self.renew_selected)
        self.renew_selected_button.grid(row=0, column=5, padx=(8, 0))
        self.renew_all_button = ttk.Button(toolbar, text="Renovar renovaveis", command=self.renew_renewables)
        self.renew_all_button.grid(row=0, column=6, padx=(8, 0))
        self.remove_selected_button = ttk.Button(toolbar, text="Remover selecionada", command=self.remove_selected)
        self.remove_selected_button.grid(row=0, column=7, padx=(8, 0))
        self.add_account_button = ttk.Button(toolbar, text="Adicionar conta", command=self.add_account)
        self.add_account_button.grid(row=0, column=8, padx=(8, 0))

        filters = ttk.Frame(self, padding=(12, 0, 12, 8))
        filters.grid(row=1, column=0, sticky="ew")
        for index in range(6):
            filters.columnconfigure(index, weight=1 if index in {1, 3, 5} else 0)

        ttk.Label(filters, text="Filtro").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(filters, textvariable=self.filter_text_var).grid(row=0, column=1, sticky="ew", padx=(0, 12))
        ttk.Label(filters, text="Status").grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.status_filter = ttk.Combobox(filters, textvariable=self.filter_status_var, state="readonly", values=["Todos"])
        self.status_filter.grid(row=0, column=3, sticky="ew", padx=(0, 12))
        ttk.Label(filters, text="Workspace").grid(row=0, column=4, sticky="w", padx=(0, 6))
        self.workspace_filter = ttk.Combobox(filters, textvariable=self.filter_workspace_var, state="readonly", values=["Todos"])
        self.workspace_filter.grid(row=0, column=5, sticky="ew")

        paned = ttk.Panedwindow(self, orient=tk.VERTICAL)
        paned.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))

        table_frame = ttk.Frame(paned)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table_frame,
            columns=tuple(self.column_map.keys()),
            show="headings",
            selectmode="browse",
        )
        for column, label in self.column_map.items():
            width = 120
            if column in {"source", "email"}:
                width = 180
            if column == "workspace":
                width = 160
            if column == "label":
                width = 150
            self.tree.heading(column, text=label, command=lambda col=column: self.sort_by(col))
            self.tree.column(column, width=width, anchor=tk.W if column not in {"http", "five_hour", "weekly", "reset5h", "expiry"} else tk.CENTER)
        self.tree.tag_configure("OK", foreground="#167a2b")
        self.tree.tag_configure("RENOVADO", foreground="#167a2b")
        self.tree.tag_configure("ESGOTADO", foreground="#a15d00")
        self.tree.tag_configure("EXPIRADO", foreground="#8c2f39")
        self.tree.tag_configure("MORTO", foreground="#8c2f39")
        self.tree.tag_configure("ERRO_REDE", foreground="#8c2f39")
        self.tree.tag_configure("SEM_TOKEN", foreground="#8c2f39")
        yscroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        paned.add(table_frame, weight=4)

        lower = ttk.Panedwindow(paned, orient=tk.HORIZONTAL)
        detail_frame = ttk.Frame(lower, padding=8)
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(1, weight=1)
        ttk.Label(detail_frame, text="Detalhes", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.detail_text = ScrolledText(detail_frame, wrap=tk.WORD, height=12)
        self.detail_text.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.detail_text.configure(state=tk.DISABLED)
        lower.add(detail_frame, weight=2)

        log_frame = ttk.Frame(lower, padding=8)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        ttk.Label(log_frame, text="Logs", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.log_text = ScrolledText(log_frame, wrap=tk.WORD, height=12)
        self.log_text.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.log_text.configure(state=tk.DISABLED)
        lower.add(log_frame, weight=2)
        paned.add(lower, weight=2)

        status_bar = ttk.Frame(self, padding=(12, 0, 12, 12))
        status_bar.grid(row=3, column=0, sticky="ew")
        status_bar.columnconfigure(0, weight=1)
        ttk.Label(status_bar, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Label(status_bar, textvariable=self.count_var).grid(row=0, column=1, sticky="e")

    def _bind_events(self) -> None:
        self.tree.bind("<<TreeviewSelect>>", self.on_row_selected)
        self.tree.bind("<Button-3>", self.show_row_menu)
        self.filter_text_var.trace_add("write", lambda *_: self.apply_filters())
        self.status_filter.bind("<<ComboboxSelected>>", lambda _event: self.apply_filters())
        self.workspace_filter.bind("<<ComboboxSelected>>", lambda _event: self.apply_filters())
        self.directory_entry.bind("<Return>", lambda _event: self.change_directory_from_entry())
        self.bind("<Delete>", lambda _event: self.remove_selected())

    def _build_context_menu(self) -> None:
        self.row_menu = tk.Menu(self, tearoff=False)
        self.row_menu.add_command(label="Renovar selecionada", command=self.renew_selected)
        self.row_menu.add_command(label="Remover selecionada", command=self.remove_selected)

    def change_directory_from_entry(self) -> None:
        self._set_directory(Path(self.directory_var.get()))
        self.queue_scan("manual")

    def choose_directory(self) -> None:
        initial_dir = self.directory_var.get() or str(Path.cwd())
        selected = filedialog.askdirectory(initialdir=initial_dir)
        if not selected:
            return
        self._set_directory(Path(selected))
        self.queue_scan("manual")

    def _set_directory(self, directory: Path) -> None:
        resolved = directory.expanduser().resolve()
        self.directory_var.set(str(resolved))
        self.logger.set_log_file(resolved / "hermes_monitor.log")

    def toggle_auto_refresh(self) -> None:
        enabled = self.auto_refresh_var.get()
        self.auto_refresh.set_enabled(enabled)
        if enabled:
            self.status_var.set("Autoatualizar ligado.")
        else:
            self.status_var.set("Autoatualizar desligado.")
        self._maybe_schedule_auto_refresh()

    def sort_by(self, column: str) -> None:
        self.view_state.toggle_sort(column)
        self.refresh_table()

    def apply_filters(self) -> None:
        self.view_state.filter_text = self.filter_text_var.get()
        self.view_state.filter_status = self.filter_status_var.get() or "Todos"
        self.view_state.filter_workspace = self.filter_workspace_var.get() or "Todos"
        self.refresh_table()

    def refresh_table(self) -> None:
        current_selection = self.view_state.selected_record_id
        self.tree.delete(*self.tree.get_children())
        for record in self.view_state.visible_records():
            values = (
                record.source_path.name,
                record.label,
                record.status,
                record.http_status if record.http_status is not None else "-",
                self._format_pct(record.five_hour_pct),
                format_remaining(record.five_hour_reset),
                self._format_pct(record.weekly_pct),
                record.workspace_name,
                record.email,
                record.token_expiry,
            )
            tag = record.status if record.status in self.status_tags else ""
            self.tree.insert("", tk.END, iid=record.record_id, values=values, tags=(tag,))
        if current_selection and current_selection in self.tree.get_children():
            self.tree.selection_set(current_selection)
        elif self.tree.get_children():
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.view_state.select(first)
        self._sync_details()
        self.count_var.set(f"{len(self.view_state.visible_records())} conta(s)")

    def _sync_filters(self) -> None:
        status_choices = self.view_state.status_choices()
        workspace_choices = self.view_state.workspace_choices()
        self.status_filter.configure(values=status_choices)
        self.workspace_filter.configure(values=workspace_choices)
        if self.filter_status_var.get() not in status_choices:
            self.filter_status_var.set("Todos")
        if self.filter_workspace_var.get() not in workspace_choices:
            self.filter_workspace_var.set("Todos")

    def on_row_selected(self, _event: object) -> None:
        selection = self.tree.selection()
        self.view_state.select(selection[0] if selection else None)
        self._sync_details()
        self._refresh_controls()

    def _sync_details(self) -> None:
        text = self.view_state.detail_text()
        self.detail_text.configure(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert(tk.END, text)
        self.detail_text.configure(state=tk.DISABLED)

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _current_directory(self) -> Path:
        return Path(self.directory_var.get()).expanduser().resolve()

    def queue_scan(self, reason: str) -> None:
        self._set_directory(self._current_directory())

        def task() -> list[AccountRecord]:
            return self.service.scan_directory(self._current_directory())

        if not self.runner.run("scan", task):
            if reason != "auto":
                self.logger.warning("Ja existe uma operacao em andamento.")
            return
        self.status_var.set("A atualizar contas...")

    def renew_selected(self) -> None:
        record = self.view_state.selected_record()
        if record is None:
            messagebox.showinfo("Renovar", "Selecione uma conta primeiro.")
            return
        if not record.can_renew:
            messagebox.showinfo("Renovar", "A conta selecionada nao tem refresh token.")
            return
        self._queue_renew([record.ref], "Renovar conta selecionada")

    def renew_renewables(self) -> None:
        refs = [record.ref for record in self.view_state.visible_records() if record.can_renew]
        if not refs:
            messagebox.showinfo("Renovar", "Nenhuma conta renovavel no filtro atual.")
            return
        self._queue_renew(refs, f"Renovar {len(refs)} conta(s)")

    def remove_selected(self) -> None:
        record = self.view_state.selected_record()
        if record is None:
            messagebox.showinfo("Remover", "Selecione uma conta primeiro.")
            return
        if not messagebox.askyesno(
            "Confirmar remocao",
            f"Remover a conta '{record.label}' de {record.source_path.name}?",
        ):
            return

        def task() -> list[AccountRecord]:
            return self.service.delete_entries([record.ref], self._current_directory())

        if not self.runner.run("delete", task):
            self.logger.warning("Ja existe uma operacao em andamento.")
            return
        self.status_var.set("A remover conta...")

    def _queue_renew(self, refs: list, label: str) -> None:
        if not messagebox.askyesno("Confirmar renew", f"{label}?"):
            return

        def task() -> list[AccountRecord]:
            return self.service.renew_entries(refs, self._current_directory())

        if not self.runner.run("renew", task):
            self.logger.warning("Ja existe uma operacao em andamento.")
            return
        self.status_var.set("Executando renew...")

    def add_account(self) -> None:
        if self.runner.is_busy():
            messagebox.showinfo("Adicionar conta", "Espere a operacao atual terminar.")
            return
        self.device_cancel_event = threading.Event()
        self.device_dialog = DeviceLoginDialog(self, self.device_cancel_event, self._start_add_account)

    def _start_add_account(self, mode: str) -> bool:
        if self.runner.is_busy():
            messagebox.showinfo("Adicionar conta", "Espere a operacao atual terminar.")
            return False

        def task() -> list[AccountRecord]:
            if mode == "device":
                return self.service.device_code_login(
                    self._current_directory(),
                    progress_callback=lambda text: self.bus.emit("device_status", text=text),
                    event_callback=lambda kind, **payload: self.bus.emit(kind, **payload),
                    cancel_event=self.device_cancel_event,
                )
            return self.service.browser_login(
                self._current_directory(),
                progress_callback=lambda text: self.bus.emit("device_status", text=text),
                event_callback=lambda kind, **payload: self.bus.emit(kind, **payload),
                cancel_event=self.device_cancel_event,
            )

        if not self.runner.run("device_login", task):
            if self.device_dialog is not None and self.device_dialog.exists():
                self.device_dialog.close()
            self.device_dialog = None
            self.logger.warning("Ja existe uma operacao em andamento.")
            return False
        if mode == "device":
            self.status_var.set("A iniciar autenticacao por codigo...")
        else:
            self.status_var.set("A iniciar autenticacao no browser...")
        return True

    def _refresh_controls(self) -> None:
        busy = self.runner.is_busy()
        selected = self.view_state.selected_record()
        self.refresh_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.renew_selected_button.configure(
            state=tk.NORMAL if selected is not None and selected.can_renew and not busy else tk.DISABLED
        )
        has_renewable = any(record.can_renew for record in self.view_state.visible_records())
        self.renew_all_button.configure(state=tk.NORMAL if has_renewable and not busy else tk.DISABLED)
        self.remove_selected_button.configure(state=tk.NORMAL if selected is not None and not busy else tk.DISABLED)
        self.add_account_button.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def show_row_menu(self, event: tk.Event) -> None:
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        self.tree.selection_set(row_id)
        self.view_state.select(row_id)
        self._sync_details()
        self._refresh_controls()
        self.row_menu.tk_popup(event.x_root, event.y_root)

    def _process_bus(self) -> None:
        while True:
            try:
                event = self.bus.queue.get_nowait()
            except Empty:
                break
            self._handle_event(event.kind, event.payload)
        self._maybe_schedule_auto_refresh()
        self.after(self.POLL_INTERVAL_MS, self._process_bus)

    def _handle_event(self, kind: str, payload: dict) -> None:
        if kind == "log":
            self._append_log(str(payload.get("line", "")))
        elif kind == "worker_started":
            self._refresh_controls()
        elif kind == "worker_finished":
            self._refresh_controls()
        elif kind == "worker_error":
            self.status_var.set(f"Erro: {payload.get('error', 'falha desconhecida')}")
            self.logger.error(str(payload.get("error", "Falha desconhecida")))
            if payload.get("name") == "device_login" and self.device_dialog is not None and self.device_dialog.exists():
                self.device_dialog.set_progress(100)
                self.device_dialog.set_status(str(payload.get("error", "Falha ao adicionar conta")))
        elif kind == "worker_result":
            records = payload.get("result", [])
            if isinstance(records, list):
                self.view_state.set_records(records)
                self._sync_filters()
                self.apply_filters()
                worker_name = payload.get("name")
                if worker_name == "scan":
                    self.status_var.set("Atualizacao concluida.")
                elif worker_name == "renew":
                    self.status_var.set("Renew concluido.")
                elif worker_name == "delete":
                    self.status_var.set("Conta removida.")
                elif worker_name == "device_login":
                    self.status_var.set("Conta adicionada com sucesso.")
                    if self.device_dialog is not None and self.device_dialog.exists():
                        self.device_dialog.set_progress(100)
                        self.device_dialog.set_status("Conta adicionada com sucesso.")
        elif kind == "browser_login_ready":
            if self.device_dialog is not None and self.device_dialog.exists():
                url = str(payload.get("url", "") or "")
                if url:
                    self.device_dialog.set_launch_url(url, open_now=True)
                    self.device_dialog.set_status("Browser aberto. Termine o login para importar a conta.")
        elif kind == "device_code":
            if self.device_dialog is not None and self.device_dialog.exists():
                session = payload.get("session")
                if session is not None:
                    self.device_dialog.set_device_code(session.user_code, session.verification_uri)
                    self.device_dialog.set_status("Codigo gerado. Copie e aprove no browser.")
        elif kind == "device_poll":
            if self.device_dialog is not None and self.device_dialog.exists():
                attempt = int(payload.get("attempt", 0))
                total = max(1, int(payload.get("total", 1)))
                self.device_dialog.set_progress(min(85, int((attempt / total) * 100)))
                self.device_dialog.set_status(str(payload.get("status", "")))
        elif kind == "device_status":
            if self.device_dialog is not None and self.device_dialog.exists():
                text = str(payload.get("text", ""))
                self.device_dialog.set_status(text)
                if "Browser pronto" in text:
                    self.device_dialog.set_progress(40)
                elif "trocar codigo" in text.lower():
                    self.device_dialog.set_progress(75)
        elif kind == "device_saved":
            if self.device_dialog is not None and self.device_dialog.exists():
                self.device_dialog.set_progress(90)
                self.device_dialog.set_status(f"Gravado em {payload.get('path', '-')}")
        elif kind == "browser_saved":
            if self.device_dialog is not None and self.device_dialog.exists():
                self.device_dialog.set_progress(90)
                self.device_dialog.set_status(f"Gravado em {payload.get('path', '-')}")

    def _maybe_schedule_auto_refresh(self) -> None:
        if self.auto_refresh.request_schedule(self.runner.is_busy()):
            delay_ms = self.auto_refresh.interval_seconds * 1000
            self.after(delay_ms, self._auto_refresh_tick)

    def _auto_refresh_tick(self) -> None:
        self.auto_refresh.mark_fired()
        if self.auto_refresh.enabled:
            self.queue_scan("auto")

    def _format_pct(self, value: float | None) -> str:
        if value is None:
            return "-"
        return f"{int(round(value))}%"


def main() -> None:
    try:
        app = ExplodirApp()
    except OpenAIAPIError as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Dependencia ausente", str(exc))
        root.destroy()
        return
    app.mainloop()


if __name__ == "__main__":
    main()
