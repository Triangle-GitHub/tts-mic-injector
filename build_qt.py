# -*- coding: utf-8 -*-
"""PyInstaller 打包脚本。"""

import sys
from PyInstaller.__main__ import run


def main():
    system = sys.platform
    path_sep = ';' if system == 'win32' else ':'

    print(f"正在为 {system} 平台打包 TTS Mic Injector...")

    args = [
        'app.py',
        '--windowed',
        '--name=TTSMicInjector',
        f'--add-data=assets{path_sep}assets',
        '--noconfirm',
        '--clean',
    ]

    if system == 'win32':
        args.append('--hidden-import=win32com')
        args.append('--hidden-import=pythoncom')
    elif system == 'linux':
        args.append('--hidden-import=plyer.platforms.linux.notification')
    elif system == 'darwin':
        args.append('--hidden-import=plyer.platforms.macosx.notification')

    print(">>> 开始打包...")
    run(args)
    print("\n打包完成！")


if __name__ == '__main__':
    main()
