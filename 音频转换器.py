import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import subprocess
import os
import sys
import signal
from pathlib import Path
import queue
import time
from concurrent.futures import ThreadPoolExecutor

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# ==================== 工具函数（模块级，便于在类外也能用） ====================

def get_app_dir():
    """
    获取“程序所在目录”。

    这是整个 FFmpeg 检测逻辑的地基。必须区分三种运行环境：

    1. 开发模式（直接跑 .py）
       -> __file__ 指向 音频转换器.py 本身，取其父目录即可。

    2. 打包模式（PyInstaller --onefile）
       -> 代码被解压到临时目录（如 C:/.../Temp/_MEIxxxx/），__file__ 指向那里，
          但用户是把 ffmpeg 文件夹放在【exe 旁边】的，所以要用 sys.argv[0]
          （即 exe 本身的路径）来定位。

    3. 打包模式（PyInstaller --onedir / 或用了 --add-binary 把 ffmpeg 打进去）
       -> 此时 ffmpeg 在 sys._MEIPASS 里，也要纳入搜索。

    返回值：Path 对象，代表“应该放 ffmpeg 的那个根目录”。
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后：sys.executable / sys.argv[0] 都指向真正的 exe
        # 优先用 sys.executable（最可靠），兜底用 argv[0]
        exe_path = getattr(sys, 'executable', None) or sys.argv[0]
        return Path(exe_path).parent.resolve()
    else:
        # 开发模式：脚本所在目录
        return Path(__file__).parent.resolve()


class BatchAudioConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("音频格式转换器(支持批量)")
        self.root.geometry("1280x720")
        self.root.resizable(True, True)

        # 程序根目录（exe 或脚本所在目录，启动时确定一次）
        self.app_dir = get_app_dir()

        # 支持的格式
        self.supported_formats = {
            'FLAC': '.flac',
            'MP3': '.mp3',
            'WAV': '.wav',
            'OGG': '.ogg',
            'AAC': '.aac',
            'M4A': '.m4a',
            'WMA': '.wma',
            'AIFF': '.aiff',
            'ALAC': '.m4a'
        }

        # 转换队列
        self.conversion_queue = []
        self.is_converting = False
        self.cancelled = False
        self.conversion_stats = {"success": 0, "failed": 0, "total": 0}

        # 线程池 & 进度队列（UI 线程通过队列更新，避免跨线程操作 Tk）
        self.executor = None
        self.progress_queue = queue.Queue()

        # 活跃 FFmpeg 子进程（用于真正取消时 kill）
        self.active_processes = []

        # FFmpeg / ffprobe 可执行文件路径（默认走系统 PATH，检测后可能被替换为本地路径）
        self.ffmpeg_path = 'ffmpeg'
        self.ffprobe_path = 'ffprobe'

        # 设置主题
        style = ttk.Style()
        style.theme_use('clam')

        self.setup_ui()
        self.check_ffmpeg()
        self.setup_bindings()

        # 定期检查进度更新
        self.check_progress_updates()

    # ==================== FFmpeg 环境 ====================

    def check_ffmpeg(self):
        search_dirs = self._get_ffmpeg_search_dirs()

        self.log(f"程序目录: {self.app_dir}")
        self.log(f"FFmpeg 搜索路径（按顺序）:")
        for i, d in enumerate(search_dirs, 1):
            self.log(f"  {i}. {d}")

        ffmpeg_found = self._find_executable('ffmpeg', search_dirs)
        ffprobe_found = self._find_executable('ffprobe', search_dirs)

        if ffmpeg_found and ffprobe_found:
            # 判断是否来自本地相对路径
            if ffmpeg_found != 'ffmpeg':
                self.ffmpeg_path = ffmpeg_found
                self.ffprobe_path = ffprobe_found
                self.log(f"✓ 使用本地 FFmpeg: {ffmpeg_found}")
            else:
                self.ffmpeg_path = 'ffmpeg'
                self.ffprobe_path = 'ffprobe'
                self.log("✓ 使用系统 PATH 中的 FFmpeg")
            return True
        else:
            missing = []
            if not ffmpeg_found:
                missing.append('ffmpeg')
            if not ffprobe_found:
                missing.append('ffprobe')
            self.show_warning(
                "FFmpeg 检测",
                f"未找到 {'、'.join(missing)}。\n\n"
                f"请将 FFmpeg 放入程序目录下的 ffmpeg/bin/ 文件夹中，\n"
                f"或确保已添加到系统 PATH 环境变量。\n\n"
                f"程序目录：{self.app_dir}"
            )
            return False

    def _get_ffmpeg_search_dirs(self):
        """
        获取 FFmpeg 搜索目录列表（按优先级排列）。
        关键点：用 self.app_dir（来自 get_app_dir）作为“程序目录”，
        而不是 Path(__file__).parent —— 这样打包成 exe 后也能正确定位
        到 exe 旁边的 ffmpeg 文件夹。
        """
        dirs = []
        base = Path(self.app_dir)  # 防御性转换，确保支持字符串与 Path

        # 1. <程序目录>/ffmpeg/bin/   ← 推荐布局：把 ffmpeg 整个文件夹放 exe 旁
        dirs.append(base / 'ffmpeg' / 'bin')

        # 2. <程序目录>/ffmpeg/       ← ffmpeg.exe 直接放在 ffmpeg 根目录
        dirs.append(base / 'ffmpeg')

        # 3. <程序目录>               ← exe 同级目录
        dirs.append(base)

        # 4. <上级目录>/ffmpeg/bin/   ← 开发时常见结构
        dirs.append(base.parent / 'ffmpeg' / 'bin')

        # 5. <程序目录>/bin/
        dirs.append(base / 'bin')

        # 6. PyInstaller 打包后，若用 --add-binary 把 ffmpeg 打进 exe
        #    （--onefile 时资源在 _MEIPASS；--onedir 时一般仍在 base）
        if hasattr(sys, '_MEIPASS'):
            meipass = Path(sys._MEIPASS)
            dirs.append(meipass / 'ffmpeg' / 'bin')
            dirs.append(meipass / 'ffmpeg')
            dirs.append(meipass)

        return dirs

    def _find_executable(self, name, search_dirs):
        """
        在系统 PATH 及指定目录列表中查找可执行文件。
        返回找到的完整路径字符串；若来自系统 PATH 则返回命令名本身（如 'ffmpeg'）；
        均未找到返回 None。
        """
        # 先试系统 PATH（用完整命令名，避免被同名脚本干扰）
        if self._can_run(name):
            return name

        # 再试相对路径目录
        exe_names = [name, f'{name}.exe'] if os.name == 'nt' else [name]
        for directory in search_dirs:
            if not directory or not directory.exists():
                continue
            for exe_name in exe_names:
                exe_path = directory / exe_name
                if exe_path.exists() and os.access(exe_path, os.X_OK):
                    # 验证能正常运行（用完整路径）
                    if self._can_run(str(exe_path)):
                        return str(exe_path)

        return None

    @staticmethod
    def _can_run(command):
        """判断一个命令（命令名或完整路径）能否正常执行 -version"""
        try:
            result = subprocess.run([command, '-version'],
                                    capture_output=True, text=True, timeout=3,
                                    creationflags=getattr(os, 'CREATE_NO_WINDOW', 0))
            # ffmpeg -version 返回 0，且输出里包含 "ffmpeg" 字样，避免误判
            return result.returncode == 0 and 'ffmpeg' in result.stdout.lower()
        except Exception:
            return False

    def diagnose_ffmpeg(self):
        """
        诊断 FFmpeg 环境（可在 UI 上加个按钮调用，便于排查）。
        打印当前模式、程序目录、PATH 中的 ffmpeg、各候选目录是否存在等。
        """
        self.log("========== FFmpeg 诊断 ==========")
        self.log(f"运行模式: {'打包(exe)' if getattr(sys, 'frozen', False) else '开发(.py)'}")
        self.log(f"程序目录: {self.app_dir}")
        self.log(f"当前 ffmpeg_path: {self.ffmpeg_path}")
        self.log(f"当前 ffprobe_path: {self.ffprobe_path}")
        self.log(f"sys._MEIPASS: {getattr(sys, '_MEIPASS', '无')}")

        # 系统 PATH 里有哪些 ffmpeg
        path_ffmpeg = self._which('ffmpeg')
        path_ffprobe = self._which('ffprobe')
        self.log(f"系统 PATH 中的 ffmpeg: {path_ffmpeg or '未找到'}")
        self.log(f"系统 PATH 中的 ffprobe: {path_ffprobe or '未找到'}")

        # 各候选目录检查
        for d in self._get_ffmpeg_search_dirs():
            exists = d.exists()
            has_ffmpeg = (d / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')).exists()
            self.log(f"  {'✓' if exists else '✗'} {d}  (ffmpeg={'有' if has_ffmpeg else '无'})")
        self.log("===============================")

        # 重新检测一次
        self.check_ffmpeg()

    @staticmethod
    def _which(name):
        """模拟 which/where，在系统 PATH 中查找可执行文件完整路径"""
        exe = f'{name}.exe' if os.name == 'nt' else name
        for p in os.environ.get('PATH', '').split(os.pathsep):
            try:
                candidate = Path(p) / exe
                if candidate.exists():
                    return str(candidate)
            except Exception:
                continue
        return None

    # ==================== UI ====================

    def setup_ui(self):
        """设置用户界面"""
        main_container = ttk.Frame(self.root, padding="10")
        main_container.pack(fill=tk.BOTH, expand=True)

        header_frame = ttk.Frame(main_container)
        header_frame.pack(fill=tk.X, pady=(0, 10))

        title_label = ttk.Label(header_frame,
                                text="🎵 音频格式转换器(支持批量)",
                                font=('Arial', 24, 'bold'),
                                foreground="#2c3e50")
        title_label.pack(side=tk.LEFT)

        # 两列布局
        content_frame = ttk.Frame(main_container)
        content_frame.pack(fill=tk.BOTH, expand=True)

        # 左侧控制面板
        left_panel = ttk.LabelFrame(content_frame, text="控制面板", padding="15")
        left_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(0, 10))

        # 右侧
        right_panel = ttk.Frame(content_frame)
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # ---------- 1. 文件选择 ----------
        batch_frame = ttk.LabelFrame(left_panel, text="1. 批量文件选择", padding="10")
        batch_frame.pack(fill=tk.X, pady=(0, 15))

        ttk.Button(batch_frame, text="📁 导入文件夹",
                   command=self.import_folder, width=20).pack(fill=tk.X, pady=(0, 5))
        ttk.Button(batch_frame, text="📄 选择多个文件",
                   command=self.select_multiple_files, width=20).pack(fill=tk.X, pady=(0, 10))
        ttk.Button(batch_frame, text="🗑️ 清空文件列表",
                   command=self.clear_file_list, width=20).pack(fill=tk.X)

        # ---------- 2. 转换设置 ----------
        settings_frame = ttk.LabelFrame(left_panel, text="2. 转换设置", padding="10")
        settings_frame.pack(fill=tk.X, pady=(0, 15))

        ttk.Label(settings_frame, text="目标格式:", font=('Arial', 10, 'bold')).pack(anchor=tk.W, pady=(0, 5))
        self.format_var = tk.StringVar(value='MP3')
        ttk.Combobox(settings_frame, textvariable=self.format_var,
                     values=list(self.supported_formats.keys()),
                     state='readonly', width=18).pack(fill=tk.X, pady=(0, 10))

        ttk.Label(settings_frame, text="输出质量:", font=('Arial', 10, 'bold')).pack(anchor=tk.W, pady=(0, 5))
        self.quality_var = tk.StringVar(value='320k')
        ttk.Combobox(settings_frame, textvariable=self.quality_var,
                     values=['64k', '128k', '192k', '256k', '320k', '无损'],
                     state='readonly', width=18).pack(fill=tk.X, pady=(0, 10))

        ttk.Label(settings_frame, text="输出目录:", font=('Arial', 10, 'bold')).pack(anchor=tk.W, pady=(0, 5))
        dir_frame = ttk.Frame(settings_frame)
        dir_frame.pack(fill=tk.X)
        self.output_dir_var = tk.StringVar(value=str(Path.home() / "Desktop"))
        ttk.Entry(dir_frame, textvariable=self.output_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(dir_frame, text="📂", command=self.select_output_dir, width=3).pack(side=tk.RIGHT)

        # ---------- 3. 转换控制 ----------
        control_frame = ttk.LabelFrame(left_panel, text="3. 转换控制", padding="10")
        control_frame.pack(fill=tk.X)

        self.file_count_var = tk.StringVar(value="等待添加文件...")
        ttk.Label(control_frame, textvariable=self.file_count_var,
                  font=('Arial', 10), foreground="#3498db").pack(pady=(0, 10))

        self.convert_btn = ttk.Button(control_frame, text="🚀 开始转换",
                                      command=self.start_batch_conversion, state='disabled')
        self.convert_btn.pack(fill=tk.X, pady=(0, 5))

        # 取消按钮（专注做“真正取消”）
        self.cancel_btn = ttk.Button(control_frame, text="⏹️ 取消转换",
                                     command=self.cancel_conversion, state='disabled')
        self.cancel_btn.pack(fill=tk.X)

        # FFmpeg 诊断按钮（排查找不到 ffmpeg 的神器）
        ttk.Button(control_frame, text="🔍 诊断 FFmpeg",
                   command=self.diagnose_ffmpeg).pack(fill=tk.X, pady=(5, 0))

        # 状态指示灯
        status_frame = ttk.Frame(left_panel)
        status_frame.pack(fill=tk.X, pady=(15, 0))
        self.status_indicator = ttk.Label(status_frame, text="●", foreground="green", font=('Arial', 16))
        self.status_indicator.pack(side=tk.LEFT, padx=(0, 10))
        self.status_label = ttk.Label(status_frame, text="就绪", font=('Arial', 10))
        self.status_label.pack(side=tk.LEFT)

        # ---------- 右侧 Notebook ----------
        notebook = ttk.Notebook(right_panel)
        notebook.pack(fill=tk.BOTH, expand=True)

        # 文件列表
        file_tab = ttk.Frame(notebook)
        notebook.add(file_tab, text="📋 文件列表")
        columns = ('序号', '文件名', '格式', '大小', '状态')
        self.file_tree = ttk.Treeview(file_tab, columns=columns, show='headings', height=15)
        for col in columns:
            self.file_tree.heading(col, text=col)
            self.file_tree.column(col, width=100)
        self.file_tree.column('文件名', width=250)
        self.file_tree.column('状态', width=100)
        tree_scroll = ttk.Scrollbar(file_tab, orient=tk.VERTICAL, command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=tree_scroll.set)
        self.file_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 转换日志（只读）
        log_tab = ttk.Frame(notebook)
        notebook.add(log_tab, text="📝 转换日志")
        self.log_text = scrolledtext.ScrolledText(log_tab,
                                                  height=20, wrap=tk.WORD,
                                                  font=('Consolas', 9),
                                                  state='disabled')  # 只读
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 统计信息（只读）
        stats_tab = ttk.Frame(notebook)
        notebook.add(stats_tab, text="📊 统计信息")
        self.stats_text = scrolledtext.ScrolledText(stats_tab,
                                                    height=20, wrap=tk.WORD,
                                                    font=('Arial', 10),
                                                    state='disabled')  # 只读
        self.stats_text.pack(fill=tk.BOTH, expand=True)

        # ---------- 底部进度 ----------
        bottom_frame = ttk.Frame(main_container)
        bottom_frame.pack(fill=tk.X, pady=(10, 0))

        self.overall_progress_var = tk.DoubleVar()
        ttk.Progressbar(bottom_frame, variable=self.overall_progress_var,
                        maximum=100, length=600).pack(fill=tk.X, pady=(0, 5))

        current_frame = ttk.Frame(bottom_frame)
        current_frame.pack(fill=tk.X)
        ttk.Label(current_frame, text="当前文件:").pack(side=tk.LEFT)
        self.current_file_var = tk.StringVar(value="无")
        ttk.Label(current_frame, textvariable=self.current_file_var, foreground="blue").pack(side=tk.LEFT, padx=(5, 20))
        self.current_progress_var = tk.DoubleVar()
        ttk.Progressbar(current_frame, variable=self.current_progress_var,
                        maximum=100, length=300).pack(side=tk.LEFT, fill=tk.X, expand=True)

        stats_frame = ttk.Frame(bottom_frame)
        stats_frame.pack(fill=tk.X, pady=(5, 0))
        self.stats_vars = {
            'total': tk.StringVar(value="总计: 0"),
            'success': tk.StringVar(value="成功: 0"),
            'failed': tk.StringVar(value="失败: 0"),
            'remaining': tk.StringVar(value="剩余: 0")
        }
        for var in self.stats_vars.values():
            ttk.Label(stats_frame, textvariable=var, font=('Arial', 9)).pack(side=tk.LEFT, padx=10)

    def setup_bindings(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # ==================== 文件导入 ====================

    def import_folder(self):
        folder_path = filedialog.askdirectory(title="选择音频文件夹")
        if not folder_path:
            return
        audio_extensions = {'.flac', '.mp3', '.wav', '.ogg', '.aac', '.m4a', '.wma', '.aiff'}
        files = []
        for ext in audio_extensions:
            files.extend(Path(folder_path).glob(f"*{ext}"))
            files.extend(Path(folder_path).glob(f"*{ext.upper()}"))
        if not files:
            self.show_info("导入结果", "在文件夹中未找到支持的音频文件")
            return
        self.add_files_to_list(files)
        self.show_info("导入成功", f"成功导入 {len(files)} 个音频文件")

    def select_multiple_files(self):
        filetypes = [("音频文件", "*.flac *.mp3 *.wav *.ogg *.aac *.m4a *.wma *.aiff"),
                     ("所有文件", "*.*")]
        files = filedialog.askopenfilenames(title="选择音频文件", filetypes=filetypes)
        if files:
            self.add_files_to_list([Path(f) for f in files])

    def add_files_to_list(self, files):
        existing_paths = [item['path'] for item in self.conversion_queue]
        for file_path in files:
            if file_path in existing_paths:
                continue
            try:
                size = os.path.getsize(file_path) / (1024 * 1024)
                self.conversion_queue.append({
                    'path': file_path,
                    'name': file_path.name,
                    'ext': file_path.suffix.upper(),
                    'size': f"{size:.2f} MB",
                    'status': '等待',
                    'tree_id': None
                })
            except Exception:
                continue
        self.update_file_list()
        self.update_file_count()

    # ==================== 列表 / 按钮状态 ====================

    def update_file_list(self):
        for item in self.file_tree.get_children():
            self.file_tree.delete(item)
        for i, item in enumerate(self.conversion_queue, 1):
            tree_id = self.file_tree.insert('', tk.END, values=(
                i, item['name'], item['ext'], item['size'], item['status']))
            item['tree_id'] = tree_id
        self.update_control_buttons()

    def update_file_count(self):
        total = len(self.conversion_queue)
        waiting = sum(1 for item in self.conversion_queue if item['status'] == '等待')
        if total == 0:
            self.file_count_var.set("等待添加文件...")
            self.convert_btn.config(state='disabled', text="🚀 开始转换")
        else:
            self.file_count_var.set(f"已添加 {total} 个文件 ({waiting} 个等待中)")
            self.convert_btn.config(text="🚀 开始转换" if total == 1
                                    else f"🚀 开始批量转换 ({waiting}个文件)")
            self.convert_btn.config(state='normal' if waiting > 0 else 'disabled')

    def update_control_buttons(self):
        waiting = sum(1 for item in self.conversion_queue if item['status'] == '等待')
        if self.is_converting:
            self.convert_btn.config(state='disabled',
                                    text="转换中..." if len(self.conversion_queue) == 1 else "批量转换中...")
            self.cancel_btn.config(state='normal')
        elif waiting > 0:
            self.convert_btn.config(state='normal')
            self.cancel_btn.config(state='disabled')
        else:
            self.convert_btn.config(state='disabled')
            self.cancel_btn.config(state='disabled')

    def clear_file_list(self):
        if self.is_converting:
            self.show_warning("操作被拒绝", "转换过程中无法清空列表")
            return
        self.conversion_queue.clear()
        self.update_file_list()
        self.update_file_count()
        self.log("已清空文件列表")

    def select_output_dir(self):
        directory = filedialog.askdirectory(title="选择输出目录")
        if directory:
            self.output_dir_var.set(directory)
            self.log(f"输出目录设置为: {directory}")

    # ==================== 转换流程 ====================

    def start_batch_conversion(self):
        if self.is_converting:
            return

        # 转换开始前再次确认 FFmpeg 可用
        if not self._is_ffmpeg_available():
            self.show_warning("无法转换",
                              "未检测到 FFmpeg，请先配置 FFmpeg 后再试。\n\n"
                              "可点击「🔍 诊断 FFmpeg」查看详细搜索路径。")
            return

        output_dir = Path(self.output_dir_var.get())
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            self.show_error("错误", "无法创建输出目录")
            return

        self.reset_stats()
        self.cancelled = False

        self.executor = ThreadPoolExecutor(max_workers=1)
        self.is_converting = True

        total = len([item for item in self.conversion_queue if item['status'] == '等待'])
        self.log("开始单个文件转换" if total == 1 else f"开始批量转换 {total} 个文件")
        self.status_label.config(text="转换中...")
        self.status_indicator.config(foreground="orange")

        import threading
        threading.Thread(target=self.run_batch_conversion, daemon=True).start()
        self.update_control_buttons()

    def _is_ffmpeg_available(self):
        """快速校验当前记录的 FFmpeg / ffprobe 路径是否仍可调用"""
        try:
            r1 = subprocess.run([self.ffmpeg_path, '-version'],
                                capture_output=True, text=True, timeout=2,
                                creationflags=getattr(os, 'CREATE_NO_WINDOW', 0))
            r2 = subprocess.run([self.ffprobe_path, '-version'],
                                capture_output=True, text=True, timeout=2,
                                creationflags=getattr(os, 'CREATE_NO_WINDOW', 0))
            return r1.returncode == 0 and r2.returncode == 0
        except Exception:
            return False

    def run_batch_conversion(self):
        files_to_convert = [item for item in self.conversion_queue if item['status'] == '等待']
        if not files_to_convert:
            self.log("没有需要转换的文件")
            self.finish_conversion()
            return

        self.conversion_stats['total'] = len(files_to_convert)
        self.update_stats_display()

        # 逐个提交，便于取消时立即生效
        for item in files_to_convert:
            if self.cancelled:
                break
            future = self.executor.submit(self.convert_single_file, item)
            try:
                result = future.result()
            except Exception:
                result = False
            if result:
                self.conversion_stats['success'] += 1
            else:
                self.conversion_stats['failed'] += 1
            self.update_stats_display()

        self.finish_conversion()

    # ==================== 单个文件转换（真实进度） ====================

    def convert_single_file(self, item):
        """转换单个文件，实时解析 FFmpeg 进度"""
        if self.cancelled:
            return False

        try:
            item['status'] = '转换中'
            self.progress_queue.put(('status', item, '转换中'))
            self.progress_queue.put(('current_file', item['name']))
            self.progress_queue.put(('current_progress', 0))

            output_dir = Path(self.output_dir_var.get())
            target_format = self.format_var.get()
            output_filename = Path(item['path']).stem + self.supported_formats[target_format]
            output_file = output_dir / output_filename

            # 若输出文件已存在，跳过以避免覆盖
            if output_file.exists():
                item['status'] = '⏭️ 已跳过'
                self.log(f"跳过（已存在）: {item['name']}")
                return True

            cmd = self.build_ffmpeg_command(str(item['path']), str(output_file))
            duration = self.get_audio_duration(str(item['path']))

            # 用 Popen 实时读取 stderr
            process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.PIPE, text=True, bufsize=1,
                                       creationflags=getattr(os, 'CREATE_NO_WINDOW', 0))
            self.active_processes.append(process)

            last_percent = -1
            try:
                for line in process.stderr:
                    if self.cancelled:
                        process.terminate()
                        break

                    # 解析 time=HH:MM:SS.ss
                    if 'time=' in line:
                        try:
                            time_str = line.split('time=')[1].split()[0]
                            current_sec = self.parse_time(time_str)
                            if duration > 0:
                                percent = min(current_sec / duration * 100, 100)
                                # 避免过于频繁的更新
                                if int(percent) > last_percent:
                                    last_percent = int(percent)
                                    self.progress_queue.put(('current_progress', percent))
                        except Exception:
                            pass

                process.wait()
            finally:
                if process in self.active_processes:
                    self.active_processes.remove(process)

            if self.cancelled:
                item['status'] = '⏹️ 已取消'
                self.log(f"取消: {item['name']}")
                if output_file.exists():
                    try:
                        output_file.unlink()
                    except Exception:
                        pass
                return False

            if process.returncode == 0:
                item['status'] = '✓ 成功'
                self.log(f"成功: {item['name']} → {target_format}")
                self.progress_queue.put(('current_progress', 100))
                return True
            else:
                item['status'] = '✗ 失败'
                self.log(f"失败: {item['name']}")
                return False

        except Exception as e:
            item['status'] = '❌ 错误'
            self.log(f"错误: {item['name']} - {str(e)}")
            return False
        finally:
            self.progress_queue.put(('item_status', item))
            self.progress_queue.put(('current_progress', 0))
            self.progress_queue.put(('current_file', "无"))

    def get_audio_duration(self, filepath):
        """用 ffprobe 获取音频时长（秒）"""
        try:
            cmd = [self.ffprobe_path, '-v', 'quiet', '-show_entries', 'format=duration',
                   '-of', 'csv=p=0', filepath]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                                    creationflags=getattr(os, 'CREATE_NO_WINDOW', 0))
            return float(result.stdout.strip())
        except Exception:
            return 0

    @staticmethod
    def parse_time(time_str):
        """把 HH:MM:SS.ss 或 S.ss 解析为秒"""
        parts = time_str.split(':')
        try:
            if len(parts) == 3:
                h, m, s = parts
                return int(h) * 3600 + int(m) * 60 + float(s)
            elif len(parts) == 2:
                m, s = parts
                return int(m) * 60 + float(s)
            else:
                return float(parts[0])
        except ValueError:
            return 0

    def build_ffmpeg_command(self, input_file, output_file):
        """构建 FFmpeg 命令（使用 self.ffmpeg_path 以支持本地相对路径）"""
        cmd = [self.ffmpeg_path, '-i', input_file, '-y', '-hide_banner', '-progress', 'pipe:1']
        target_format = self.format_var.get()
        quality = self.quality_var.get()

        if target_format == 'MP3':
            cmd.extend(['-codec:a', 'libmp3lame'])
            if quality != '无损':
                cmd.extend(['-b:a', quality])
            else:
                cmd.extend(['-q:a', '0'])
        elif target_format == 'WAV':
            cmd.extend(['-codec:a', 'pcm_s16le'])
        elif target_format == 'FLAC':
            cmd.extend(['-codec:a', 'flac'])
            if quality != '无损':
                cmd.extend(['-compression_level', '8'])
        elif target_format == 'OGG':
            cmd.extend(['-codec:a', 'libvorbis'])
            if quality != '无损':
                q_map = {'64k': '2', '128k': '4', '192k': '6', '256k': '8', '320k': '10'}
                cmd.extend(['-q:a', q_map.get(quality, '6')])
        elif target_format == 'AAC':
            cmd.extend(['-codec:a', 'aac'])
            if quality != '无损':
                cmd.extend(['-b:a', quality])

        cmd.append(output_file)
        return cmd

    # ==================== 取消（真正杀进程） ====================

    def cancel_conversion(self):
        """真正取消：置标志 + 杀掉所有 FFmpeg 子进程 + 停线程池"""
        if not self.is_converting:
            return
        self.cancelled = True
        self.log("正在取消转换，等待当前文件结束...")

        # 立即终止所有活跃的 FFmpeg 进程
        for process in list(self.active_processes):
            try:
                self._kill_process_tree(process)
            except Exception:
                pass
        self.active_processes.clear()

        if self.executor:
            self.executor.shutdown(wait=False, cancel_futures=True)

        self.status_label.config(text="正在停止...")
        self.cancel_btn.config(state='disabled')

    @staticmethod
    def _kill_process_tree(process):
        """跨平台杀掉进程及其子进程"""
        if os.name == 'nt':
            if HAS_PSUTIL:
                try:
                    parent = psutil.Process(process.pid)
                    for child in parent.children(recursive=True):
                        child.kill()
                    parent.kill()
                    return
                except Exception:
                    pass
            # Windows 无 psutil 时退路
            try:
                os.kill(process.pid, signal.SIGTERM)
            except Exception:
                pass
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()

    # ==================== 完成 / 统计 ====================

    def finish_conversion(self):
        self.is_converting = False
        self.active_processes.clear()

        self.status_label.config(text="就绪")
        self.status_indicator.config(foreground="green")
        self.update_control_buttons()

        success = self.conversion_stats['success']
        total = self.conversion_stats['total']

        if total == 0:
            self.log("无任务可执行")
            return

        if total == 1:
            self.log("转换完成！" if success == 1 else "转换失败")
        else:
            self.log(f"转换结束！成功: {success}/{total} 个文件")
            if self.cancelled:
                self.log("（由用户取消）")

        self.update_stats_display()

    def reset_stats(self):
        self.conversion_stats = {"success": 0, "failed": 0, "total": 0}
        self.update_stats_display()

    def update_stats_display(self):
        total = self.conversion_stats['total']
        success = self.conversion_stats['success']
        failed = self.conversion_stats['failed']
        remaining = total - success - failed

        self.stats_vars['total'].set(f"总计: {total}")
        self.stats_vars['success'].set(f"成功: {success}")
        self.stats_vars['failed'].set(f"失败: {failed}")
        self.stats_vars['remaining'].set(f"剩余: {remaining}")

        # 统计页也通过 disable/enable 安全写入（可能在非 UI 线程调用）
        stats_text = (
            f"═══════════════════════════════════\n"
            f"        转换统计信息\n"
            f"═══════════════════════════════════\n"
            f" 总计文件: {total}\n"
            f" 成功转换: {success}\n"
            f" 转换失败: {failed}\n"
            f" 等待转换: {remaining}\n"
            f" 成功率: {success / total * 100 if total > 0 else 0:.1f}%\n"
            f"═══════════════════════════════════\n"
        )
        self._set_text_readonly(self.stats_text, stats_text)

    # ==================== 进度队列消费（UI 线程） ====================

    def check_progress_updates(self):
        """定时从队列取消息，更新 UI（只在主线程操作 Tk）"""
        try:
            while not self.progress_queue.empty():
                msg = self.progress_queue.get_nowait()
                self._dispatch_progress(msg)
        except Exception:
            pass
        self.root.after(100, self.check_progress_updates)

    def _dispatch_progress(self, msg):
        msg_type = msg[0]
        if msg_type == 'current_progress':
            self.current_progress_var.set(msg[1])
        elif msg_type == 'overall_progress':
            self.overall_progress_var.set(msg[1])
        elif msg_type == 'current_file':
            self.current_file_var.set(msg[1])
        elif msg_type == 'item_status':
            self.update_item_status(msg[1])
        elif msg_type == 'status':
            self.update_item_status(msg[1], force_status=msg[2])

    def update_item_status(self, item, force_status=None):
        status = force_status or item.get('status', '等待')
        if item.get('tree_id'):
            vals = self.file_tree.item(item['tree_id'])['values']
            if vals:
                self.file_tree.item(item['tree_id'], values=(
                    vals[0], item['name'], item['ext'], item['size'], status))
        self._update_overall_progress()

    def _update_overall_progress(self):
        total = len(self.conversion_queue)
        completed = sum(1 for item in self.conversion_queue
                        if item['status'] in ['✓ 成功', '✗ 失败', '⏱️ 超时',
                                              '❌ 错误', '⏹️ 已取消', '⏭️ 已跳过'])
        if total > 0:
            self.overall_progress_var.set((completed / total) * 100)

    # ==================== 日志（只读） ====================

    def log(self, message):
        """日志只读：临时 enable → 写入 → 恢复 disabled"""
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        log_entry = f"[{timestamp}] {message}\n"
        self._append_text_readonly(self.log_text, log_entry)

    @staticmethod
    def _append_text_readonly(text_widget, content):
        try:
            text_widget.config(state='normal')
            text_widget.insert(tk.END, content)
            text_widget.see(tk.END)
            text_widget.config(state='disabled')
        except tk.TclError:
            pass

    @staticmethod
    def _set_text_readonly(text_widget, content):
        try:
            text_widget.config(state='normal')
            text_widget.delete(1.0, tk.END)
            text_widget.insert(1.0, content)
            text_widget.config(state='disabled')
        except tk.TclError:
            pass

    # ==================== 弹窗 ====================

    def show_error(self, title, message):
        messagebox.showerror(title, message)
        self.log(f"[错误] {title}: {message}")

    def show_warning(self, title, message):
        messagebox.showwarning(title, message)
        self.log(f"[警告] {title}: {message}")

    def show_info(self, title, message):
        messagebox.showinfo(title, message)
        self.log(f"[信息] {title}: {message}")

    # ==================== 关闭 ====================

    def on_closing(self):
        if self.is_converting:
            self.cancel_conversion()
        # 等待一小段让进程退出
        if self.executor:
            self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main():
    root = tk.Tk()
    app = BatchAudioConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()