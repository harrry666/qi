# Hastrid Booking 商家使用手册（源文件）

面向商家的 PDF 使用手册源文件。推荐给新商家时发这份 PDF，讲清楚网页版 + 小程序版怎么 set up、有哪些功能。

## 文件
- `manual.html` — 手册正文（A4 打印排版，暖奶油+金品牌风，图片按 `assets/` 引用）
- `assets/` — 手册用到的真实截图
  - `01`–`15_*.png` — 网页版界面（用 demo 账号 `demo@hagua.com` 无头浏览器截的）
  - `mp_home.png` / `mp_calendar.png` / `mp_share.png` — 小程序界面（手机真机截图，已裁掉模拟器外壳）

## 重新生成 PDF
改完 `manual.html` 后，用 Chrome 无头模式生成 PDF：

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --no-pdf-header-footer --virtual-time-budget=15000 \
  --print-to-pdf="$HOME/Downloads/Hastrid商家使用手册.pdf" \
  "file://$(pwd)/manual.html"
```

或直接跑 `bash build.sh`。

## 重新截网页截图（如果界面更新了）
截图脚本思路：playwright 启动 chromium → 登录 demo 商家 → 逐页 `page.screenshot()` 存进 `assets/`。
demo 账号：`demo@hagua.com` / `demo1234`。小程序截图只能手机真机截，然后用 PIL 按行/列亮度自动裁掉深色模拟器外壳。

## 联系方式（手册 + 官网首页都放了）
- 微信：`ii9so_lovz`
- 电话：`(626) 559-6294`
