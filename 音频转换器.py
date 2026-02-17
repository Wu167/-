import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import subprocess
import os
import threading
from pathlib import Path
import queue
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

class BatchAudioConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("音频批量格式转换器 v2.1")
        self.root.geometry("900x700")
        self.root.resizable(True, True)
        
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
        self.current_converting = None
        self.is_converting = False
        self.conversion_stats = {"success": 0, "failed": 0, "total": 0}
        
        # 线程池
        self.executor = None
        self.progress_queue = queue.Queue()
        
        # 设置主题
        style = ttk.Style()
        style.theme_use('clam')
        
        self.setup_ui()
        self.check_ffmpeg()
        self.setup_bindings()
        
        # 定期检查进度更新
        self.check_progress_updates()
    
    def check_ffmpeg(self):
        """检查FFmpeg是否可用"""
        try:
            result = subprocess.run(['ffmpeg', '-version'], 
                                  capture_output=True, text=True, timeout=2)
            if result.returncode != 0:
                self.show_warning("FFmpeg检测", 
                                "FFmpeg可能未正确安装，部分功能可能受限")
                return False
            return True
        except:
            self.show_warning("FFmpeg检测", 
                            "未检测到FFmpeg，请确保已安装并添加到PATH")
            return False
    
    def setup_ui(self):
        """设置全新的用户界面"""
        # 创建主容器
        main_container = ttk.Frame(self.root, padding="10")
        main_container.pack(fill=tk.BOTH, expand=True)
        
        # 顶部标题区域
        header_frame = ttk.Frame(main_container)
        header_frame.pack(fill=tk.X, pady=(0, 10))
        
        title_label = ttk.Label(header_frame, 
                               text="🎵 音频批量格式转换器", 
                               font=('Arial', 24, 'bold'),
                               foreground="#2c3e50")
        title_label.pack(side=tk.LEFT)
        
        version_label = ttk.Label(header_frame, 
                                 text="v2.1",
                                 font=('Arial', 12),
                                 foreground="#7f8c8d")
        version_label.pack(side=tk.LEFT, padx=(10, 0), pady=(10, 0))
        
        # 创建两列布局
        content_frame = ttk.Frame(main_container)
        content_frame.pack(fill=tk.BOTH, expand=True)
        
        # 左侧控制面板
        left_panel = ttk.LabelFrame(content_frame, text="控制面板", padding="15")
        left_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(0, 10))
        
        # 右侧文件列表和日志
        right_panel = ttk.Frame(content_frame)
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        # ========== 左侧控制面板内容 ==========
        
        # 1. 批量文件选择
        batch_frame = ttk.LabelFrame(left_panel, text="1. 批量文件选择", padding="10")
        batch_frame.pack(fill=tk.X, pady=(0, 15))
        
        # 文件夹批量导入
        folder_btn = ttk.Button(batch_frame, 
                               text="📁 导入文件夹",
                               command=self.import_folder,
                               width=20)
        folder_btn.pack(fill=tk.X, pady=(0, 5))
        
        # 多文件选择
        files_btn = ttk.Button(batch_frame,
                              text="📄 选择多个文件",
                              command=self.select_multiple_files,
                              width=20)
        files_btn.pack(fill=tk.X, pady=(0, 10))
        
        # 清空列表
        clear_list_btn = ttk.Button(batch_frame,
                                   text="🗑️ 清空文件列表",
                                   command=self.clear_file_list,
                                   width=20)
        clear_list_btn.pack(fill=tk.X)
        
        # 2. 转换设置
        settings_frame = ttk.LabelFrame(left_panel, text="2. 转换设置", padding="10")
        settings_frame.pack(fill=tk.X, pady=(0, 15))
        
        # 目标格式
        ttk.Label(settings_frame, text="目标格式:", font=('Arial', 10, 'bold')).pack(anchor=tk.W, pady=(0, 5))
        self.format_var = tk.StringVar(value='MP3')
        format_combo = ttk.Combobox(settings_frame,
                                   textvariable=self.format_var,
                                   values=list(self.supported_formats.keys()),
                                   state='readonly',
                                   width=18)
        format_combo.pack(fill=tk.X, pady=(0, 10))
        
        # 质量设置
        ttk.Label(settings_frame, text="输出质量:", font=('Arial', 10, 'bold')).pack(anchor=tk.W, pady=(0, 5))
        self.quality_var = tk.StringVar(value='320k')
        quality_combo = ttk.Combobox(settings_frame,
                                    textvariable=self.quality_var,
                                    values=['64k', '128k', '192k', '256k', '320k', '无损'],
                                    state='readonly',
                                    width=18)
        quality_combo.pack(fill=tk.X, pady=(0, 10))
        
        # 输出目录
        ttk.Label(settings_frame, text="输出目录:", font=('Arial', 10, 'bold')).pack(anchor=tk.W, pady=(0, 5))
        
        dir_frame = ttk.Frame(settings_frame)
        dir_frame.pack(fill=tk.X)
        
        self.output_dir_var = tk.StringVar(value=str(Path.home() / "ConvertedAudio"))
        output_entry = ttk.Entry(dir_frame, textvariable=self.output_dir_var)
        output_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        output_dir_btn = ttk.Button(dir_frame, 
                                   text="📂",
                                   command=self.select_output_dir,
                                   width=3)
        output_dir_btn.pack(side=tk.RIGHT)
        
        # 3. 转换控制
        control_frame = ttk.LabelFrame(left_panel, text="3. 转换控制", padding="10")
        control_frame.pack(fill=tk.X)
        
        # 文件计数显示
        self.file_count_var = tk.StringVar(value="等待添加文件...")
        file_count_label = ttk.Label(control_frame, 
                                    textvariable=self.file_count_var,
                                    font=('Arial', 10),
                                    foreground="#3498db")
        file_count_label.pack(pady=(0, 10))
        
        # 转换按钮 - 初始显示"开始转换"
        self.convert_btn = ttk.Button(control_frame,
                                     text="🚀 开始转换",
                                     command=self.start_batch_conversion,
                                     state='disabled',
                                     style='Accent.TButton')
        self.convert_btn.pack(fill=tk.X, pady=(0, 5))
        
        # 暂停/继续按钮
        self.pause_btn = ttk.Button(control_frame,
                                   text="⏸️ 暂停",
                                   command=self.toggle_pause,
                                   state='disabled')
        self.pause_btn.pack(fill=tk.X, pady=(0, 5))
        
        # 停止按钮
        self.stop_btn = ttk.Button(control_frame,
                                  text="⏹️ 停止",
                                  command=self.stop_conversion,
                                  state='disabled')
        self.stop_btn.pack(fill=tk.X)
        
        # 状态指示器
        status_frame = ttk.Frame(left_panel)
        status_frame.pack(fill=tk.X, pady=(15, 0))
        
        self.status_indicator = ttk.Label(status_frame, text="●", foreground="green", font=('Arial', 16))
        self.status_indicator.pack(side=tk.LEFT, padx=(0, 10))
        
        self.status_label = ttk.Label(status_frame, text="就绪", font=('Arial', 10))
        self.status_label.pack(side=tk.LEFT)
        
        # ========== 右侧面板内容 ==========
        
        # 创建Notebook选项卡
        notebook = ttk.Notebook(right_panel)
        notebook.pack(fill=tk.BOTH, expand=True)
        
        # 选项卡1：文件列表
        file_tab = ttk.Frame(notebook)
        notebook.add(file_tab, text="📋 文件列表")
        
        # 文件列表表格
        columns = ('序号', '文件名', '格式', '大小', '状态')
        self.file_tree = ttk.Treeview(file_tab, columns=columns, show='headings', height=15)
        
        # 设置列
        for col in columns:
            self.file_tree.heading(col, text=col)
            self.file_tree.column(col, width=100)
        
        # 调整列宽
        self.file_tree.column('文件名', width=250)
        self.file_tree.column('状态', width=100)
        
        # 添加滚动条
        tree_scroll = ttk.Scrollbar(file_tab, orient=tk.VERTICAL, command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=tree_scroll.set)
        
        self.file_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 选项卡2：转换日志
        log_tab = ttk.Frame(notebook)
        notebook.add(log_tab, text="📝 转换日志")
        
        # 日志文本框
        self.log_text = scrolledtext.ScrolledText(log_tab, 
                                                 height=20,
                                                 wrap=tk.WORD,
                                                 font=('Consolas', 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        # 选项卡3：统计信息
        stats_tab = ttk.Frame(notebook)
        notebook.add(stats_tab, text="📊 统计信息")
        
        self.stats_text = scrolledtext.ScrolledText(stats_tab,
                                                   height=20,
                                                   wrap=tk.WORD,
                                                   font=('Arial', 10))
        self.stats_text.pack(fill=tk.BOTH, expand=True)
        
        # 底部进度条和统计
        bottom_frame = ttk.Frame(main_container)
        bottom_frame.pack(fill=tk.X, pady=(10, 0))
        
        # 总体进度条
        self.overall_progress_var = tk.DoubleVar()
        self.overall_progress = ttk.Progressbar(bottom_frame,
                                               variable=self.overall_progress_var,
                                               maximum=100,
                                               length=600)
        self.overall_progress.pack(fill=tk.X, pady=(0, 5))
        
        # 当前文件进度
        current_frame = ttk.Frame(bottom_frame)
        current_frame.pack(fill=tk.X)
        
        ttk.Label(current_frame, text="当前文件:").pack(side=tk.LEFT)
        self.current_file_var = tk.StringVar(value="无")
        ttk.Label(current_frame, textvariable=self.current_file_var, foreground="blue").pack(side=tk.LEFT, padx=(5, 20))
        
        self.current_progress_var = tk.DoubleVar()
        self.current_progress = ttk.Progressbar(current_frame,
                                               variable=self.current_progress_var,
                                               maximum=100,
                                               length=300)
        self.current_progress.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # 统计信息显示
        stats_frame = ttk.Frame(bottom_frame)
        stats_frame.pack(fill=tk.X, pady=(5, 0))
        
        self.stats_vars = {
            'total': tk.StringVar(value="总计: 0"),
            'success': tk.StringVar(value="成功: 0"),
            'failed': tk.StringVar(value="失败: 0"),
            'remaining': tk.StringVar(value="剩余: 0")
        }
        
        for key, var in self.stats_vars.items():
            label = ttk.Label(stats_frame, textvariable=var, font=('Arial', 9))
            label.pack(side=tk.LEFT, padx=10)
    
    def setup_bindings(self):
        """设置事件绑定"""
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def import_folder(self):
        """导入整个文件夹"""
        folder_path = filedialog.askdirectory(title="选择音频文件夹")
        if not folder_path:
            return
        
        # 支持的音频扩展名
        audio_extensions = {'.flac', '.mp3', '.wav', '.ogg', '.aac', '.m4a', '.wma', '.aiff'}
        
        files = []
        for ext in audio_extensions:
            files.extend(Path(folder_path).glob(f"*{ext}"))
            files.extend(Path(folder_path).glob(f"*{ext.upper()}"))
        
        if not files:
            self.show_info("导入结果", f"在文件夹中未找到支持的音频文件")
            return
        
        self.add_files_to_list(files)
        self.show_info("导入成功", f"成功导入 {len(files)} 个音频文件")
    
    def select_multiple_files(self):
        """选择多个文件"""
        filetypes = [
            ("音频文件", "*.flac *.mp3 *.wav *.ogg *.aac *.m4a *.wma *.aiff"),
            ("所有文件", "*.*")
        ]
        
        files = filedialog.askopenfilenames(
            title="选择音频文件",
            filetypes=filetypes
        )
        
        if files:
            self.add_files_to_list([Path(f) for f in files])
    
    def add_files_to_list(self, files):
        """添加文件到列表"""
        for file_path in files:
            if file_path in [item['path'] for item in self.conversion_queue]:
                continue
            
            try:
                size = os.path.getsize(file_path) / (1024 * 1024)  # MB
                item = {
                    'path': file_path,
                    'name': file_path.name,
                    'ext': file_path.suffix.upper(),
                    'size': f"{size:.2f} MB",
                    'status': '等待',
                    'tree_id': None
                }
                self.conversion_queue.append(item)
            except:
                continue
        
        self.update_file_list()
        self.update_file_count()
    
    def update_file_list(self):
        """更新文件列表显示"""
        # 清空现有项
        for item in self.file_tree.get_children():
            self.file_tree.delete(item)
        
        # 添加新项
        for i, item in enumerate(self.conversion_queue, 1):
            tree_id = self.file_tree.insert('', tk.END, values=(
                i,
                item['name'],
                item['ext'],
                item['size'],
                item['status']
            ))
            item['tree_id'] = tree_id
        
        # 更新按钮状态
        self.update_control_buttons()
    
    def update_file_count(self):
        """更新文件计数和按钮文本"""
        total = len(self.conversion_queue)
        waiting = sum(1 for item in self.conversion_queue if item['status'] == '等待')
        
        if total == 0:
            self.file_count_var.set("等待添加文件...")
            self.convert_btn.config(state='disabled')
            # 无文件时显示"开始转换"
            self.convert_btn.config(text="🚀 开始转换")
        else:
            self.file_count_var.set(f"已添加 {total} 个文件 ({waiting} 个等待中)")
            
            # 根据文件数量动态更新按钮文本
            if total == 1:
                self.convert_btn.config(text="🚀 开始转换")
            else:
                self.convert_btn.config(text=f"🚀 开始批量转换 ({waiting}个文件)")
            
            if waiting > 0:
                self.convert_btn.config(state='normal')
            else:
                self.convert_btn.config(state='disabled')
    
    def update_control_buttons(self):
        """更新控制按钮状态"""
        waiting = sum(1 for item in self.conversion_queue if item['status'] == '等待')
        converting = self.is_converting
        
        if converting:
            self.convert_btn.config(state='disabled')
            if len(self.conversion_queue) == 1:
                self.convert_btn.config(text="转换中...")
            else:
                self.convert_btn.config(text="批量转换中...")
            self.pause_btn.config(state='normal')
            self.stop_btn.config(state='normal')
        elif waiting > 0:
            # 根据文件数量设置按钮文本
            total = len(self.conversion_queue)
            if total == 1:
                self.convert_btn.config(text="🚀 开始转换")
            else:
                self.convert_btn.config(text=f"🚀 开始批量转换 ({waiting}个文件)")
            
            self.convert_btn.config(state='normal')
            self.pause_btn.config(state='disabled')
            self.stop_btn.config(state='disabled')
        else:
            # 无等待文件时根据总数显示按钮文本
            total = len(self.conversion_queue)
            if total == 0:
                self.convert_btn.config(text="🚀 开始转换")
            elif total == 1:
                self.convert_btn.config(text="🚀 开始转换")
            else:
                self.convert_btn.config(text="🚀 开始批量转换")
            
            self.convert_btn.config(state='disabled')
            self.pause_btn.config(state='disabled')
            self.stop_btn.config(state='disabled')
    
    def clear_file_list(self):
        """清空文件列表"""
        if self.is_converting:
            self.show_warning("操作被拒绝", "转换过程中无法清空列表")
            return
        
        self.conversion_queue.clear()
        self.update_file_list()
        self.update_file_count()
        self.log("已清空文件列表")
    
    def select_output_dir(self):
        """选择输出目录"""
        directory = filedialog.askdirectory(title="选择输出目录")
        if directory:
            self.output_dir_var.set(directory)
            self.log(f"输出目录设置为: {directory}")
    
    def start_batch_conversion(self):
        """开始批量转换（也处理单个文件转换）"""
        if self.is_converting:
            return
        
        # 检查输出目录
        output_dir = Path(self.output_dir_var.get())
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except:
            self.show_error("错误", "无法创建输出目录")
            return
        
        # 重置统计
        self.reset_stats()
        
        # 创建线程池（单个文件也使用线程池，但可以设置最大工作线程为1）
        max_workers = 1 if len(self.conversion_queue) == 1 else 2
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.is_converting = True
        self.pause_conversion = False
        
        # 根据文件数量更新状态信息
        total = len([item for item in self.conversion_queue if item['status'] == '等待'])
        if total == 1:
            self.log("开始单个文件转换")
            self.status_label.config(text="转换中...")
        else:
            self.log(f"开始批量转换 {total} 个文件")
            self.status_label.config(text=f"批量转换中... ({total}个文件)")
        
        self.status_indicator.config(foreground="orange")
        
        # 启动转换线程
        conversion_thread = threading.Thread(target=self.run_batch_conversion)
        conversion_thread.daemon = True
        conversion_thread.start()
        
        self.update_control_buttons()
    
    def run_batch_conversion(self):
        """运行批量转换"""
        # 获取等待转换的文件
        files_to_convert = [item for item in self.conversion_queue if item['status'] == '等待']
        
        if not files_to_convert:
            self.log("没有需要转换的文件")
            self.finish_conversion()
            return
        
        # 更新总数
        self.conversion_stats['total'] = len(files_to_convert)
        self.update_stats_display()
        
        # 提交转换任务
        futures = []
        for item in files_to_convert:
            future = self.executor.submit(self.convert_single_file, item)
            futures.append(future)
        
        # 等待所有任务完成
        for future in as_completed(futures):
            if self.pause_conversion:
                while self.pause_conversion:
                    time.sleep(0.5)
            
            result = future.result()
            if result:
                self.conversion_stats['success'] += 1
            else:
                self.conversion_stats['failed'] += 1
            
            self.update_stats_display()
        
        # 所有任务完成
        self.finish_conversion()
    
    def convert_single_file(self, item):
        """转换单个文件"""
        try:
            # 更新状态
            item['status'] = '转换中'
            self.update_item_status(item)
            
            # 构建输出路径
            output_dir = Path(self.output_dir_var.get())
            target_format = self.format_var.get()
            output_filename = Path(item['path']).stem + self.supported_formats[target_format]
            output_file = output_dir / output_filename
            
            # 构建FFmpeg命令
            cmd = self.build_ffmpeg_command(str(item['path']), str(output_file))
            
            # 运行转换
            self.current_file_var.set(item['name'])
            self.current_progress_var.set(0)
            
            process = subprocess.run(cmd, 
                                   capture_output=True, 
                                   text=True, 
                                   timeout=300)  # 5分钟超时
            
            if process.returncode == 0:
                item['status'] = '✓ 成功'
                self.log(f"成功: {item['name']} → {target_format}")
                
                # 模拟进度完成
                for i in range(10):
                    if self.pause_conversion:
                        return False
                    self.current_progress_var.set((i + 1) * 10)
                    time.sleep(0.1)
                
                return True
            else:
                item['status'] = '✗ 失败'
                self.log(f"失败: {item['name']} - {process.stderr[:100]}")
                return False
                
        except subprocess.TimeoutExpired:
            item['status'] = '⏱️ 超时'
            self.log(f"超时: {item['name']}")
            return False
        except Exception as e:
            item['status'] = '❌ 错误'
            self.log(f"错误: {item['name']} - {str(e)}")
            return False
        finally:
            self.update_item_status(item)
            self.current_progress_var.set(0)
            self.current_file_var.set("无")
    
    def build_ffmpeg_command(self, input_file, output_file):
        """构建FFmpeg命令"""
        cmd = ['ffmpeg', '-i', input_file, '-y', '-hide_banner']
        
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
                quality_map = {'64k': '2', '128k': '4', '192k': '6', '256k': '8', '320k': '10'}
                cmd.extend(['-q:a', quality_map.get(quality, '6')])
        
        cmd.append(output_file)
        return cmd
    
    def update_item_status(self, item):
        """更新项目状态"""
        if item.get('tree_id'):
            self.file_tree.item(item['tree_id'], values=(
                self.file_tree.item(item['tree_id'])['values'][0],  # 序号
                item['name'],
                item['ext'],
                item['size'],
                item['status']
            ))
        
        # 更新整体进度
        total = len(self.conversion_queue)
        completed = sum(1 for item in self.conversion_queue 
                       if item['status'] in ['✓ 成功', '✗ 失败', '⏱️ 超时', '❌ 错误'])
        
        if total > 0:
            progress = (completed / total) * 100
            self.overall_progress_var.set(progress)
    
    def toggle_pause(self):
        """暂停/继续转换"""
        if hasattr(self, 'pause_conversion'):
            self.pause_conversion = not self.pause_conversion
            if self.pause_conversion:
                self.pause_btn.config(text="▶️ 继续")
                self.status_label.config(text="已暂停")
                self.status_indicator.config(foreground="yellow")
                self.log("转换已暂停")
            else:
                self.pause_btn.config(text="⏸️ 暂停")
                self.status_label.config(text="转换中...")
                self.status_indicator.config(foreground="orange")
                self.log("转换已继续")
    
    def stop_conversion(self):
        """停止转换"""
        if self.executor:
            self.executor.shutdown(wait=False, cancel_futures=True)
        self.is_converting = False
        self.finish_conversion()
        self.log("转换已停止")
    
    def finish_conversion(self):
        """完成转换"""
        self.is_converting = False
        
        # 更新状态
        self.status_label.config(text="就绪")
        self.status_indicator.config(foreground="green")
        
        # 更新按钮状态
        self.update_control_buttons()
        
        # 显示完成统计
        success = self.conversion_stats['success']
        total = self.conversion_stats['total']
        
        if total > 0:
            # 根据文件数量显示不同的完成信息
            if total == 1:
                if success == 1:
                    self.log("单个文件转换完成！")
                    self.show_info("转换完成", "文件转换成功！")
                else:
                    self.log("单个文件转换失败")
                    self.show_info("转换完成", "文件转换失败")
            else:
                self.log(f"批量转换完成！成功: {success}/{total} 个文件")
                
                if success == total:
                    self.show_info("转换完成", f"所有 {total} 个文件转换成功！")
                else:
                    self.show_info("转换完成", 
                                 f"转换完成！\n成功: {success} 个文件\n失败: {total - success} 个文件")
    
    def reset_stats(self):
        """重置统计信息"""
        self.conversion_stats = {"success": 0, "failed": 0, "total": 0}
        self.update_stats_display()
    
    def update_stats_display(self):
        """更新统计显示"""
        total = self.conversion_stats['total']
        success = self.conversion_stats['success']
        failed = self.conversion_stats['failed']
        remaining = total - success - failed
        
        self.stats_vars['total'].set(f"总计: {total}")
        self.stats_vars['success'].set(f"成功: {success}")
        self.stats_vars['failed'].set(f"失败: {failed}")
        self.stats_vars['remaining'].set(f"剩余: {remaining}")
        
        # 更新统计文本框
        stats_text = f"""
╔══════════════════════════════════╗
║        转换统计信息              ║
╠══════════════════════════════════╣
║ 总计文件: {total:>20}  ║
║ 成功转换: {success:>20}  ║
║ 转换失败: {failed:>20}  ║
║ 等待转换: {remaining:>20}  ║
║                                  ║
║ 成功率: {(success/total*100 if total>0 else 0):>22.1f}%  ║
╚══════════════════════════════════╝
        """
        
        self.stats_text.delete(1.0, tk.END)
        self.stats_text.insert(1.0, stats_text)
    
    def check_progress_updates(self):
        """定期检查进度更新"""
        try:
            while not self.progress_queue.empty():
                update = self.progress_queue.get_nowait()
                # 处理进度更新
                pass
        except:
            pass
        
        self.root.after(100, self.check_progress_updates)
    
    def log(self, message):
        """添加日志信息"""
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        log_entry = f"[{timestamp}] {message}\n"
        
        self.log_text.insert(tk.END, log_entry)
        self.log_text.see(tk.END)
    
    def show_error(self, title, message):
        """显示错误信息"""
        messagebox.showerror(title, message)
        self.log(f"[错误] {title}: {message}")
    
    def show_warning(self, title, message):
        """显示警告信息"""
        messagebox.showwarning(title, message)
        self.log(f"[警告] {title}: {message}")
    
    def show_info(self, title, message):
        """显示信息"""
        messagebox.showinfo(title, message)
        self.log(f"[信息] {title}: {message}")
    
    def on_closing(self):
        """关闭窗口时的处理"""
        if self.executor:
            self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()

def main():
    root = tk.Tk()
    app = BatchAudioConverterApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()