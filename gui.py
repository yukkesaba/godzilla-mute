"""Windows desktop interface for GodzillaMute."""
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from types import SimpleNamespace
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from godzilla_mute import ABSENCE_SECONDS, Detector, Gate, Obs, live, replay


class App:
    def __init__(self, root):
        self.root = root
        root.title("ゴジラミュート")
        root.geometry("680x550")
        root.minsize(620, 500)
        root.option_add("*Font", ("Yu Gothic UI", 10))
        self.events = Queue()
        self.commands = Queue()
        self.mode = None
        self.closing = False

        self.port = tk.StringVar(value="4455")
        self.password = tk.StringVar()
        self.video = tk.StringVar()
        self.audio = tk.StringVar()
        self.apply = tk.BooleanVar(value=True)
        self.auto_resume = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="待機中")

        page = ttk.Frame(root, padding=22)
        page.pack(fill="both", expand=True)
        ttk.Label(page, text="ゴジラミュート", font=("Yu Gothic UI", 19, "bold")).pack(anchor="w")
        ttk.Label(page, textvariable=self.status).pack(anchor="w", pady=(2, 14))

        settings = ttk.LabelFrame(page, text="OBS接続", padding=12)
        settings.pack(fill="x")
        settings.columnconfigure(1, weight=1)

        self.port_entry = ttk.Entry(settings, textvariable=self.port, width=8)
        self.password_entry = ttk.Entry(settings, textvariable=self.password, show="*")
        self.video_box = ttk.Combobox(settings, textvariable=self.video)
        self.audio_box = ttk.Combobox(settings, textvariable=self.audio)
        fields = [
            ("ポート", self.port_entry), ("パスワード", self.password_entry),
            ("ゲーム映像ソース", self.video_box), ("ゲーム専用の音声ソース", self.audio_box)
        ]
        for row, (label, field) in enumerate(fields):
            ttk.Label(settings, text=label).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 10))
            field.grid(row=row, column=1, sticky="w" if row == 0 else "ew", pady=3)
        self.fetch = ttk.Button(settings, text="ソースを取得", command=self.fetch_sources)
        self.fetch.grid(row=0, column=2, padx=(10, 0))
        self.paste = ttk.Button(settings, text="貼り付け", command=self.paste_password)
        self.paste.grid(row=1, column=2, sticky="ew", padx=(10, 0))

        options = ttk.Frame(page)
        options.pack(fill="x", pady=(10, 6))
        self.toggle_apply = ttk.Checkbutton(options, text="OBSの音声を実際に切り替える", variable=self.apply)
        self.toggle_resume = ttk.Checkbutton(options, text=f"自動復帰（{ABSENCE_SECONDS:g}秒）",
                                             variable=self.auto_resume)
        self.toggle_apply.pack(side="left", padx=(0, 16))
        self.toggle_resume.pack(side="left")

        actions = ttk.Frame(page)
        actions.pack(fill="x", pady=(0, 10))
        self.start = ttk.Button(actions, text="監視を開始", command=self.start_live)
        self.test = ttk.Button(actions, text="録画でテスト", command=self.start_replay)
        self.mute = ttk.Button(actions, text="手動ミュート", command=lambda: self.commands.put("m"))
        self.restore = ttk.Button(actions, text="音声を戻す", command=lambda: self.commands.put("r"))
        self.stop = ttk.Button(actions, text="停止", command=self.stop_work)
        for button in (self.start, self.test, self.mute, self.restore, self.stop):
            button.pack(side="left", padx=(0, 6))

        log_frame = ttk.LabelFrame(page, text="検知ログ", padding=8)
        log_frame.pack(fill="both", expand=True)
        self.log_view = tk.Text(log_frame, height=6, wrap="word", state="disabled",
                                font=("Yu Gothic UI", 10))
        scroll = ttk.Scrollbar(log_frame, command=self.log_view.yview)
        self.log_view.configure(yscrollcommand=scroll.set)
        self.log_view.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.settings = [field for _, field in fields] + [
            self.fetch, self.paste, self.start, self.test, self.toggle_apply, self.toggle_resume
        ]
        self.refresh()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(50, self.drain)

    def refresh(self):
        for widget in self.settings:
            widget.configure(state="normal" if self.mode is None else "disabled")
        for widget in (self.mute, self.restore):
            widget.configure(state="normal" if self.mode == "live" else "disabled")
        self.stop.configure(state="normal" if self.mode in ("live", "replay") else "disabled")

    def log(self, message):
        self.events.put(("log", message))

    def next_command(self):
        try:
            return self.commands.get_nowait()
        except Empty:
            return None

    def launch(self, mode, status, task):
        self.commands = Queue()
        self.mode = mode
        self.status.set(status)
        self.refresh()

        def worker():
            try:
                task()
            except Exception as error:
                self.events.put(("error", str(error)))
            finally:
                self.events.put(("done", None))

        Thread(target=worker, daemon=True).start()

    def connection(self):
        try:
            port = int(self.port.get())
            if 1 <= port <= 65535:
                return port, self.password.get() or None
        except ValueError:
            pass
        messagebox.showerror("ポート番号", "1〜65535のポート番号を入力してください。", parent=self.root)
        return None

    def paste_password(self):
        try:
            password = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showinfo("貼り付け", "クリップボードにテキストがありません。", parent=self.root)
            return
        self.password.set(password.rstrip("\r\n"))

    def fetch_sources(self):
        connection = self.connection()
        if connection is None:
            return

        def task():
            obs = Obs(*connection)
            try:
                rows = obs.request("GetInputList")["inputs"]
                self.events.put(("sources", rows))
            finally:
                obs.socket.close()

        self.launch("fetch", "OBSに接続中…", task)

    def start_live(self):
        connection = self.connection()
        if connection is None:
            return
        port, password = connection
        args = SimpleNamespace(port=port, video=self.video.get(), audio=self.audio.get(),
                               apply=self.apply.get())
        if not (args.video.strip() and args.audio.strip()):
            messagebox.showerror("OBSソース", "ゲーム映像とゲーム専用の音声ソースを指定してください。",
                                 parent=self.root)
            return
        resume = self.auto_resume.get()
        status = "OBS音声を制御中" if args.apply else "監視中（ログのみ）"
        self.launch("live", status, lambda: live(args, Detector(), Gate(resume), password,
                                                  log=self.log, command=self.next_command))

    def start_replay(self):
        path = filedialog.askopenfilename(parent=self.root, title="テストする録画を選択",
                    filetypes=[("動画", "*.mp4 *.mkv *.mov *.avi"), ("すべてのファイル", "*.*")])
        if path:
            resume = self.auto_resume.get()
            self.launch("replay", "録画を解析中…", lambda: replay(Path(path), Detector(), Gate(resume),
                        log=self.log, command=self.next_command))

    def stop_work(self):
        self.commands.put("q")
        self.status.set("停止中…")
        for widget in (self.mute, self.restore, self.stop):
            widget.configure(state="disabled")

    def close(self):
        if self.mode is None:
            self.root.destroy()
        else:
            self.closing = True
            self.stop_work()

    def drain(self):
        while True:
            try:
                event, value = self.events.get_nowait()
            except Empty:
                break
            if event == "sources":
                self.video_box.configure(values=sorted(row["inputName"] for row in value))
                audio_kinds = {"wasapi_process_output_capture", "game_capture", "window_capture"}
                self.audio_box.configure(values=sorted(row["inputName"] for row in value
                                                      if row["inputKind"] in audio_kinds))
                self.log(f"OBSに接続しました（ソース {len(value)} 件）。")
            elif event in ("log", "error"):
                if event == "error":
                    value = "エラー: " + value
                self.log_view.configure(state="normal")
                self.log_view.insert("end", value + "\n")
                self.log_view.see("end")
                self.log_view.configure(state="disabled")
            elif event == "done":
                if self.closing:
                    self.root.destroy()
                    return
                self.mode = None
                self.status.set("待機中")
                self.refresh()
        self.root.after(50, self.drain)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
