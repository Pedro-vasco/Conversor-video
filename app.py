import json
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import customtkinter as ctk
from tkinter import filedialog, messagebox, ttk
from tkinter import PhotoImage

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")


@dataclass
class QueueItem:
    id: str
    input_path: Path
    status: str = "Pendente"
    estimate_mb: float = 0.0
    output_path: Optional[Path] = None


class VideoConverterApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Conversor WhatsApp - Versão 6")
        self.geometry("1280x760")

        self.items: list[QueueItem] = []
        self.current_item_id: Optional[str] = None
        self.stop_after_current = False
        self.cancel_current = False
        self.processing_thread: Optional[threading.Thread] = None
        self.current_process: Optional[subprocess.Popen] = None
        self.log_queue: queue.Queue[str] = queue.Queue()

        self.output_dir = self._default_output_dir()
        self.ffmpeg_path = self._resolve_binary("ffmpeg.exe")
        self.ffprobe_path = self._resolve_binary("ffprobe.exe")
        self.thumbnail_cache_dir = Path(tempfile.gettempdir()) / "conversor_video_v6_thumb"
        self.thumbnail_cache_dir.mkdir(parents=True, exist_ok=True)
        self.current_preview_image = None

        self._build_ui()
        self.after(200, self._drain_logs)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        top = ctk.CTkFrame(self)
        top.pack(fill="x", padx=12, pady=12)

        self.output_label = ctk.CTkLabel(top, text=f"Pasta de saída: {self.output_dir}")
        self.output_label.pack(side="left", padx=8, pady=8)
        ctk.CTkButton(top, text="Escolher pasta de saída", command=self.choose_output_folder).pack(
            side="right", padx=8, pady=8
        )

        middle = ctk.CTkFrame(self)
        middle.pack(fill="x", padx=12, pady=(0, 12))

        ctk.CTkButton(middle, text="Adicionar vídeos", command=self.add_files).pack(side="left", padx=6, pady=8)
        ctk.CTkButton(middle, text="Iniciar fila", command=self.start_queue).pack(side="left", padx=6, pady=8)
        ctk.CTkButton(middle, text="Cancelar item atual", command=self.cancel_current_item).pack(
            side="left", padx=6, pady=8
        )
        ctk.CTkButton(middle, text="Parar após item atual", command=self.stop_after_item).pack(side="left", padx=6, pady=8)
        ctk.CTkButton(middle, text="Retry erro/cancelado", command=self.retry_failed_items).pack(side="left", padx=6, pady=8)
        ctk.CTkButton(middle, text="Limpar fila", command=self.clear_queue).pack(side="left", padx=6, pady=8)

        options = ctk.CTkFrame(self)
        options.pack(fill="x", padx=12, pady=(0, 12))

        ctk.CTkLabel(options, text="Modo:").pack(side="left", padx=(8, 4))
        self.mode_var = ctk.StringVar(value="quality")
        self.mode_menu = ctk.CTkComboBox(options, values=["quality", "target_size"], variable=self.mode_var)
        self.mode_menu.pack(side="left", padx=4)
        self.mode_menu.bind("<<ComboboxSelected>>", lambda _e: self.refresh_estimates())
        self.mode_menu.configure(command=lambda _v: self.refresh_estimates())

        ctk.CTkLabel(options, text="Preset qualidade:").pack(side="left", padx=(12, 4))
        self.preset_var = ctk.StringVar(value="medium")
        self.preset_menu = ctk.CTkComboBox(options, values=["high", "medium", "low"], variable=self.preset_var)
        self.preset_menu.pack(side="left", padx=4)
        self.preset_menu.configure(command=lambda _v: self.refresh_estimates())

        ctk.CTkLabel(options, text="CRF:").pack(side="left", padx=(12, 4))
        self.crf_var = ctk.StringVar(value="25")
        ctk.CTkEntry(options, textvariable=self.crf_var, width=60).pack(side="left", padx=4)

        ctk.CTkLabel(options, text="Alvo MB/item:").pack(side="left", padx=(12, 4))
        self.target_mb_var = ctk.StringVar(value="16")
        ctk.CTkEntry(options, textvariable=self.target_mb_var, width=80).pack(side="left", padx=4)

        content = ctk.CTkFrame(self)
        content.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        left = ctk.CTkFrame(content)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=0)

        self.tree = ttk.Treeview(left, columns=("arquivo", "status", "estimativa", "saida"), show="headings", height=16)
        self.tree.heading("arquivo", text="Arquivo")
        self.tree.heading("status", text="Status")
        self.tree.heading("estimativa", text="Estimativa")
        self.tree.heading("saida", text="Saída")
        self.tree.column("arquivo", width=350)
        self.tree.column("status", width=110)
        self.tree.column("estimativa", width=110)
        self.tree.column("saida", width=340)
        self.tree.pack(fill="both", expand=True, padx=8, pady=8)
        self.tree.bind("<<TreeviewSelect>>", self.on_select_item)

        self.progress = ctk.CTkProgressBar(left)
        self.progress.pack(fill="x", padx=8, pady=(0, 8))
        self.progress.set(0)

        right = ctk.CTkFrame(content)
        right.pack(side="right", fill="both", padx=(8, 0), pady=0)

        ctk.CTkLabel(right, text="Preview").pack(padx=8, pady=(8, 4))
        self.preview_label = ctk.CTkLabel(right, text="Selecione um item para preview", width=340, height=240)
        self.preview_label.pack(padx=8, pady=8)

        ctk.CTkLabel(right, text="Logs").pack(padx=8, pady=(8, 4))
        self.logs = ctk.CTkTextbox(right, width=360)
        self.logs.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _default_output_dir(self) -> Path:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        path = base / "output"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _resolve_binary(self, name: str) -> str:
        candidates = []
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).parent
            candidates.extend([exe_dir / name, exe_dir / "ffmpeg" / name])
        base = Path(__file__).resolve().parent
        candidates.extend([base / "ffmpeg" / name, base / name])
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        found = shutil.which(name.replace(".exe", ""))
        return found or name

    def choose_output_folder(self):
        selected = filedialog.askdirectory(title="Escolher pasta de saída")
        if selected:
            self.output_dir = Path(selected)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.output_label.configure(text=f"Pasta de saída: {self.output_dir}")
            self.log(f"Pasta de saída definida para: {self.output_dir}")
            self.refresh_estimates()

    def add_files(self):
        files = filedialog.askopenfilenames(
            title="Selecionar vídeos",
            filetypes=[("Vídeos", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v"), ("Todos", "*.*")],
        )
        for file_path in files:
            item = QueueItem(id=str(uuid.uuid4()), input_path=Path(file_path))
            item.estimate_mb = self.estimate_size_mb(item)
            self.items.append(item)
            self._render_item(item)

    def clear_queue(self):
        if self.processing_thread and self.processing_thread.is_alive():
            messagebox.showwarning("Fila em andamento", "Não é possível limpar enquanto a fila está em andamento.")
            return
        self.items.clear()
        for row in self.tree.get_children():
            self.tree.delete(row)
        self.progress.set(0)
        self._set_preview_fallback("Fila vazia")

    def retry_failed_items(self):
        retried = 0
        for item in self.items:
            if item.status in {"Erro", "Cancelado"}:
                item.status = "Pendente"
                item.output_path = None
                item.estimate_mb = self.estimate_size_mb(item)
                self._update_tree_row(item)
                retried += 1
        if retried:
            self.log(f"{retried} item(ns) reenfileirado(s).")
        else:
            self.log("Nenhum item com erro/cancelado para retry.")

    def stop_after_item(self):
        self.stop_after_current = True
        self.log("A fila será parada após o item atual.")

    def cancel_current_item(self):
        if self.current_process and self.current_process.poll() is None:
            self.cancel_current = True
            self.current_process.terminate()
            self.log("Cancelamento solicitado para o item atual.")
        else:
            self.log("Nenhum item em processamento para cancelar.")

    def start_queue(self):
        if self.processing_thread and self.processing_thread.is_alive():
            messagebox.showinfo("Fila", "A fila já está em execução.")
            return
        pending = [item for item in self.items if item.status == "Pendente"]
        if not pending:
            messagebox.showwarning("Fila", "Não há itens pendentes para processar.")
            return
        self.stop_after_current = False
        self.cancel_current = False
        self.processing_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.processing_thread.start()

    def _process_queue(self):
        pending = [item for item in self.items if item.status == "Pendente"]
        total = max(len(pending), 1)
        done = 0
        for item in pending:
            self.current_item_id = item.id
            item.status = "Processando"
            self._schedule_ui(lambda i=item: self._update_tree_row(i))

            success, canceled, output_path = self.convert_item(item)
            if success:
                item.status = "Concluído"
                item.output_path = output_path
            elif canceled:
                item.status = "Cancelado"
                item.output_path = None
            else:
                item.status = "Erro"
                item.output_path = None

            done += 1
            progress_value = done / total
            self._schedule_ui(lambda val=progress_value: self.progress.set(val))
            self._schedule_ui(lambda i=item: self._update_tree_row(i))
            if self.stop_after_current:
                self.log("Fila parada conforme solicitado.")
                break

        self.current_item_id = None

    def convert_item(self, item: QueueItem):
        duration = self.get_duration_seconds(item.input_path)
        output_path = self.get_output_path(item.input_path)

        vf_scale = {
            "high": "scale='min(1280,iw)':-2",
            "medium": "scale='min(960,iw)':-2",
            "low": "scale='min(854,iw)':-2",
        }.get(self.preset_var.get(), "scale='min(960,iw)':-2")

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-i",
            str(item.input_path),
            "-vf",
            vf_scale,
            "-r",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-movflags",
            "+faststart",
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-ar",
            "44100",
        ]

        if self.mode_var.get() == "target_size":
            target_mb = self._safe_float(self.target_mb_var.get(), 16.0)
            target_bits = max(target_mb, 1) * 1024 * 1024 * 8
            audio_bitrate = 96_000
            video_bitrate = int(max((target_bits / max(duration, 1)) - audio_bitrate, 300_000))
            cmd.extend(["-b:v", str(video_bitrate), "-maxrate", str(int(video_bitrate * 1.2)), "-bufsize", str(video_bitrate * 2)])
            cmd.extend(["-b:a", "96k"])
        else:
            crf = int(max(min(self._safe_float(self.crf_var.get(), 25), 35), 18))
            cmd.extend(["-crf", str(crf), "-b:a", "128k"])

        cmd.append(str(output_path))

        self.log(f"Convertendo: {item.input_path.name}")
        self.log("Comando FFmpeg: " + " ".join(cmd))
        canceled = False
        try:
            self.current_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                universal_newlines=True,
            )
            if self.current_process.stdout:
                for line in self.current_process.stdout:
                    self.log(line.strip())
            code = self.current_process.wait()
            if self.cancel_current:
                canceled = True
                self.cancel_current = False
            if code != 0 and not canceled:
                self.log(f"Erro na conversão: {item.input_path.name}")
                return False, False, None
            if canceled:
                self.log(f"Item cancelado: {item.input_path.name}")
                return False, True, None
            self.log(f"Concluído: {output_path}")
            return True, False, output_path
        except Exception as exc:
            self.log(f"Falha inesperada: {exc}")
            return False, False, None
        finally:
            self.current_process = None

    def get_output_path(self, input_path: Path) -> Path:
        base_name = f"{input_path.stem}_whatsapp"
        output_path = self.output_dir / f"{base_name}.mp4"
        idx = 1
        while output_path.exists():
            output_path = self.output_dir / f"{base_name}_{idx}.mp4"
            idx += 1
        return output_path

    def get_duration_seconds(self, input_path: Path) -> float:
        cmd = [
            self.ffprobe_path,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            str(input_path),
        ]
        try:
            completed = subprocess.run(cmd, check=True, capture_output=True, text=True)
            data = json.loads(completed.stdout)
            return float(data.get("format", {}).get("duration", 0) or 0)
        except Exception:
            return 0.0

    def estimate_size_mb(self, item: QueueItem) -> float:
        mode = self.mode_var.get()
        if mode == "target_size":
            return max(self._safe_float(self.target_mb_var.get(), 16.0), 1.0)

        duration = max(self.get_duration_seconds(item.input_path), 1)
        preset_bitrates = {"high": 1_900_000, "medium": 1_300_000, "low": 900_000}
        audio_bitrate = 128_000
        total_bitrate = preset_bitrates.get(self.preset_var.get(), 1_300_000) + audio_bitrate
        size_mb = (duration * total_bitrate) / 8 / 1024 / 1024
        return max(size_mb, 0.5)

    def refresh_estimates(self):
        for item in self.items:
            if item.status == "Pendente":
                item.estimate_mb = self.estimate_size_mb(item)
                self._update_tree_row(item)

    def on_select_item(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        item_id = selected[0]
        item = self._find_item(item_id)
        if not item:
            return
        thumb_path = self.generate_thumbnail(item.input_path)
        if not thumb_path or not thumb_path.exists():
            self._set_preview_fallback("Preview indisponível para este arquivo")
            return
        try:
            image = PhotoImage(file=str(thumb_path))
            self.current_preview_image = image
            self.preview_label.configure(image=image, text="")
        except Exception:
            self._set_preview_fallback("Preview indisponível para este arquivo")

    def generate_thumbnail(self, input_path: Path) -> Optional[Path]:
        out = self.thumbnail_cache_dir / f"{input_path.stem}_thumb.png"
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-ss",
            "00:00:01",
            "-i",
            str(input_path),
            "-frames:v",
            "1",
            "-vf",
            "scale=340:-2",
            str(out),
        ]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return out
        except Exception:
            return None

    def _set_preview_fallback(self, text: str):
        self.current_preview_image = None
        self.preview_label.configure(image=None, text=text)

    def _render_item(self, item: QueueItem):
        self.tree.insert(
            "",
            "end",
            iid=item.id,
            values=(
                item.input_path.name,
                item.status,
                f"~{item.estimate_mb:.1f} MB",
                "",
            ),
        )

    def _update_tree_row(self, item: QueueItem):
        if not self.tree.exists(item.id):
            return
        self.tree.item(
            item.id,
            values=(
                item.input_path.name,
                item.status,
                f"~{item.estimate_mb:.1f} MB",
                str(item.output_path) if item.output_path else "",
            ),
        )

    def _find_item(self, item_id: str) -> Optional[QueueItem]:
        return next((item for item in self.items if item.id == item_id), None)

    def _safe_float(self, value: str, default: float) -> float:
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    def _schedule_ui(self, callback):
        self.after(0, callback)

    def log(self, text: str):
        self.log_queue.put(text)

    def _drain_logs(self):
        while not self.log_queue.empty():
            line = self.log_queue.get_nowait()
            self.logs.insert("end", line + "\n")
            self.logs.see("end")
        self.after(200, self._drain_logs)

    def _on_close(self):
        if self.current_process and self.current_process.poll() is None:
            self.current_process.terminate()
        self.destroy()


if __name__ == "__main__":
    app = VideoConverterApp()
    app.mainloop()
