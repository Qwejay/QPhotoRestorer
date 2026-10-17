# -*- coding: utf-8 -*-
"""
PhotoRestorer 1.0 
"""
import os
import re
import sys
import math
import json
import wave
import struct
import shutil
import tempfile
import subprocess
from datetime import datetime

# 强制启用 Windows 平台高 DPI 完美缩放支持
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"

# Windows 底层 API (穿透修改文件系统创建时间)
import ctypes
from ctypes import wintypes

from PySide6.QtCore import (
    Qt, QThread, Signal, Slot, QPoint, QRectF, QPropertyAnimation,
    QEasingCurve, QTimer, QSettings, QByteArray
)
from PySide6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QPixmap, QPalette, QIcon
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QProgressBar, QFrame,
    QFileDialog, QGraphicsDropShadowEffect, QStackedLayout, QGraphicsOpacityEffect
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtCore import QUrl

MEDIA_EXTS = {'.jpg', '.jpeg', '.dng', '.png', '.mp4', '.m4v', '.mov'}
UNDO_FILE_NAME = ".photo_undo_log.json"

# ==============================================================================
# 1. 现代化品牌视觉引擎 (Chrono-Camera 时光相机)
# ==============================================================================
SVG_BRAND_ICON = """<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M10 16V12C10 10.8954 10.8954 10 12 10H14" stroke="{color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M17 16L20 9H28L31 16" stroke="{color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
  <rect x="5" y="16" width="38" height="26" rx="6" stroke="{color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="24" cy="29" r="8" stroke="{color}" stroke-width="3"/>
  <path d="M24 24.5V29L27 32" stroke="{color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M41 3L42.2 6.8L46 8L42.2 9.2L41 13L39.8 9.2L36 8L39.8 6.8L41 3Z" fill="{color}"/>
</svg>"""

SVG_FOLDER_ICON = """<svg viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/>
  <path d="m9 13 2 2 4-4"/>
</svg>"""

def render_svg(svg_template: str, color: str = "#2563EB", logical_size: int = 24) -> QPixmap:
    raw_svg = svg_template.format(color=color)
    renderer = QSvgRenderer(QByteArray(raw_svg.encode("utf-8")))
    
    # 获取系统主屏幕的物理缩放倍率 (100%=1.0, 150%=1.5, 200%=2.0)
    dpr = QApplication.primaryScreen().devicePixelRatio() if QApplication.primaryScreen() else 1.0
    
    # 根据物理倍率创建真实的高清画布
    physical_size = int(logical_size * dpr)
    pixmap = QPixmap(physical_size, physical_size)
    pixmap.fill(Qt.transparent)
    
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    
    # 渲染 SVG 到画布
    renderer.render(painter)
    painter.end()
    
    # 告诉 Qt 该 Pixmap 的像素密度，使其在 UI 中自动保持正确的逻辑大小但拥有物理级的锐利度
    pixmap.setDevicePixelRatio(dpr)
    return pixmap

# ==============================================================================
# 2. 纯算法即时微音效引擎
# ==============================================================================
class SyntheticAudio:
    @staticmethod
    def play_tone(notes, duration=0.20):
        sample_rate = 44100
        total_samples = int(sample_rate * duration)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_name = tmp.name
        tmp.close()

        with wave.open(tmp_name, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            frames = bytearray()
            for i in range(total_samples):
                t = float(i) / sample_rate
                env = math.exp(-8.0 * t / duration)
                val = sum(math.sin(2.0 * math.pi * f * t) for f in notes)
                val = (val / len(notes)) * env * 22000
                frames.extend(struct.pack('<h', int(max(-32767, min(32767, val)))))
            wf.writeframes(frames)

        effect = QSoundEffect()
        effect.setSource(QUrl.fromLocalFile(tmp_name))
        effect.setVolume(0.30)
        effect.play()
        QTimer.singleShot(1000, lambda: os.unlink(tmp_name) if os.path.exists(tmp_name) else None)

    @classmethod
    def success(cls):
        cls.play_tone([523.25, 659.25, 783.99], duration=0.22)

    @classmethod
    def error(cls):
        cls.play_tone([220.00, 207.65], duration=0.25)

# ==============================================================================
# 3. 核心底座：文件系统与时间解析
# ==============================================================================
def set_windows_creation_time(file_path, dt):
    if os.name != 'nt':
        return
    try:
        handle = ctypes.windll.kernel32.CreateFileW(
            file_path, 0x0100, 0, None, 3, 0x02000000, None
        )
        if handle == -1 or handle == 0xFFFFFFFFFFFFFFFF:
            return
        ts = dt.timestamp()
        w_time = int((ts + 11644473600) * 10000000)
        ctime = wintypes.FILETIME(w_time & 0xFFFFFFFF, (w_time >> 32) & 0xFFFFFFFF)
        ctypes.windll.kernel32.SetFileTime(handle, ctypes.byref(ctime), None, None)
        ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        pass

def find_exiftool():
    tool = shutil.which("exiftool")
    if tool: return tool
    sd = os.path.dirname(os.path.abspath(__file__))
    for n in ["exiftool.exe", "exiftool(-k).exe", "exiftool"]:
        if os.path.isfile(os.path.join(sd, n)): return os.path.join(sd, n)
    return None

def parse_time(text):
    m_wx = re.search(r'(?:mmexport|wx_camera_)(\d{10})\d{3}', text, re.IGNORECASE)
    if m_wx: return datetime.fromtimestamp(int(m_wx.group(1)))

    m_full = re.search(r'(\d{4})[-_年\.]?(\d{2})[-_月\.]?(\d{2})[_\sT\-]?(\d{2})[-_\.:]?(\d{2})[-_\.:]?(\d{2})', text)
    if m_full:
        y, mo, d, H, M, S = [int(x) for x in m_full.groups()]
        if (1970<=y<=2035) and (1<=mo<=12) and (1<=d<=31) and (0<=H<=23) and (0<=M<=59) and (0<=S<=59):
            return datetime(y, mo, d, H, M, S)

    m_day = re.search(r'(\d{4})[-_年\.]?(\d{2})[-_月\.]?(\d{2})', text)
    if m_day:
        y, mo, d = int(m_day.group(1)), int(m_day.group(2)), int(m_day.group(3))
        if 1970<=y<=2035 and 1<=mo<=12 and 1<=d<=31: return datetime(y, mo, d, 12, 0, 0)

    m_mon = re.search(r'(\d{4})[-_年\.]?(\d{2})', text)
    if m_mon:
        y, mo = int(m_mon.group(1)), int(m_mon.group(2))
        if 1970<=y<=2035 and 1<=mo<=12: return datetime(y, mo, 1, 12, 0, 0)

    return None

def get_target_dt(file_path):
    dt = parse_time(os.path.basename(file_path))
    if dt: return dt
    for folder in reversed(os.path.abspath(file_path).split(os.sep)[:-1]):
        dt = parse_time(folder)
        if dt: return dt
    return None

# ==============================================================================
# 4. 异步线程基建 (索引与执行)
# ==============================================================================
class FastScanWorker(QThread):
    scan_done = Signal(str, int, bool)
    def __init__(self, target_dir):
        super().__init__()
        self.target_dir = target_dir
        self._cancel = False
    def cancel(self): self._cancel = True
    def run(self):
        c = self._scan(self.target_dir) if not self._cancel else 0
        if not self._cancel:
            has_undo = os.path.isfile(os.path.join(self.target_dir, UNDO_FILE_NAME))
            self.scan_done.emit(self.target_dir, c, has_undo)
    def _scan(self, path):
        c = 0
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if self._cancel: return c
                    if entry.is_file(follow_symlinks=False) and os.path.splitext(entry.name)[1].lower() in MEDIA_EXTS:
                        c += 1
                    elif entry.is_dir(follow_symlinks=False):
                        c += self._scan(entry.path)
        except Exception: pass
        return c

class ExecWorker(QThread):
    progress = Signal(int, int, str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, mode, target_dir, opts):
        super().__init__()
        self.mode = mode
        self.target_dir = target_dir
        self.opts = opts
        self._cancel = False

    def cancel(self): self._cancel = True

    def run(self):
        try:
            if self.mode == "process": self.do_process()
            else: self.do_undo()
        except Exception as e:
            self.error.emit(str(e))

    def do_process(self):
        all_media = []
        for root, _, files in os.walk(self.target_dir):
            for f in files:
                if os.path.splitext(f)[1].lower() in MEDIA_EXTS:
                    all_media.append(os.path.join(root, f))
            if not self.opts.get("recursive", True): break

        total = len(all_media)
        if total == 0:
            self.finished.emit({"total": 0, "processed": 0, "renamed": 0})
            return

        exiftool = find_exiftool() if self.opts.get("write_exif", True) else None
        exif_cmds, file_map = [], {}

        for fpath in all_media:
            if self._cancel: return
            dt = get_target_dt(fpath)
            if dt:
                file_map[fpath] = dt
                ts_str = dt.strftime("%Y:%m:%d %H:%M:%S")
                if exiftool:
                    cmd = ["-overwrite_original", "-api", "QuickTimeUTC", "-charset", "filename=utf8",
                           f"-AllDates={ts_str}", f"-FileModifyDate={ts_str}", f"-FileCreateDate={ts_str}"]
                    if os.path.splitext(fpath)[1].lower() in {'.mp4', '.m4v', '.mov'}:
                        cmd.extend([f"-QuickTime:CreateDate={ts_str}", f"-QuickTime:ModifyDate={ts_str}",
                                    f"-Track*Date={ts_str}", f"-Media*Date={ts_str}"])
                    cmd.extend([os.path.abspath(fpath), "-execute"])
                    exif_cmds.extend(cmd)

        if exif_cmds and exiftool and not self._cancel:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            proc = subprocess.Popen([exiftool, "-stay_open", "True", "-charset", "filename=utf8", "-@", "-"],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding="utf-8", startupinfo=si)
            proc.communicate(input="\n".join(exif_cmds) + "\n-stay_open\nFalse\n")

        undo_items, renamed = [], 0
        for idx, fpath in enumerate(all_media):
            if self._cancel: break
            dt = file_map.get(fpath)
            if dt:
                cur = fpath
                fol, old_name = os.path.dirname(cur), os.path.basename(cur)
                ext = os.path.splitext(old_name)[1]
                
                try:
                    st = os.stat(cur)
                    om, oa = st.st_mtime, st.st_atime
                except Exception: om, oa = None, None

                if self.opts.get("rename", True):
                    nb = dt.strftime("%Y%m%d_%H%M%S")
                    if old_name.lower() != f"{nb}{ext.lower()}":
                        tn = f"{nb}{ext.lower()}"
                        tp = os.path.join(fol, tn)
                        c = 1
                        while os.path.exists(tp) and os.path.abspath(tp).lower() != os.path.abspath(cur).lower():
                            tp = os.path.join(fol, f"{nb}_{c}{ext.lower()}")
                            c += 1
                        if os.path.abspath(cur) != os.path.abspath(tp):
                            try:
                                os.rename(cur, tp)
                                cur = tp
                                renamed += 1
                            except Exception: pass

                try: os.utime(cur, (dt.timestamp(), dt.timestamp()))
                except Exception: pass
                set_windows_creation_time(cur, dt)

                undo_items.append({"original_path": os.path.abspath(fpath), "modified_path": os.path.abspath(cur),
                                   "original_mtime": om, "original_atime": oa})

            if (idx + 1) % 50 == 0 or (idx + 1) == total:
                self.progress.emit(idx + 1, total, f"处理中: {os.path.basename(fpath)}")

        if undo_items:
            try:
                with open(os.path.join(self.target_dir, UNDO_FILE_NAME), "w", encoding="utf-8") as f:
                    json.dump({"target_dir": os.path.abspath(self.target_dir), "items": undo_items}, f)
            except Exception: pass

        self.finished.emit({"total": total, "processed": len(undo_items), "renamed": renamed})

    def do_undo(self):
        uf = os.path.join(self.target_dir, UNDO_FILE_NAME)
        if not os.path.isfile(uf): return self.error.emit("未检测到历史记录")
        with open(uf, "r", encoding="utf-8") as f: data = json.load(f)
        
        items, reverted = data.get("items", []), 0
        for idx, it in enumerate(reversed(items)):
            if self._cancel: break
            op, mp, om = it["original_path"], it["modified_path"], it.get("original_mtime")
            ac = mp
            if os.path.exists(mp) and os.path.abspath(mp) != os.path.abspath(op):
                try:
                    if not os.path.exists(op):
                        os.rename(mp, op)
                        ac = op
                        reverted += 1
                except Exception: pass
            if os.path.exists(ac) and om:
                try: os.utime(ac, (om, om))
                except Exception: pass
            if (idx + 1) % 50 == 0 or (idx + 1) == len(items):
                self.progress.emit(idx + 1, len(items), f"恢复: {os.path.basename(op)}")
        try: os.remove(uf)
        except Exception: pass
        self.finished.emit({"total": len(items), "reverted": reverted})

# ==============================================================================
# 5. UI 核心构件 (画布与微交互按钮)
# ==============================================================================
class MorphingStage(QFrame):
    clicked = Signal()
    folder_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.is_hover = False
        self.setFixedHeight(140)

        self.stack = QStackedLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 0)

        # 待命态
        w_idle = QWidget()
        l_idle = QVBoxLayout(w_idle)
        l_idle.setAlignment(Qt.AlignCenter)
        l_idle.setSpacing(8)
        self.ic_idle = QLabel()
        self.ic_idle.setPixmap(render_svg(SVG_BRAND_ICON, "#2563EB", 36))
        self.ic_idle.setAlignment(Qt.AlignCenter)
        self.lb_idle_t = QLabel("拖入媒体文件夹，或点击选择")
        self.lb_idle_t.setFont(QFont("Segoe UI Variable Display", 12, QFont.DemiBold))
        self.lb_idle_t.setStyleSheet("color: #111827;")
        self.lb_idle_t.setAlignment(Qt.AlignCenter)
        self.lb_idle_s = QLabel("一键修复媒体文件日期为拍摄日期")
        self.lb_idle_s.setFont(QFont("Segoe UI", 8))
        self.lb_idle_s.setStyleSheet("color: #64748B;")
        self.lb_idle_s.setAlignment(Qt.AlignCenter)
        l_idle.addWidget(self.ic_idle)
        l_idle.addWidget(self.lb_idle_t)
        l_idle.addWidget(self.lb_idle_s)
        self.stack.addWidget(w_idle)

        # 就绪态
        w_ready = QWidget()
        l_ready = QVBoxLayout(w_ready)
        l_ready.setAlignment(Qt.AlignCenter)
        l_ready.setSpacing(6)
        self.ic_ready = QLabel()
        self.ic_ready.setPixmap(render_svg(SVG_FOLDER_ICON, "#2563EB", 28))
        self.ic_ready.setAlignment(Qt.AlignCenter)
        self.lb_ready_t = QLabel()
        self.lb_ready_t.setFont(QFont("Segoe UI Variable Display", 12, QFont.Bold))
        self.lb_ready_t.setStyleSheet("color: #111827;")
        self.lb_ready_t.setAlignment(Qt.AlignCenter)
        self.lb_ready_s = QLabel()
        self.lb_ready_s.setFont(QFont("Segoe UI", 8))
        self.lb_ready_s.setStyleSheet("color: #64748B;")
        self.lb_ready_s.setAlignment(Qt.AlignCenter)
        l_ready.addWidget(self.ic_ready)
        l_ready.addWidget(self.lb_ready_t)
        l_ready.addWidget(self.lb_ready_s)
        self.stack.addWidget(w_ready)

    def set_loading(self, path):
        self.lb_ready_t.setText(os.path.basename(path) or path)
        self.lb_ready_s.setText("正在极速索引媒体流...")
        self.stack.setCurrentIndex(1)
        self.update()

    def set_ready(self, path, count):
        if path:
            self.lb_ready_t.setText(os.path.basename(path) or path)
            self.lb_ready_s.setText(f"已吸附 {count} 个媒体项目 · 时空修复就绪")
            self.stack.setCurrentIndex(1)
        else:
            self.stack.setCurrentIndex(0)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton: self.clicked.emit()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            for u in e.mimeData().urls():
                if os.path.isdir(u.toLocalFile()):
                    self.is_hover = True
                    self.update()
                    e.acceptProposedAction()
                    return
        e.ignore()

    def dragLeaveEvent(self, e):
        self.is_hover = False
        self.update()
        e.accept()

    def dropEvent(self, e):
        self.is_hover = False
        self.update()
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if os.path.isdir(p):
                self.folder_dropped.emit(p)
                e.acceptProposedAction()
                return
        e.ignore()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.is_hover:
            painter.setBrush(QBrush(QColor("#EFF6FF")))
            painter.setPen(QPen(QColor("#BFDBFE"), 1.2))
        else:
            painter.setBrush(QBrush(QColor("#FFFFFF")))
            painter.setPen(QPen(QColor("#F1F5F9"), 1))
        painter.drawRoundedRect(rect, 14, 14)
        painter.end()

class ModernButton(QPushButton):
    """带平滑呼吸反馈的现代胶囊按钮"""
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(40)
        # 挂载 Windows 11 新字体或回退雅黑
        self.setFont(QFont("Segoe UI Variable Display", 10, QFont.Bold))
        self.op_eff = QGraphicsOpacityEffect(self)
        self.op_eff.setOpacity(1.0)
        self.setGraphicsEffect(self.op_eff)
        self.anim = QPropertyAnimation(self.op_eff, b"opacity")
        self.anim.setDuration(150)
        self.reset_style()

    def reset_style(self):
        self.setStyleSheet("""
            QPushButton { background-color: #2563EB; color: #FFFFFF; border: none; border-radius: 20px; }
            QPushButton:disabled { background-color: #E2E8F0; color: #94A3B8; }
        """)

    def set_danger_style(self):
        self.setStyleSheet("""
            QPushButton { background-color: #DC2626; color: #FFFFFF; border: none; border-radius: 20px; }
        """)

    def enterEvent(self, e):
        if self.isEnabled():
            self.anim.stop()
            self.anim.setStartValue(self.op_eff.opacity())
            self.anim.setEndValue(0.85)
            self.anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self.isEnabled():
            self.anim.stop()
            self.anim.setStartValue(self.op_eff.opacity())
            self.anim.setEndValue(1.0)
            self.anim.start()
        super().leaveEvent(e)

# ==============================================================================
# 6. 主视窗集成
# ==============================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PhotoRestorer 1.0")
        self.resize(480, 350)
        self.setMinimumSize(450, 300)
        self.setWindowIcon(QIcon(render_svg(SVG_BRAND_ICON, "#2563EB", 64)))

        self.cur_dir = ""
        self.scan_w = None
        self.exec_w = None

        QApplication.setStyle("Fusion")
        pal = QPalette()
        pal.setColor(QPalette.Window, QColor("#F8FAFC"))
        pal.setColor(QPalette.WindowText, QColor("#111827"))
        QApplication.setPalette(pal)

        self.setup_ui()
        self.load_settings()

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_l = QVBoxLayout(central)
        main_l.setContentsMargins(24, 20, 24, 20)
        main_l.setSpacing(14)

        # Header
        top_l = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(render_svg(SVG_BRAND_ICON, "#2563EB", 22))
        title = QLabel("PhotoRestorer")
        title.setFont(QFont("Segoe UI Variable Display", 12, QFont.Bold))
        title.setStyleSheet("color: #111827; letter-spacing: -0.2px;")
        
        self.btn_undo = QPushButton("↩ 撤销操作")
        self.btn_undo.setCursor(Qt.PointingHandCursor)
        self.btn_undo.setFont(QFont("Segoe UI", 9))
        self.btn_undo.setStyleSheet("QPushButton{color:#64748B; border:none; background:transparent;} QPushButton:hover{color:#2563EB;}")
        self.btn_undo.setEnabled(False)
        self.btn_undo.clicked.connect(self.run_undo)

        self.btn_open = QPushButton("打开目录")
        self.btn_open.setCursor(Qt.PointingHandCursor)
        self.btn_open.setFont(QFont("Segoe UI", 9))
        self.btn_open.setStyleSheet("QPushButton{color:#64748B; border:none; background:transparent;} QPushButton:hover{color:#111827;}")
        self.btn_open.setVisible(False)
        self.btn_open.clicked.connect(self.open_dir)

        top_l.addWidget(logo)
        top_l.addWidget(title)
        top_l.addStretch()
        top_l.addWidget(self.btn_undo)
        top_l.addWidget(self.btn_open)
        main_l.addLayout(top_l)

        # Stage
        self.stage = MorphingStage()
        self.stage.clicked.connect(self.pick_dir)
        self.stage.folder_dropped.connect(self.load_dir)
        shadow = QGraphicsDropShadowEffect(self.stage)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 8))
        shadow.setOffset(0, 3)
        self.stage.setGraphicsEffect(shadow)
        main_l.addWidget(self.stage)

        # Options
        opt_l = QHBoxLayout()
        opt_l.setContentsMargins(4, 4, 4, 4)
        opt_l.setSpacing(20)
        self.chk_ren = QCheckBox("规范重命名 (20031226_200545)")
        self.chk_ren.setChecked(True)
        self.chk_exif = QCheckBox("深层固化系统时间与EXIF")
        self.chk_exif.setChecked(True)
        chk_css = """
            QCheckBox { color: #475569; font-family: "Segoe UI"; font-size: 12px; }
            QCheckBox:hover { color: #111827; }
            QCheckBox::indicator { width:14px; height:14px; border-radius:4px; border:1px solid #CBD5E1; background:#FFF; }
            QCheckBox::indicator:checked { background:#2563EB; border:1px solid #2563EB; }
        """
        self.chk_ren.setStyleSheet(chk_css)
        self.chk_exif.setStyleSheet(chk_css)
        opt_l.addWidget(self.chk_ren)
        opt_l.addWidget(self.chk_exif)
        opt_l.addStretch()
        main_l.addLayout(opt_l)

        # Progress & Ticker
        self.prog = QProgressBar()
        self.prog.setFixedHeight(4)
        self.prog.setTextVisible(False)
        self.prog.setStyleSheet("QProgressBar{background:#F1F5F9; border-radius:2px; border:none;} QProgressBar::chunk{background:#2563EB; border-radius:2px;}")
        self.prog.setVisible(False)
        main_l.addWidget(self.prog)

        self.ticker = QLabel()
        self.ticker.setFont(QFont("Segoe UI", 9))
        self.ticker.setStyleSheet("color: #94A3B8;")
        self.ticker.setAlignment(Qt.AlignCenter)
        self.ticker.setFixedHeight(16)
        main_l.addWidget(self.ticker)

        # CTA
        self.btn_main = ModernButton("执行修复")
        self.btn_main.setEnabled(False)
        self.btn_main.clicked.connect(self.run_main)
        main_l.addWidget(self.btn_main)

    def trigger_shake(self):
        SyntheticAudio.error()
        anim = QPropertyAnimation(self, b"pos")
        anim.setDuration(240)
        p = self.pos()
        for i, off in enumerate([0, -8, 6, -4, 2, 0]):
            anim.setKeyValueAt(i/5, p + QPoint(off, 0))
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()

    def pick_dir(self):
        if d := QFileDialog.getExistingDirectory(self, "选择媒体所在目录", self.cur_dir or "."):
            self.load_dir(d)

    def load_dir(self, path):
        self.cur_dir = path
        self.stage.set_loading(path)
        self.btn_main.setEnabled(False)
        self.ticker.setText("正在极速探查目录结构...")
        
        if self.scan_w and self.scan_w.isRunning(): self.scan_w.cancel(); self.scan_w.wait()
        self.scan_w = FastScanWorker(path)
        self.scan_w.scan_done.connect(self.on_scan_done)
        self.scan_w.start()

    @Slot(str, int, bool)
    def on_scan_done(self, path, count, has_undo):
        if path != self.cur_dir: return
        self.stage.set_ready(path, count)
        self.btn_main.setEnabled(count > 0)
        self.btn_open.setVisible(True)
        self.btn_undo.setEnabled(has_undo)
        self.ticker.setText(f"就绪：锁定 {count} 个目标媒体" if count > 0 else "未检测到支持的 JPG/MP4 媒体")

    def open_dir(self):
        if self.cur_dir and os.path.isdir(self.cur_dir):
            os.startfile(self.cur_dir) if os.name == 'nt' else subprocess.Popen(["xdg-open", self.cur_dir])
        else: self.trigger_shake()

    def run_main(self):
        if self.exec_w and self.exec_w.isRunning():
            self.exec_w.cancel()
            self.btn_main.setText("中止中...")
            self.btn_main.setEnabled(False)
            return

        self.btn_main.setText("⏹ 中断操作")
        self.btn_main.set_danger_style()
        self.prog.setVisible(True)
        self.btn_undo.setEnabled(False)

        opts = {"rename": self.chk_ren.isChecked(), "write_exif": self.chk_exif.isChecked()}
        self.exec_w = ExecWorker("process", self.cur_dir, opts)
        self.exec_w.progress.connect(self.on_prog)
        self.exec_w.finished.connect(self.on_done)
        self.exec_w.error.connect(self.on_err)
        self.exec_w.start()

    def run_undo(self):
        if not self.cur_dir: return
        self.btn_main.setEnabled(False)
        self.btn_undo.setEnabled(False)
        self.prog.setVisible(True)
        self.ticker.setText("正在执行时空回溯...")

        self.exec_w = ExecWorker("undo", self.cur_dir, {})
        self.exec_w.progress.connect(self.on_prog)
        self.exec_w.finished.connect(self.on_undo_done)
        self.exec_w.error.connect(self.on_err)
        self.exec_w.start()

    @Slot(int, int, str)
    def on_prog(self, cur, total, txt):
        pct = int((cur/total)*100) if total else 0
        self.prog.setValue(pct)
        self.ticker.setText(f"{pct}% · {txt}")

    @Slot(dict)
    def on_done(self, res):
        self.btn_main.setText("执行修复")
        self.btn_main.reset_style()
        self.btn_main.setEnabled(True)
        self.prog.setVisible(False)
        self.btn_undo.setEnabled(True)
        SyntheticAudio.success()
        self.load_dir(self.cur_dir)
        self.ticker.setText(f"耀世重生：已重写 {res['processed']} 份元数据，规范重命名 {res['renamed']} 项")

    @Slot(dict)
    def on_undo_done(self, res):
        self.btn_main.setText("执行修复")
        self.btn_main.reset_style()
        self.btn_main.setEnabled(True)
        self.prog.setVisible(False)
        SyntheticAudio.success()
        self.load_dir(self.cur_dir)
        self.ticker.setText(f"回溯完成：已安全剥离并复原 {res.get('reverted',0)} 项更改")

    @Slot(str)
    def on_err(self, err):
        self.btn_main.setText("执行修复")
        self.btn_main.reset_style()
        self.btn_main.setEnabled(True)
        self.prog.setVisible(False)
        self.trigger_shake()
        self.ticker.setText(f"执行异常: {err}")

    def closeEvent(self, e):
        s = QSettings("PhotoRestorer", "Release")
        s.setValue("rn", self.chk_ren.isChecked())
        s.setValue("ex", self.chk_exif.isChecked())
        super().closeEvent(e)

    def load_settings(self):
        s = QSettings("PhotoRestorer", "Release")
        self.chk_ren.setChecked(s.value("rn", True, type=bool))
        self.chk_exif.setChecked(s.value("ex", True, type=bool))

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())