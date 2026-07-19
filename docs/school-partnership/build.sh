#!/bin/bash
# 从 proposal.html 生成 PDF 到下载文件夹
cd "$(dirname "$0")"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --no-pdf-header-footer --virtual-time-budget=15000 \
  --print-to-pdf="$HOME/Downloads/Hastrid美容学院合作方案.pdf" \
  "file://$(pwd)/proposal.html"
echo "已生成 $HOME/Downloads/Hastrid美容学院合作方案.pdf"
