# ============================================================
# 剪辑守护 v1.0 — 防止剪映/PR/AE 崩溃导致内容丢失
# 作者：ClipGuardian Team
# 依赖：psutil（进程检测），其余全为标准库
# ============================================================

import os
import sys
import time
import json
import zipfile
import hashlib
import shutil
import threading
import datetime
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

import psutil  # 进程检测（唯一额外依赖）

# ============================================================
# 配置常量
# ============================================================
APP_TITLE = "剪辑守护 v1.0"
WINDOW_WIDTH = 550
WINDOW_HEIGHT = 480

# 深色主题配色方案
COLOR_BG = "#1e1e2e"              # 主背景色
COLOR_BG_SECONDARY = "#2a2a3e"    # 次级背景色（框架内）
COLOR_ACCENT = "#f59e0b"          # 橙色强调色
COLOR_TEXT = "#e0e0e0"            # 主文字色
COLOR_TEXT_DIM = "#8888a0"        # 次要文字色
COLOR_SUCCESS = "#4ade80"         # 绿色（守护中/成功）
COLOR_DANGER = "#e94560"          # 红色（警告/停止）
COLOR_INACTIVE = "#555570"        # 灰色（待命中/未激活）
FONT_FAMILY = "Microsoft YaHei"

# 备份配置
DEFAULT_BACKUP_DIR = "D:\\ClipBackups"
MAX_BACKUP_COUNT = 50             # 最大保留备份数
SCAN_INTERVAL = 30                # 守护扫描间隔（秒）

# 目标进程名（用于 tasklist 匹配）
TARGET_PROCESSES = {
    "Premiere": ["Adobe Premiere Pro.exe"],
    "AfterFX": ["AfterFX.exe"],
    "Jianying": ["JianyingPro.exe"],
}


# ============================================================
# 工具函数
# ============================================================

def get_config_path():
    """获取配置文件路径（存储在用户目录下）"""
    config_dir = os.path.join(os.path.expanduser("~"), ".clip_guardian")
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, "config.json")


def load_config():
    """加载用户配置"""
    path = get_config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "backup_interval": 120,      # 默认备份间隔（秒）
        "monitor_folders": [],       # 监控文件夹列表
        "backup_dir": DEFAULT_BACKUP_DIR,
    }


def save_config(config):
    """保存用户配置"""
    with open(get_config_path(), "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def hash_folder(folder_path):
    """
    计算文件夹的内容哈希值（用于检测文件变化）
    遍历所有文件，按路径排序后计算 SHA256
    """
    if not os.path.isdir(folder_path):
        return ""
    hasher = hashlib.sha256()
    for root, dirs, files in os.walk(folder_path):
        # 跳过备份目录自身
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".clip_backups")]
        for fname in sorted(files):
            fpath = os.path.join(root, fname)
            try:
                stat = os.stat(fpath)
                # 哈希内容：相对路径 + 文件大小 + 修改时间
                rel_path = os.path.relpath(fpath, folder_path)
                hasher.update(rel_path.encode("utf-8"))
                hasher.update(str(stat.st_size).encode("utf-8"))
                hasher.update(str(stat.st_mtime).encode("utf-8"))
            except OSError:
                continue
    return hasher.hexdigest()


def get_folder_size_mb(folder_path):
    """计算文件夹大小（MB）"""
    total = 0
    for root, dirs, files in os.walk(folder_path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                continue
    return total / (1024 * 1024)


def check_process_running(process_names):
    """
    检测目标进程是否在运行
    process_names: 进程名列表（如 ["Adobe Premiere Pro.exe"]）
    返回：是否运行中
    """
    try:
        # 使用 psutil 遍历所有运行进程
        running_names = set()
        for proc in psutil.process_iter(["name"]):
            try:
                running_names.add(proc.info["name"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for name in process_names:
            if name in running_names:
                return True
        return False
    except Exception:
        return False


def get_monitored_apps_running(config):
    """检查所监控的三款软件中哪些正在运行，返回名称列表"""
    running = []
    if check_process_running(TARGET_PROCESSES["Premiere"]):
        running.append("PR")
    if check_process_running(TARGET_PROCESSES["AfterFX"]):
        running.append("AE")
    if check_process_running(TARGET_PROCESSES["Jianying"]):
        running.append("剪映")
    return running


# ============================================================
# 备份引擎
# ============================================================

class BackupEngine:
    """备份引擎：负责压缩、存储、清理备份"""

    def __init__(self, backup_dir, max_backups=MAX_BACKUP_COUNT):
        self.backup_dir = backup_dir
        self.max_backups = max_backups
        # 上次备份哈希缓存：{文件夹路径: 哈希值}
        self.last_hashes = {}
        os.makedirs(backup_dir, exist_ok=True)

    def get_backup_subdir(self, folder_name):
        """获取某个文件夹名对应的备份子目录"""
        sub_dir = os.path.join(self.backup_dir, folder_name)
        os.makedirs(sub_dir, exist_ok=True)
        return sub_dir

    def backup_folder(self, folder_path):
        """
        备份单个文件夹
        返回：备份文件路径（成功）或 None（跳过/失败）
        """
        if not os.path.isdir(folder_path):
            return None

        # 计算当前哈希，对比上次
        current_hash = hash_folder(folder_path)
        if folder_path in self.last_hashes:
            if self.last_hashes[folder_path] == current_hash:
                # 内容无变化，跳过
                return None

        self.last_hashes[folder_path] = current_hash

        # 生成备份文件名
        folder_name = os.path.basename(folder_path.rstrip("\\/"))
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_name = f"{folder_name}_{timestamp}.zip"

        sub_dir = self.get_backup_subdir(folder_name)
        zip_path = os.path.join(sub_dir, zip_name)

        # 打包为 zip
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(folder_path):
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        arcname = os.path.relpath(fpath, folder_path)
                        try:
                            zf.write(fpath, arcname)
                        except OSError:
                            continue
        except Exception as e:
            # 写入失败时删除已创建的不完整文件
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except Exception:
                    pass
            return None

        # 清理旧备份（超出最大保留数）
        self._cleanup_old_backups(sub_dir)

        return zip_path

    def backup_all(self, folders, callback=None):
        """
        备份所有监控文件夹
        callback(status, info): 回调函数，用于 UI 更新
        返回：成功备份数
        """
        success_count = 0
        for folder in folders:
            if callback:
                callback("backup", f"正在备份: {os.path.basename(folder)}")
            result = self.backup_folder(folder)
            if result:
                success_count += 1
                if callback:
                    callback("backup_done", {
                        "path": result,
                        "size": os.path.getsize(result) / (1024 * 1024),
                    })
        return success_count

    def _cleanup_old_backups(self, sub_dir):
        """清理超出最大保留数的旧备份"""
        try:
            files = [f for f in os.listdir(sub_dir) if f.endswith(".zip")]
            # 按修改时间排序（最旧的在前）
            files.sort(key=lambda f: os.path.getmtime(os.path.join(sub_dir, f)))
            while len(files) > self.max_backups:
                oldest = files.pop(0)
                os.remove(os.path.join(sub_dir, oldest))
        except Exception:
            pass

    def get_backup_history(self):
        """获取所有备份历史记录"""
        history = []
        if not os.path.isdir(self.backup_dir):
            return history
        for folder_name in os.listdir(self.backup_dir):
            sub_dir = os.path.join(self.backup_dir, folder_name)
            if not os.path.isdir(sub_dir):
                continue
            for fname in os.listdir(sub_dir):
                if not fname.endswith(".zip"):
                    continue
                fpath = os.path.join(sub_dir, fname)
                try:
                    stat = os.stat(fpath)
                    history.append({
                        "path": fpath,
                        "folder": folder_name,
                        "filename": fname,
                        "size_mb": stat.st_size / (1024 * 1024),
                        "mtime": stat.st_mtime,
                        "time_str": datetime.datetime.fromtimestamp(
                            stat.st_mtime
                        ).strftime("%Y-%m-%d %H:%M:%S"),
                    })
                except OSError:
                    continue
        # 按时间倒序
        history.sort(key=lambda x: x["mtime"], reverse=True)
        return history

    def restore_backup(self, zip_path, target_folder):
        """
        从备份恢复：解压到目标文件夹
        """
        if not os.path.exists(zip_path):
            return False
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(target_folder)
            return True
        except Exception:
            return False

    def delete_backup(self, zip_path):
        """删除指定备份文件"""
        try:
            os.remove(zip_path)
            return True
        except OSError:
            return False


# ============================================================
# GUI 主界面
# ============================================================

class ClipGuardianApp:
    """剪辑守护主界面类"""

    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.root.resizable(False, False)
        self.root.configure(bg=COLOR_BG)

        # 加载配置
        self.config = load_config()

        # 备份引擎
        self.backup_engine = BackupEngine(self.config["backup_dir"])

        # 守护状态
        self.guardian_running = False        # 守护线程是否运行
        self.guardian_thread = None          # 守护线程引用
        self.last_backup_time = None         # 上次备份时间
        self.apps_running = []               # 当前运行的目标软件列表

        # 变量绑定
        self.var_premiere = tk.BooleanVar()
        self.var_afterfx = tk.BooleanVar()
        self.var_jianying = tk.BooleanVar(value=True)  # 剪映默认勾选

        self.var_interval = tk.StringVar(value="2分钟")
        self.var_backup_dir = tk.StringVar(value=self.config["backup_dir"])

        # 构建界面
        self._build_ui()

        # 启动后刷新一次备份历史
        self._refresh_history()

    # --------------------------------------------------------
    # 界面构建
    # --------------------------------------------------------

    def _build_ui(self):
        """构建全部 GUI 组件"""
        # 使用 ttk 的 Style 做深色主题适配
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=COLOR_BG, foreground=COLOR_TEXT, fieldbackground=COLOR_BG_SECONDARY)
        style.configure("TFrame", background=COLOR_BG)
        style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT)
        style.configure("TButton", background=COLOR_BG_SECONDARY, foreground=COLOR_TEXT, borderwidth=1)
        style.map("TButton", background=[("active", COLOR_ACCENT)])
        style.configure("TCheckbutton", background=COLOR_BG, foreground=COLOR_TEXT)
        style.configure("TLabelframe", background=COLOR_BG, foreground=COLOR_ACCENT)
        style.configure("TLabelframe.Label", background=COLOR_BG, foreground=COLOR_ACCENT)

        # 主内容区（带内边距的外层框架）
        main_frame = tk.Frame(self.root, bg=COLOR_BG)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        # ---- 区域一：软件监控 ----
        self._build_section_monitor(main_frame)

        # ---- 区域二：自动备份 ----
        self._build_section_backup(main_frame)

        # ---- 区域三：备份历史 ----
        self._build_section_history(main_frame)

        # ---- 区域四：操作与状态 ----
        self._build_section_status(main_frame)

    def _build_section_monitor(self, parent):
        """区域一：软件监控"""
        frame = tk.LabelFrame(
            parent, text=" 软件监控 ",
            bg=COLOR_BG, fg=COLOR_ACCENT,
            font=(FONT_FAMILY, 10, "bold"),
            padx=10, pady=8,
        )
        frame.pack(fill=tk.X, pady=(0, 8))

        # 复选框行
        chk_frame = tk.Frame(frame, bg=COLOR_BG)
        chk_frame.pack(fill=tk.X)

        cb_pr = tk.Checkbutton(
            chk_frame, text="Adobe Premiere Pro",
            variable=self.var_premiere,
            bg=COLOR_BG, fg=COLOR_TEXT,
            selectcolor=COLOR_BG_SECONDARY,
            activebackground=COLOR_BG,
            activeforeground=COLOR_ACCENT,
            font=(FONT_FAMILY, 9),
        )
        cb_pr.pack(side=tk.LEFT, padx=(0, 15))

        cb_ae = tk.Checkbutton(
            chk_frame, text="After Effects",
            variable=self.var_afterfx,
            bg=COLOR_BG, fg=COLOR_TEXT,
            selectcolor=COLOR_BG_SECONDARY,
            activebackground=COLOR_BG,
            activeforeground=COLOR_ACCENT,
            font=(FONT_FAMILY, 9),
        )
        cb_ae.pack(side=tk.LEFT, padx=(0, 15))

        cb_jy = tk.Checkbutton(
            chk_frame, text="剪映专业版",
            variable=self.var_jianying,
            bg=COLOR_BG, fg=COLOR_TEXT,
            selectcolor=COLOR_BG_SECONDARY,
            activebackground=COLOR_BG,
            activeforeground=COLOR_ACCENT,
            font=(FONT_FAMILY, 9),
        )
        cb_jy.pack(side=tk.LEFT)

        # 开始守护按钮（橙色大按钮）
        self.btn_start_guard = tk.Button(
            frame, text="▶  开始守护",
            command=self._start_guardian,
            bg=COLOR_ACCENT, fg="#1e1e2e",
            font=(FONT_FAMILY, 11, "bold"),
            activebackground="#d97706",
            activeforeground="#1e1e2e",
            relief=tk.FLAT,
            cursor="hand2",
            height=1,
        )
        self.btn_start_guard.pack(fill=tk.X, pady=(8, 0))

    def _build_section_backup(self, parent):
        """区域二：自动备份设置"""
        frame = tk.LabelFrame(
            parent, text=" 自动备份 ",
            bg=COLOR_BG, fg=COLOR_ACCENT,
            font=(FONT_FAMILY, 10, "bold"),
            padx=10, pady=8,
        )
        frame.pack(fill=tk.X, pady=(0, 8))

        # 备份间隔行
        row1 = tk.Frame(frame, bg=COLOR_BG)
        row1.pack(fill=tk.X, pady=(0, 4))
        tk.Label(row1, text="备份间隔：", bg=COLOR_BG, fg=COLOR_TEXT_DIM, font=(FONT_FAMILY, 9)).pack(side=tk.LEFT)
        interval_cb = ttk.Combobox(
            row1, textvariable=self.var_interval,
            values=["30秒", "1分钟", "2分钟", "5分钟"],
            state="readonly", width=8,
        )
        interval_cb.pack(side=tk.LEFT, padx=(4, 0))
        # 从配置恢复
        interval_map = {30: "30秒", 60: "1分钟", 120: "2分钟", 300: "5分钟"}
        self.var_interval.set(interval_map.get(self.config.get("backup_interval", 120), "2分钟"))

        # 监控文件夹行
        row2 = tk.Frame(frame, bg=COLOR_BG)
        row2.pack(fill=tk.X, pady=(0, 4))
        tk.Label(row2, text="监控文件夹：", bg=COLOR_BG, fg=COLOR_TEXT_DIM, font=(FONT_FAMILY, 9)).pack(side=tk.LEFT)

        # 输入框 + 浏览按钮
        self.entry_folder = tk.Entry(
            row2, bg=COLOR_BG_SECONDARY, fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT, relief=tk.FLAT,
            font=(FONT_FAMILY, 9),
        )
        self.entry_folder.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))
        btn_browse = tk.Button(
            row2, text="浏览", command=self._browse_folder,
            bg=COLOR_BG_SECONDARY, fg=COLOR_TEXT,
            font=(FONT_FAMILY, 8),
            relief=tk.FLAT, cursor="hand2",
            activebackground=COLOR_ACCENT,
        )
        btn_browse.pack(side=tk.LEFT)

        # 添加/移除按钮
        row2b = tk.Frame(frame, bg=COLOR_BG)
        row2b.pack(fill=tk.X, pady=(0, 4))
        tk.Button(
            row2b, text="＋ 添加", command=self._add_folder,
            bg=COLOR_BG_SECONDARY, fg=COLOR_TEXT,
            font=(FONT_FAMILY, 9), relief=tk.FLAT, cursor="hand2",
            activebackground=COLOR_SUCCESS,
        ).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(
            row2b, text="－ 移除", command=self._remove_folder,
            bg=COLOR_BG_SECONDARY, fg=COLOR_TEXT,
            font=(FONT_FAMILY, 9), relief=tk.FLAT, cursor="hand2",
            activebackground=COLOR_DANGER,
        ).pack(side=tk.LEFT)

        # 文件夹列表
        self.listbox_folders = tk.Listbox(
            frame, height=3,
            bg=COLOR_BG_SECONDARY, fg=COLOR_TEXT,
            selectbackground=COLOR_ACCENT,
            selectforeground="#1e1e2e",
            relief=tk.FLAT,
            font=(FONT_FAMILY, 9),
        )
        self.listbox_folders.pack(fill=tk.X, pady=(0, 4))
        # 加载已有监控文件夹
        for f in self.config.get("monitor_folders", []):
            if os.path.isdir(f):
                self.listbox_folders.insert(tk.END, f)

        # 备份存储位置行
        row3 = tk.Frame(frame, bg=COLOR_BG)
        row3.pack(fill=tk.X, pady=(0, 4))
        tk.Label(row3, text="备份位置：", bg=COLOR_BG, fg=COLOR_TEXT_DIM, font=(FONT_FAMILY, 9)).pack(side=tk.LEFT)
        entry_backup = tk.Entry(
            row3, textvariable=self.var_backup_dir,
            bg=COLOR_BG_SECONDARY, fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT, relief=tk.FLAT,
            font=(FONT_FAMILY, 9),
        )
        entry_backup.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))
        tk.Button(
            row3, text="浏览", command=self._browse_backup_dir,
            bg=COLOR_BG_SECONDARY, fg=COLOR_TEXT,
            font=(FONT_FAMILY, 8), relief=tk.FLAT, cursor="hand2",
            activebackground=COLOR_ACCENT,
        ).pack(side=tk.LEFT)

        # 说明文字
        tk.Label(
            frame,
            text="守护开启后，每隔指定时间为每个监控文件夹创建 zip 备份",
            bg=COLOR_BG, fg=COLOR_TEXT_DIM,
            font=(FONT_FAMILY, 8),
        ).pack(anchor=tk.W, pady=(2, 0))

    def _build_section_history(self, parent):
        """区域三：备份历史"""
        frame = tk.LabelFrame(
            parent, text=" 备份历史 ",
            bg=COLOR_BG, fg=COLOR_ACCENT,
            font=(FONT_FAMILY, 10, "bold"),
            padx=10, pady=8,
        )
        frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        # 历史列表（可滚动）
        list_frame = tk.Frame(frame, bg=COLOR_BG)
        list_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(list_frame, bg=COLOR_BG_SECONDARY)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.listbox_history = tk.Listbox(
            list_frame, height=5,
            yscrollcommand=scrollbar.set,
            bg=COLOR_BG_SECONDARY, fg=COLOR_TEXT,
            selectbackground=COLOR_ACCENT,
            selectforeground="#1e1e2e",
            relief=tk.FLAT,
            font=(FONT_FAMILY, 9),
        )
        self.listbox_history.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.listbox_history.yview)

        # 双击恢复
        self.listbox_history.bind("<Double-Button-1>", self._on_history_double_click)

        # 按钮行
        btn_frame = tk.Frame(frame, bg=COLOR_BG)
        btn_frame.pack(fill=tk.X, pady=(4, 0))
        tk.Button(
            btn_frame, text="删除选中备份", command=self._delete_selected_backup,
            bg=COLOR_BG_SECONDARY, fg=COLOR_TEXT,
            font=(FONT_FAMILY, 9), relief=tk.FLAT, cursor="hand2",
            activebackground=COLOR_DANGER,
        ).pack(side=tk.LEFT)

    def _build_section_status(self, parent):
        """区域四：底部状态栏 + 操作按钮"""
        frame = tk.Frame(parent, bg=COLOR_BG)
        frame.pack(fill=tk.X)

        # 状态指示行
        status_row = tk.Frame(frame, bg=COLOR_BG)
        status_row.pack(fill=tk.X, pady=(0, 6))

        # 指示灯（Canvas 画圆）
        self.canvas_indicator = tk.Canvas(
            status_row, width=16, height=16,
            bg=COLOR_BG, highlightthickness=0,
        )
        self.canvas_indicator.pack(side=tk.LEFT)
        self._draw_indicator(COLOR_INACTIVE)

        # 守护状态文字
        self.lbl_status = tk.Label(
            status_row, text="○ 待命中",
            bg=COLOR_BG, fg=COLOR_TEXT_DIM,
            font=(FONT_FAMILY, 9, "bold"),
        )
        self.lbl_status.pack(side=tk.LEFT, padx=(4, 20))

        # 上次备份时间
        self.lbl_last_backup = tk.Label(
            status_row, text="上次备份：--",
            bg=COLOR_BG, fg=COLOR_TEXT_DIM,
            font=(FONT_FAMILY, 8),
        )
        self.lbl_last_backup.pack(side=tk.LEFT)

        # 按钮行
        btn_row = tk.Frame(frame, bg=COLOR_BG)
        btn_row.pack(fill=tk.X)

        self.btn_stop_guard = tk.Button(
            btn_row, text="■ 停止守护",
            command=self._stop_guardian,
            bg=COLOR_INACTIVE, fg=COLOR_TEXT,
            font=(FONT_FAMILY, 10, "bold"),
            relief=tk.FLAT, cursor="hand2",
            activebackground=COLOR_DANGER,
            state=tk.DISABLED,
        )
        self.btn_stop_guard.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self.btn_backup_now = tk.Button(
            btn_row, text="⚡ 立即备份",
            command=self._backup_now,
            bg=COLOR_ACCENT, fg="#1e1e2e",
            font=(FONT_FAMILY, 10, "bold"),
            relief=tk.FLAT, cursor="hand2",
            activebackground="#d97706",
            activeforeground="#1e1e2e",
        )
        self.btn_backup_now.pack(side=tk.RIGHT, fill=tk.X, expand=True)

    def _draw_indicator(self, color):
        """在 Canvas 上绘制圆形指示灯"""
        self.canvas_indicator.delete("all")
        self.canvas_indicator.create_oval(2, 2, 14, 14, fill=color, outline="")

    # --------------------------------------------------------
    # 守护线程
    # --------------------------------------------------------

    def _start_guardian(self):
        """启动守护线程"""
        # 检查是否至少勾选了一个软件
        if not any([self.var_premiere.get(), self.var_afterfx.get(), self.var_jianying.get()]):
            messagebox.showwarning("提示", "请至少勾选一个需要监控的软件")
            return

        # 检查是否有监控文件夹
        folders = self._get_monitor_folders()
        if not folders:
            messagebox.showwarning("提示", "请至少添加一个需要备份的监控文件夹")
            return

        # 更新配置
        self._save_current_config()

        # 启动守护
        self.guardian_running = True
        self.btn_start_guard.config(state=tk.DISABLED, bg=COLOR_INACTIVE)
        self.btn_stop_guard.config(state=tk.NORMAL, bg=COLOR_BG_SECONDARY)

        # 更新备份引擎
        self.backup_engine.backup_dir = self.config["backup_dir"]
        self.backup_engine.max_backups = MAX_BACKUP_COUNT

        # 启动守护线程
        self.guardian_thread = threading.Thread(target=self._guardian_loop, daemon=True)
        self.guardian_thread.start()

        self._update_status("守护启动中...", COLOR_INACTIVE)

    def _stop_guardian(self):
        """停止守护线程"""
        self.guardian_running = False
        self.btn_start_guard.config(state=tk.NORMAL, bg=COLOR_ACCENT)
        self.btn_stop_guard.config(state=tk.DISABLED, bg=COLOR_INACTIVE)
        self._update_status("○ 待命中", COLOR_INACTIVE)

    def _guardian_loop(self):
        """守护线程主循环：每 30 秒扫描进程，按间隔备份"""
        # 在守护启动时，解析备份间隔（秒）
        interval_map = {"30秒": 30, "1分钟": 60, "2分钟": 120, "5分钟": 300}
        backup_interval = interval_map.get(self.var_interval.get(), 120)
        last_backup_check = 0

        while self.guardian_running:
            # -- 检测目标软件是否运行 --
            running_apps = self._scan_target_apps()

            # 获取监控文件夹
            folders = self._get_monitor_folders()

            if running_apps:
                app_names = " / ".join(running_apps)
                self.root.after(0, self._update_status,
                                f"● 守护中 — {app_names} 正在运行", COLOR_SUCCESS)
                self.root.after(0, self._draw_indicator, COLOR_SUCCESS)

                # -- 按间隔备份 --
                current_time = time.time()
                if current_time - last_backup_check >= backup_interval:
                    self._do_backup(folders)
                    last_backup_check = current_time
            else:
                self.root.after(0, self._update_status, "○ 待命中", COLOR_INACTIVE)
                self.root.after(0, self._draw_indicator, COLOR_INACTIVE)

            # 等待 30 秒后再次扫描
            for _ in range(SCAN_INTERVAL):
                if not self.guardian_running:
                    break
                time.sleep(1)

        # 线程结束时确保 UI 恢复
        self.root.after(0, self._stop_guardian)

    def _scan_target_apps(self):
        """扫描目标软件进程，返回正在运行的名称列表"""
        running = []
        if self.var_premiere.get() and check_process_running(TARGET_PROCESSES["Premiere"]):
            running.append("PR")
        if self.var_afterfx.get() and check_process_running(TARGET_PROCESSES["AfterFX"]):
            running.append("AE")
        if self.var_jianying.get() and check_process_running(TARGET_PROCESSES["Jianying"]):
            running.append("剪映")
        return running

    def _do_backup(self, folders):
        """执行备份（后台线程中调用）"""
        if not folders:
            return
        try:
            count = self.backup_engine.backup_all(folders)
            if count > 0:
                now = datetime.datetime.now().strftime("%H:%M:%S")
                self.root.after(0, self._set_last_backup_time, now)
            # 刷新历史列表
            self.root.after(0, self._refresh_history)
        except Exception:
            pass

    def _backup_now(self):
        """立即备份（手动触发）"""
        folders = self._get_monitor_folders()
        if not folders:
            messagebox.showwarning("提示", "请先添加监控文件夹")
            return

        self._save_current_config()
        self.backup_engine.backup_dir = self.config["backup_dir"]

        # 后台线程执行
        def run():
            count = self.backup_engine.backup_all(folders)
            self.root.after(0, lambda: self.lbl_last_backup.config(
                text=f"上次备份：{datetime.datetime.now().strftime('%H:%M:%S')}（{count} 个文件夹已备份）"
            ))
            self.root.after(0, self._refresh_history)

        threading.Thread(target=run, daemon=True).start()

    # --------------------------------------------------------
    # UI 更新回调（线程安全，通过 root.after 调度）
    # --------------------------------------------------------

    def _update_status(self, text, color):
        """更新状态栏文字和颜色"""
        self.lbl_status.config(text=text, fg=color)

    def _set_last_backup_time(self, time_str):
        """设置上次备份时间显示"""
        self.lbl_last_backup.config(text=f"上次备份：{time_str}")

    # --------------------------------------------------------
    # 备份历史操作
    # --------------------------------------------------------

    def _refresh_history(self):
        """刷新备份历史列表"""
        self.listbox_history.delete(0, tk.END)
        history = self.backup_engine.get_backup_history()
        # 存储完整历史数据（通过下标映射）
        self._history_data = history
        for item in history:
            display = f"{item['time_str']}  |  {item['folder']}  |  {item['size_mb']:.1f}MB"
            self.listbox_history.insert(tk.END, display)

    def _on_history_double_click(self, event):
        """双击历史记录：弹窗确认后恢复备份"""
        selection = self.listbox_history.curselection()
        if not selection:
            return
        idx = selection[0]
        if idx >= len(self._history_data):
            return

        item = self._history_data[idx]

        # 确认对话框
        answer = messagebox.askyesno(
            "恢复备份",
            f"确定要将备份恢复到原位置吗？\n\n"
            f"备份文件：{item['filename']}\n"
            f"原始文件夹：{item['folder']}\n"
            f"备份时间：{item['time_str']}\n"
            f"大小：{item['size_mb']:.1f} MB\n\n"
            f"⚠ 此操作将覆盖目标文件夹中的同名文件！",
        )
        if not answer:
            return

        # 尝试从监控文件夹中找到对应的原始路径
        target_folder = None
        for f in self._get_monitor_folders():
            if os.path.basename(f.rstrip("\\/")) == item["folder"]:
                target_folder = f
                break
        if not target_folder:
            # 未找到匹配的监控文件夹，询问用户
            target_folder = filedialog.askdirectory(title="选择恢复目标文件夹")
            if not target_folder:
                return

        # 执行恢复
        success = self.backup_engine.restore_backup(item["path"], target_folder)
        if success:
            messagebox.showinfo("恢复成功", f"备份已成功恢复到：\n{target_folder}")
        else:
            messagebox.showerror("恢复失败", "解压备份文件时发生错误")

    def _delete_selected_backup(self):
        """删除选中的备份"""
        selection = self.listbox_history.curselection()
        if not selection:
            messagebox.showwarning("提示", "请先选择一条备份记录")
            return
        idx = selection[0]
        if idx >= len(self._history_data):
            return

        item = self._history_data[idx]
        answer = messagebox.askyesno(
            "删除备份",
            f"确定要删除此备份吗？\n\n"
            f"文件：{item['filename']}\n"
            f"时间：{item['time_str']}",
        )
        if not answer:
            return

        if self.backup_engine.delete_backup(item["path"]):
            self._refresh_history()
        else:
            messagebox.showerror("错误", "删除备份文件失败")

    # --------------------------------------------------------
    # 文件夹管理
    # --------------------------------------------------------

    def _browse_folder(self):
        """浏览选择监控文件夹"""
        folder = filedialog.askdirectory(title="选择要监控的项目文件夹")
        if folder:
            self.entry_folder.delete(0, tk.END)
            self.entry_folder.insert(0, folder)

    def _browse_backup_dir(self):
        """浏览选择备份存储位置"""
        folder = filedialog.askdirectory(title="选择备份存储目录")
        if folder:
            self.var_backup_dir.set(folder)

    def _add_folder(self):
        """添加监控文件夹到列表"""
        folder = self.entry_folder.get().strip()
        if not folder:
            messagebox.showwarning("提示", "请先选择或输入文件夹路径")
            return
        if not os.path.isdir(folder):
            messagebox.showerror("错误", f"文件夹不存在：\n{folder}")
            return
        # 检查是否已在列表中
        existing = self.listbox_folders.get(0, tk.END)
        if folder in existing:
            messagebox.showinfo("提示", "该文件夹已在监控列表中")
            return
        self.listbox_folders.insert(tk.END, folder)
        self.entry_folder.delete(0, tk.END)

    def _remove_folder(self):
        """从列表中移除选中的监控文件夹"""
        selection = self.listbox_folders.curselection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要移除的文件夹")
            return
        self.listbox_folders.delete(selection[0])

    def _get_monitor_folders(self):
        """获取当前所有监控文件夹路径列表"""
        return list(self.listbox_folders.get(0, tk.END))

    def _save_current_config(self):
        """保存当前配置到磁盘"""
        self.config["monitor_folders"] = self._get_monitor_folders()
        self.config["backup_dir"] = self.var_backup_dir.get()

        # 保存备份间隔
        interval_map = {"30秒": 30, "1分钟": 60, "2分钟": 120, "5分钟": 300}
        self.config["backup_interval"] = interval_map.get(self.var_interval.get(), 120)

        save_config(self.config)


# ============================================================
# 程序入口
# ============================================================

def main():
    """主函数：创建并运行 GUI"""
    root = tk.Tk()

    # 设置窗口图标（如果存在图标文件则加载）
    try:
        icon_path = os.path.join(os.path.dirname(__file__), "icon.ico")
        if os.path.exists(icon_path):
            root.iconbitmap(icon_path)
    except Exception:
        pass

    app = ClipGuardianApp(root)

    # 窗口关闭时的处理
    def on_close():
        app.guardian_running = False
        app._save_current_config()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
