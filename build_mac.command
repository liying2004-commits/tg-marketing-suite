#!/bin/bash
# TG Marketing Suite - Mac Build Script
# 双击此文件即可构建 macOS 应用
# Double-click to build macOS app

cd "$(dirname "$0")"

echo "====================================="
echo " TG Marketing Suite - Mac Builder"
echo "====================================="
echo ""

# Check Python3
if ! command -v python3 &> /dev/null; then
    echo "[错误] 未检测到 Python3, 正在安装..."
    # Install Homebrew if needed
    if ! command -v brew &> /dev/null; then
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
    brew install python@3.11
fi

echo "[1/3] 安装依赖..."
python3 -m pip install --user --quiet pyinstaller telethon cryptg 2>&1 | tail -1

echo "[2/3] 构建 macOS 应用..."
python3 -m PyInstaller \
    --onedir \
    --name TG_Marketing_Suite \
    --add-data "modules:modules" \
    --hidden-import telethon \
    --hidden-import telethon.network \
    --hidden-import telethon.crypto \
    --hidden-import telethon.extensions \
    --hidden-import cryptg \
    --hidden-import sqlite3 \
    --noconsole \
    app_gui.py 2>&1 | tail -5

if [ -d "dist/TG_Marketing_Suite" ]; then
    echo ""
    echo "[3/3] 构建完成!"
    echo ""
    echo "应用位置: $(pwd)/dist/TG_Marketing_Suite/"
    echo "启动文件: dist/TG_Marketing_Suite/TG_Marketing_Suite"
    echo ""
    echo "将整个 TG_Marketing_Suite 文件夹拷贝给客户即可"
    echo "====================================="
else
    echo "[错误] 构建失败, 请检查上方日志"
fi

read -p "按回车键退出..."
