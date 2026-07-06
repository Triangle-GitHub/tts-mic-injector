# -*- coding: utf-8 -*-
"""
RemoteReceiver — WebSocket client that listens for messages from the relay server.
Runs in a background thread, emits PyQt signals on the main thread.
"""

import json
import threading
import time
import logging

from PyQt5.QtCore import QObject, pyqtSignal

try:
    import websocket
except ImportError:
    websocket = None

logger = logging.getLogger("TTSMicInjector")


class RemoteReceiver(QObject):
    message_received = pyqtSignal(str)
    connection_changed = pyqtSignal(bool)

    def __init__(self, server_url: str, token: str, parent=None):
        super().__init__(parent)
        self._server_url = server_url
        self._token = token
        self._ws = None
        self._running = False
        self._thread = None
        self._connected = False

    def start(self):
        if websocket is None:
            logger.error("websocket-client not installed. Run: pip install websocket-client")
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def is_connected(self) -> bool:
        return self._connected

    def _loop(self):
        while self._running:
            try:
                ws_url = f"{self._server_url}?token={self._token}"
                self._ws = websocket.create_connection(ws_url, timeout=10)
                self._connected = True
                self.connection_changed.emit(True)
                logger.info("远程连接已建立: %s", self._server_url)

                while self._running:
                    try:
                        self._ws.settimeout(1.0)
                        raw = self._ws.recv()
                        if raw:
                            try:
                                data = json.loads(raw)
                                text = data.get("text", "")
                                if text:
                                    self.message_received.emit(text)
                            except json.JSONDecodeError:
                                logger.debug("收到非 JSON 消息，忽略")
                    except websocket.WebSocketTimeoutException:
                        continue
                    except Exception as e:
                        if self._running:
                            logger.debug("接收异常: %s", e)
                        break

            except Exception as e:
                if self._running:
                    logger.warning("远程连接失败: %s，1秒后重试...", e)
            finally:
                if self._ws:
                    try:
                        self._ws.close()
                    except Exception:
                        pass
                    self._ws = None
                if self._connected:
                    self._connected = False
                    self.connection_changed.emit(False)
                    logger.info("远程连接已断开")

            if self._running:
                time.sleep(1)
