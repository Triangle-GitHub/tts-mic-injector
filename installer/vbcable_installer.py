# -*- coding: utf-8 -*-
"""
VB-Cable 一键安装器 — 下载、安装、检测流水线。

参考 MicYou (GPL-3.0) 的 vbcable.rs 实现：
  - 运行时从 VB-Audio 官网下载 ZIP
  - 静默安装: VBCABLE_Setup_x64.exe -i -h
  - 轮询等待设备就绪（最多 30 秒）
  - 安装后自动清理临时文件
"""

import os
import zipfile
import shutil
import tempfile
import logging
import time
import subprocess
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from config import VB_CABLE_KEYWORDS

try:
    import urllib.request
except ImportError:
    pass

logger = logging.getLogger("TTSMicInjector")

# ── 常量 ──
INSTALLER_URL = (
    "https://download.vb-audio.com/Download_CABLE/"
    "VBCABLE_Driver_Pack45.zip"
)
INSTALLER_EXE = "VBCABLE_Setup_x64.exe"
INSTALLER_DIR = "VBCABLE_Driver_Pack45"
WAIT_INTERVAL = 1
WAIT_MAX = 30


# ── 检测函数 ──

def _detect_device() -> bool:
    """检测 VB-Cable 输出设备是否存在（复用 pyaudio 枚举）。"""
    try:
        import pyaudio
    except ImportError:
        return False

    try:
        p = pyaudio.PyAudio()
    except Exception:
        return False

    try:
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info["maxOutputChannels"] == 0:
                continue
            name = info["name"]
            for kw in VB_CABLE_KEYWORDS:
                if kw.lower() in name.lower():
                    return True
        return False
    except Exception:
        return False
    finally:
        p.terminate()


def is_vbcable_installed() -> bool:
    """公开检测接口：True 表示 VB-Cable 已安装。"""
    return _detect_device()


# ── 安装器线程 ──

class VBCableInstaller(QThread):
    """VB-Cable 安装线程，不阻塞 UI。"""

    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)   # (success, message)
    error_occurred = pyqtSignal(str, str)  # (error_type, message)

    # 保护标志（进程级）
    _installing = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self._temp_dir = None

    @classmethod
    def is_busy(cls) -> bool:
        return cls._installing

    def run(self):
        if VBCableInstaller._installing:
            self.finished.emit(False, "安装已在进行中")
            return

        VBCableInstaller._installing = True
        try:
            self._install()
        finally:
            VBCableInstaller._installing = False

    # ── 内部安装流水线 ──

    def _install(self):
        # 1. 已安装则直接返回
        if _detect_device():
            self.progress.emit("VB-Cable 已安装")
            self.finished.emit(True, "已安装")
            return

        # 2. 创建临时目录
        self._temp_dir = Path(tempfile.gettempdir()) / "ttsmic_vbcable"
        self._temp_dir.mkdir(parents=True, exist_ok=True)

        # 3. 下载
        zip_path = self._temp_dir / "vbcable_pack.zip"
        if not self._download(zip_path):
            self._cleanup()
            self.finished.emit(False, "下载失败")
            self.error_occurred.emit("download_failed", "下载 VB-Cable 失败，请检查网络连接")
            return

        # 4. 解压
        extract_dir = self._temp_dir / INSTALLER_DIR
        installer_exe = extract_dir / INSTALLER_EXE

        if not self._extract(zip_path, extract_dir):
            self._cleanup()
            self.finished.emit(False, "解压失败")
            return

        if not installer_exe.exists():
            self._cleanup()
            self.finished.emit(False, f"未找到 {INSTALLER_EXE}")
            return

        # 5. 安装（需要管理员权限）
        self.progress.emit("正在安装（需要管理员权限）...")
        if not self._run_installer(installer_exe):
            self._cleanup()
            self.finished.emit(False, "安装被取消或失败")
            return

        # 6. 等待设备就绪
        self.progress.emit("等待设备初始化...")
        if not self._wait_device():
            self._cleanup()
            self.finished.emit(False, "安装超时：设备未在 30 秒内就绪")
            self.error_occurred.emit("timeout", "安装完成但设备未在 30 秒内就绪，请尝试重启系统")
            return

        # 7. 配置确认
        self.progress.emit("配置设备...")
        self._configure()

        # 8. 清理
        self._cleanup()

        self.finished.emit(True, "安装完成")

    # ── 步骤实现 ──

    def _download(self, dest: Path) -> bool:
        self.progress.emit("正在下载 VB-Cable 安装包...")
        try:
            urllib.request.urlretrieve(INSTALLER_URL, str(dest))
            return dest.exists() and dest.stat().st_size > 0
        except Exception as e:
            logger.error(f"下载失败: {e}")
            return False

    def _extract(self, zip_path: Path, dest_dir: Path) -> bool:
        self.progress.emit("正在解压...")
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(dest_dir)
            # 解压后删除 ZIP
            zip_path.unlink(missing_ok=True)
            return True
        except Exception as e:
            logger.error(f"解压失败: {e}")
            return False

    def _run_installer(self, exe: Path) -> bool:
        """启动安装器（管理员提权），不等待完成。返回 True 表示已启动。"""
        exe_str = str(exe).replace("'", "''")
        cmd = (
            f"Start-Process -FilePath '{exe_str}' "
            f"-ArgumentList '-i','-h' -Verb RunAs"
        )
        try:
            result = subprocess.run(
                ["powershell", "-Command", cmd],
                capture_output=True,
                text=True,
                timeout=15,
            )
            # Start-Process 本身成功了就算启动成功
            # 实际安装结果由 _wait_device 轮询判断
            return True
        except subprocess.TimeoutExpired:
            logger.warning("启动安装器超时，但可能仍在运行")
            return True
        except Exception as e:
            logger.error(f"启动安装器失败: {e}")
            return False

    def _wait_device(self) -> bool:
        waited = 0
        while waited < WAIT_MAX:
            time.sleep(WAIT_INTERVAL)
            waited += WAIT_INTERVAL
            self.progress.emit(f"等待设备就绪... ({waited}s/{WAIT_MAX}s)")
            if _detect_device():
                return True
        return False

    def _configure(self):
        """PnP 设备确认（可选）。"""
        try:
            subprocess.run(
                [
                    "powershell", "-Command",
                    "Get-PnpDevice -FriendlyName '*CABLE Output*' | "
                    "Where-Object { $_.Status -eq 'OK' } | "
                    "ForEach-Object { Write-Host \"Found: $($_.FriendlyName)\" }"
                ],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass

    def _cleanup(self):
        if self._temp_dir and self._temp_dir.exists():
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
            except Exception:
                pass
