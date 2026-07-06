# -*- coding: utf-8 -*-
"""
SetupPopup — floating popup for one-click engine dependency installation.
"""

import os
import sys
import zipfile
import shutil
import tempfile
import subprocess
import webbrowser
import logging
from pathlib import Path

from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout

from qfluentwidgets import (
    PushButton, PrimaryPushButton, BodyLabel, LineEdit,
    isDarkTheme,
)

logger = logging.getLogger("TTSMicInjector")

_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class SetupPopup(QFrame):
    """Floating popup for installing missing engine dependencies."""

    def __init__(self, parent, title, message, pip_packages=None,
                 download_items=None, need_api_key=False, open_url=None,
                 on_save_key=None):
        super().__init__(parent, Qt.Popup)
        self._parent = parent
        self._title = title
        self._message = message
        self._pip_packages = pip_packages or []
        self._download_items = download_items or []
        self._need_api_key = need_api_key
        self._open_url = open_url
        self._on_save_key = on_save_key
        self._installing = False

        self.setObjectName("setupPopup")
        self.setFrameShape(QFrame.StyledPanel)
        self._apply_theme()
        self._build_ui()

    def _apply_theme(self):
        dark = isDarkTheme()
        bg = "#2d2d2d" if dark else "#ffffff"
        border = "rgba(255,255,255,0.08)" if dark else "rgba(0,0,0,0.12)"
        self.setStyleSheet(
            f"QFrame#setupPopup {{ background-color: {bg}; border: 1px solid {border}; border-radius: 8px; }}"
        )

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        layout.addWidget(BodyLabel(self._title))
        layout.addWidget(BodyLabel(self._message))

        self._key_edit = None
        if self._need_api_key:
            layout.addWidget(BodyLabel("API Key (sk-...)"))
            self._key_edit = LineEdit()
            self._key_edit.setFixedWidth(280)
            layout.addWidget(self._key_edit)

        self._error_label = BodyLabel("")
        self._error_label.setStyleSheet("color: #e74856;")
        self._error_label.hide()
        layout.addWidget(self._error_label)

        btn_row = QHBoxLayout()
        cancel_btn = PushButton("取消")

        has_installable = bool(self._pip_packages or self._download_items)
        if self._open_url and not has_installable:
            self._install_btn = PushButton("打开下载页")
        elif self._need_api_key and has_installable:
            self._install_btn = PrimaryPushButton("保存到配置并安装")
        elif self._need_api_key:
            self._install_btn = PrimaryPushButton("保存到配置")
        else:
            self._install_btn = PrimaryPushButton("安装")

        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._install_btn)
        layout.addLayout(btn_row)

        cancel_btn.clicked.connect(self.close)
        self._install_btn.clicked.connect(self._on_install)

    def show_near(self, widget):
        """Position popup below the given widget and show."""
        pos = widget.mapToGlobal(QPoint(0, widget.height() + 4))
        self.move(pos)
        self.show()

    def _show_error(self, text):
        self._error_label.setText(text)
        self._error_label.show()

    def _hide_error(self):
        self._error_label.hide()

    def _on_install(self):
        if self._installing:
            return

        if self._open_url and not self._pip_packages and not self._download_items:
            webbrowser.open(self._open_url)
            self.close()
            return

        has_install = bool(self._pip_packages or self._download_items)

        if self._need_api_key and self._key_edit:
            api_key = self._key_edit.text().strip()
            if not api_key:
                self._show_error("请输入 API Key")
                return
            if not api_key.startswith("sk-"):
                self._show_error("API Key 无效，应以 sk- 开头")
                return
            self._hide_error()
            if self._on_save_key:
                self._on_save_key(api_key)

            if not has_install:
                self.close()
                return

        self._installing = True
        self._install_btn.setEnabled(False)
        self._install_btn.setText("安装中...")
        self._hide_error()

        all_ok = True

        for pkg in self._pip_packages:
            logger.info(f"pip install {pkg} ...")
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", pkg],
                    capture_output=True, text=True, timeout=120,
                    creationflags=_CREATION_FLAGS,
                )
                if result.returncode != 0:
                    logger.error(f"pip install {pkg} 失败: {result.stderr}")
                    all_ok = False
                else:
                    logger.info(f"pip install {pkg} 成功")
            except Exception as e:
                logger.error(f"pip install {pkg} 异常: {e}")
                all_ok = False

        root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools")
        os.makedirs(root, exist_ok=True)
        for url, filename, inner_path in self._download_items:
            logger.info(f"下载 {filename} ...")
            try:
                ok = self._download_and_extract(url, filename, inner_path, root)
                if not ok:
                    all_ok = False
            except Exception as e:
                logger.error(f"下载 {filename} 异常: {e}")
                all_ok = False

        self.close()

        if all_ok:
            self._show_restart_dialog()
        else:
            logger.error("部分安装失败，请查看日志")

    def _download_and_extract(self, url, filename, inner_path, dest_dir):
        import urllib.request
        tmp_dir = Path(tempfile.gettempdir()) / "ttsmic_setup"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        download_path = tmp_dir / filename
        try:
            urllib.request.urlretrieve(url, str(download_path))
        except Exception as e:
            logger.error(f"下载失败: {e}")
            return False

        if filename.endswith(".zip"):
            try:
                with zipfile.ZipFile(download_path, "r") as zf:
                    if inner_path:
                        try:
                            zf.extract(inner_path, tmp_dir)
                            src = tmp_dir / inner_path
                        except KeyError:
                            candidates = [
                                n for n in zf.namelist()
                                if n.endswith(os.path.basename(inner_path))
                            ]
                            if candidates:
                                zf.extract(candidates[0], tmp_dir)
                                src = tmp_dir / candidates[0]
                            else:
                                logger.error(f"压缩包中未找到: {inner_path}")
                                return False
                    else:
                        zf.extractall(tmp_dir)
                        exe_files = [
                            os.path.join(tmp_dir, n)
                            for n in zf.namelist()
                            if n.endswith(".exe") and not n.startswith("__MACOSX")
                        ]
                        if exe_files:
                            src = Path(exe_files[0])
                        else:
                            logger.error("压缩包中未找到 exe")
                            return False

                    dest = os.path.join(dest_dir, os.path.basename(inner_path) if inner_path else os.path.basename(str(src)))
                    shutil.copy2(str(src), dest)
                    logger.info(f"已安装: {dest}")
            except Exception as e:
                logger.error(f"解压失败: {e}")
                return False
            finally:
                download_path.unlink(missing_ok=True)
        else:
            dest = os.path.join(dest_dir, filename)
            shutil.copy2(str(download_path), dest)
            download_path.unlink(missing_ok=True)
            logger.info(f"已安装: {dest}")

        return True

    def _show_restart_dialog(self):
        from PyQt5.QtWidgets import QMessageBox, QApplication
        msg = QMessageBox(self._parent)
        msg.setWindowTitle("安装完成")
        msg.setText("依赖安装完成，建议重启应用以生效。\n是否立即重启？")
        msg.setIcon(QMessageBox.Question)
        restart_btn = msg.addButton("重启", QMessageBox.AcceptRole)
        later_btn = msg.addButton("稍后", QMessageBox.RejectRole)
        msg.exec_()

        if msg.clickedButton() is restart_btn:
            self._restart_app()

    @staticmethod
    def _restart_app():
        if getattr(sys, 'frozen', False):
            subprocess.Popen(
                [sys.executable],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
        else:
            app_dir = Path(__file__).resolve().parent.parent
            app_path = str(app_dir / "app.py")
            subprocess.Popen(
                [sys.executable, app_path],
                cwd=str(app_dir),
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
            )
        QApplication.instance().quit()
