#!/bin/bash
# 从 flyer.html 生成中文传单 PDF 到下载文件夹
cd "$(dirname "$0")"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --no-pdf-header-footer --virtual-time-budget=15000 \
  --print-to-pdf="$HOME/Downloads/哈瓜小约_商家传单.pdf" \
  "file://$(pwd)/flyer.html"
echo "已生成 $HOME/Downloads/哈瓜小约_商家传单.pdf"
