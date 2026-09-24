"""Small Tkinter front end for the In Falsus song pack pipeline."""

from __future__ import annotations

import queue
import json
from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Callable


BG = "#11161d"
PANEL = "#1b222c"
FIELD = "#242d38"
TEXT = "#edf2f7"
MUTED = "#aeb9c5"
ACCENT = "#59b6e6"


def _optional(value: str) -> str | None:
    value = value.strip()
    return value or None


class StudioApp:
    """Owns the window and keeps pipeline work off Tk's event loop."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._events: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._cancel_event: threading.Event | None = None
        self._busy = False
        self._last_pack = None
        self._form_controls: list[tk.Misc] = []

        self.media_var = tk.StringVar()
        self.game_var = tk.StringVar()
        self.title_var = tk.StringVar()
        self.artist_var = tk.StringVar()
        self.reference_var = tk.StringVar()
        self.cover_var = tk.StringVar()
        self.source_search_var = tk.BooleanVar(value=True)
        self.decorations_var = tk.BooleanVar(value=False)
        self.normalize_audio_var = tk.BooleanVar(value=True)
        self.scroll_effects_var = tk.BooleanVar(value=True)
        self.install_after_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Ready")

        self._configure_window()
        self._build_widgets()
        self.root.after(50, self._drain_events)
        self.root.after_idle(self._discover_game)

    def _configure_window(self) -> None:
        self.root.title("In Falsus Studio")
        self.root.geometry("840x900")
        self.root.minsize(740, 820)
        self.root.configure(background=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure('.', background=BG, foreground=TEXT, font=('Segoe UI',10))
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 18, "bold"))
        style.configure("TLabelframe", background=BG, foreground=TEXT,bordercolor='#334250')
        style.configure("TLabelframe.Label", background=BG, foreground=TEXT)
        style.configure("TEntry", fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT)
        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", BG)], foreground=[("disabled", MUTED)])
        style.configure("TButton", padding=(10, 6),background=PANEL,foreground=TEXT,bordercolor='#334250')
        style.map('TButton',background=[('active',FIELD)],foreground=[('disabled',MUTED)])
        style.configure("Accent.TButton", background=ACCENT, foreground="#081018", padding=(14, 8))
        style.map("Accent.TButton", background=[("active", "#7acaf0"), ("disabled", "#53616d")])
        style.configure("Horizontal.TProgressbar", troughcolor=FIELD, background=ACCENT, bordercolor=FIELD)

    def _build_widgets(self) -> None:
        outer = ttk.Frame(self.root, padding=20)
        outer.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        ttk.Label(outer, text="In Falsus Studio", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            outer,
            text="Create and install charts with automatic playability checks.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 16))

        inputs = ttk.LabelFrame(outer, text="Song and game", padding=12)
        inputs.grid(row=2, column=0, sticky="ew")
        inputs.columnconfigure(1, weight=1)
        self._add_path_row(
            inputs,
            0,
            "Media file or URL",
            self.media_var,
            self._browse_media,
        )
        self._add_path_row(
            inputs,
            1,
            "Game folder",
            self.game_var,
            self._browse_game,
        )
        self._add_text_row(inputs, 2, "Title (optional)", self.title_var)
        self._add_text_row(inputs, 3, "Artist (optional)", self.artist_var)
        self._add_path_row(
            inputs,
            4,
            "Reference folder",
            self.reference_var,
            self._browse_reference,
        )
        self._add_path_row(
            inputs,
            5,
            "Cover image",
            self.cover_var,
            self._browse_cover,
        )

        options = ttk.LabelFrame(outer, text="Options", padding=12)
        options.grid(row=3, column=0, sticky="new", pady=(14, 0))
        for col in range(2):
            options.columnconfigure(col, weight=1)
        self._add_check(options, 0, 0, "Search for source chart", self.source_search_var)
        self._add_check(options, 0, 1, "Include decorations", self.decorations_var)
        self._add_check(options, 1, 0, "Normalize audio", self.normalize_audio_var)
        self._add_check(options, 1, 1, "Add scroll effects", self.scroll_effects_var)
        self._add_check(options, 2, 0, "Install after creating", self.install_after_var)
        ttk.Label(options,text='Online Arcaea search; local AFF, Deemo and Ongeki references.\nDecorations require the optional BepInEx loader.',
            style='Muted.TLabel').grid(row=3,column=0,columnspan=2,sticky='w',pady=(8,0))

        action = ttk.Frame(outer)
        action.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        action.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(action, mode="indeterminate", length=160)
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.create_button = ttk.Button(
            action,
            text="Create & install",
            command=self._start_job,
            style="Accent.TButton",
        )
        self.create_button.grid(row=0, column=1, padx=(0, 8))
        self.cancel_button = ttk.Button(action, text="Cancel", command=self._cancel_job, state="disabled")
        self.cancel_button.grid(row=0, column=2)
        recovery = ttk.Frame(outer)
        recovery.grid(row=6, column=0, sticky='ew', pady=(10,0))
        self.install_button = ttk.Button(recovery, text='Install prepared pack…', command=lambda:self._manage_pack(False))
        self.install_button.pack(side='left', padx=(0,8))
        self.restore_button = ttk.Button(recovery, text='Restore backup…', command=lambda:self._manage_pack(True))
        self.restore_button.pack(side='left')
        self._form_controls.extend([self.install_button,self.restore_button])
        ttk.Label(action, textvariable=self.status_var, style="Muted.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )

        log_frame = ttk.LabelFrame(outer, text="Progress", padding=8)
        log_frame.grid(row=5, column=0, sticky="nsew", pady=(14, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(
            log_frame,
            height=5,
            wrap="word",
            state="disabled",
            background=FIELD,
            foreground=TEXT,
            insertbackground=TEXT,
            relief="flat",
            padx=8,
            pady=8,
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scrollbar.set)
        outer.rowconfigure(5, weight=1)
        self._append_log("Ready. Choose a media file or URL to begin.")

    def _add_text_row(self, parent: ttk.LabelFrame, row: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, columnspan=2, sticky="ew", pady=5)
        self._form_controls.append(entry)

    def _add_path_row(
        self,
        parent: ttk.LabelFrame,
        row: int,
        label: str,
        variable: tk.StringVar,
        browse: Callable[[], None],
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        button = ttk.Button(parent, text="Browse…", command=browse)
        button.grid(row=row, column=2, sticky="e", padx=(8, 0), pady=5)
        self._form_controls.extend((entry, button))

    def _add_check(
        self,
        parent: ttk.LabelFrame,
        row: int,
        column: int,
        text: str,
        variable: tk.BooleanVar,
    ) -> None:
        check = ttk.Checkbutton(parent, text=text, variable=variable)
        check.grid(row=row, column=column, sticky="w", padx=(0, 18), pady=4)
        self._form_controls.append(check)

    def _discover_game(self) -> None:
        if self.game_var.get().strip():
            return
        try:
            from .installer import discover_game

            found = discover_game()
        except Exception as exc:
            self._append_log(f"Game auto-discovery unavailable: {exc}")
            return
        if found:
            self.game_var.set(str(found))
            self._append_log(f"Game found: {found}")
        else:
            self._append_log("Game folder not found automatically; choose it with Browse…")

    def _browse_media(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Choose media",
            filetypes=[
                ("Media", "*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.mp4 *.webm"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.media_var.set(path)

    def _browse_game(self) -> None:
        path = filedialog.askdirectory(parent=self.root, title="Choose game folder")
        if path:
            self.game_var.set(path)

    def _browse_reference(self) -> None:
        path = filedialog.askdirectory(parent=self.root, title="Choose reference folder")
        if path:
            self.reference_var.set(path)

    def _browse_cover(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Choose cover image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp"), ("All files", "*.*")],
        )
        if path:
            self.cover_var.set(path)

    def _options(self) -> dict[str, Any]:
        return {
            "media": self.media_var.get().strip(),
            "game_root": _optional(self.game_var.get()),
            "title": _optional(self.title_var.get()),
            "artist": _optional(self.artist_var.get()),
            "reference_dir": _optional(self.reference_var.get()),
            "cover": _optional(self.cover_var.get()),
            "source_search": self.source_search_var.get(),
            "decorations": self.decorations_var.get(),
            "normalize_audio": self.normalize_audio_var.get(),
            "scroll_effects": self.scroll_effects_var.get(),
            "install_after": self.install_after_var.get(),
        }

    def _start_job(self) -> None:
        if self._busy:
            return
        options = self._options()
        if not options["media"]:
            self.status_var.set("Media is required")
            self._append_log("Error: choose a local media file or enter a media URL.")
            return

        self._set_busy(True)
        self.status_var.set("Working…")
        self._append_log("Starting pack creation…")
        cancel = threading.Event()
        self._cancel_event = cancel
        worker = threading.Thread(target=self._run_job, args=(options, cancel), daemon=True)
        worker.start()

    def _manage_pack(self, restore):
        if self._busy:return
        path = self._last_pack
        if not path:
            path = filedialog.askdirectory(title='Choose the install-pack folder')
        if not path:return
        try:
            manifest=json.loads((Path(path)/'install-manifest.json').read_text(encoding='utf8'))
            game=Path(manifest['game_root'])
        except Exception as error:
            self._append_log(f'Could not open this pack: {error}');return
        self._set_busy(True)
        self.status_var.set('Restoring…' if restore else 'Installing…')
        self._cancel_event=threading.Event()
        def worker():
            try:
                from .installer import restore_pack,install_pack
                receipt=(restore_pack if restore else install_pack)(Path(path),game)
                self._events.put(('done',dict(pack_dir=str(path),installed=not restore,
                    status=receipt['status'],warnings=[]),None,False))
            except Exception as error:self._events.put(('done',None,error,False))
        threading.Thread(target=worker,daemon=True).start()

    def _run_job(self, options: dict[str, Any], cancel: threading.Event) -> None:
        result: dict[str, Any] | None = None
        error: Exception | None = None

        def progress(message: str) -> None:
            self._events.put(("progress", str(message)))

        try:
            from .pipeline import run_job

            result = run_job(options, progress=progress, cancel=cancel)
        except Exception as exc:  # The UI must recover from every backend failure.
            error = exc
        self._events.put(("done", result, error, cancel.is_set()))

    def _cancel_job(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
            self.status_var.set("Cancelling…")
            self._append_log("Cancellation requested; waiting for the current step to finish…")
            self.cancel_button.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        form_state = "disabled" if busy else "normal"
        for control in self._form_controls:
            control.configure(state=form_state)
        self.create_button.configure(state="disabled" if busy else "normal")
        self.cancel_button.configure(state="normal" if busy else "disabled")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()
            self._cancel_event = None

    def _drain_events(self) -> None:
        try:
            while True:
                event = self._events.get_nowait()
                if event[0] == "progress":
                    self._append_log(str(event[1]))
                elif event[0] == "done":
                    self._finish_job(event[1], event[2], bool(event[3]))
        except queue.Empty:
            pass
        except tk.TclError:
            return
        try:
            self.root.after(50, self._drain_events)
        except tk.TclError:
            pass

    def _finish_job(
        self,
        result: dict[str, Any] | None,
        error: Exception | None,
        cancelled: bool,
    ) -> None:
        self._set_busy(False)
        if cancelled and not (result or {}).get('installed'):
            self.status_var.set('Cancelled')
            self._append_log('Cancelled. No completed install was reported.')
            return
        if error is not None:
            self.status_var.set("Failed")
            self._append_log(f"Error: {self._actionable_error(error)}")
            return
        if cancelled and not (result or {}).get('installed'):
            self.status_var.set("Cancelled")
            self._append_log("Cancelled. No completed install was reported.")
            return

        result = result or {}
        self.status_var.set('Installed' if result.get('installed') else 'Restored' if result.get('status')=='restored' else 'Pack ready')
        pack_dir = result.get("pack_dir") or result.get("job_dir") or "unknown output"
        self._last_pack = result.get('pack_dir') or self._last_pack
        installed = result.get("installed")
        self._append_log(f"Pack ready: {pack_dir}")
        self._append_log("Installed into the game folder." if installed else 'Backup restored.' if result.get('status')=='restored' else "Pack created; use Install prepared pack when the game is closed.")
        songs = result.get("songs")
        if songs is not None:
            try:
                self._append_log(f"Songs: {len(songs)}")
            except TypeError:
                self._append_log(f"Songs: {songs}")
        for warning in result.get("warnings", []) or []:
            self._append_log(f"Warning: {warning}")

    @staticmethod
    def _actionable_error(error: Exception) -> str:
        message = str(error).strip() or error.__class__.__name__
        if isinstance(error, FileNotFoundError):
            return f"{message} Check the media and game folder paths, then try again."
        return f"{message} Check the media, game folder, and optional inputs, then try again."

    def _append_log(self, message: str) -> None:
        try:
            self.log.configure(state="normal")
            self.log.insert("end", f"{message}\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        except tk.TclError:
            pass

    def _close(self) -> None:
        if self._busy:
            self._cancel_job()
            return
        self.root.destroy()


def main(automation=False) -> None:
    root = tk.Tk()
    if automation:root.withdraw()
    StudioApp(root)
    if automation:
        import ctypes
        root.update_idletasks()
        user=ctypes.windll.user32
        user.GetParent.restype=ctypes.c_void_p
        user.GetParent.argtypes=[ctypes.c_void_p]
        hwnd=user.GetParent(root.winfo_id())
        user.GetWindowLongW.argtypes=[ctypes.c_void_p,ctypes.c_int]
        user.SetWindowLongW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_long]
        # This test window cannot activate and has no taskbar button.
        flags=user.GetWindowLongW(hwnd,-20)
        user.SetWindowLongW(hwnd,-20,(flags|0x08000000)&~(0x40000|0x80))
        root.deiconify()
        import os
        destination=os.environ.get('INFALSUS_STUDIO_QA_EXPORT')
        if destination:
            def export():
                from .qa_render import export_window
                export_window(hwnd,destination)
                root.destroy()
            root.after(700,export)
        else:root.after(120000,root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
