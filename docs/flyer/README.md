# 哈瓜小约 商家传单（源文件）

线下跑店发的中文单页传单（A4）。之前那版的源文件丢了只剩 PDF，这份是重建版，以后改文案直接改 `flyer.html`。

## 文件
- `flyer.html` — 传单正文（A4 单页，黑白 + 宋体标题，试用块淡金底）
- `assets/mp_qr.png` — 微信小程序码（从旧版 PDF 里抠出来放大的，900×900）
- `assets/web_qr.png` — 网页版二维码，指向 `https://hastridbooking.com`

重新生成网页二维码：
```bash
python3 -c "
import qrcode
qr=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=2)
qr.add_data('https://hastridbooking.com'); qr.make(fit=True)
qr.make_image().convert('RGB').resize((900,900)).save('assets/web_qr.png')"
```

⚠️ **小程序码没法用程序验证**（微信专有格式，标准 QR 解码器解不出），印之前一定要用微信实扫一次。网页码已用 cv2 解码验证过。

## 重新生成 PDF
```bash
bash build.sh
```
输出到 `~/Downloads/哈瓜小约_商家传单.pdf`。**注意会覆盖同名文件**，改之前先备份。

## 当前版本内容（2026-07-18）
5 个条目：接不了电话也不丢单 / 微信+网页两边都能用 / 自动提醒少放鸽子 / 客人信息不用记本子 / 先免费用 30 天。

## 刻意没写的东西
**没写月费金额**（Harry 定的，2026-07-18）。传单只讲 30 天免费试用，价格留到面对面谈。线上实际方案是 $29.99/月含 300 条短信，超额 $0.02/段。

**没做竞品价格对比。** 曾经想写「Booksy / Square $50-90 起」，但实际 Square Appointments 单人店有免费版、Booksy 约 $29.99 起、Vagaro $23.99 起，写出来跑店会被当场拆穿。
真正的差异化是**全中文界面 + 微信小程序 + 本地人上门帮设置**，传单里靠 02 和 05 两条体现。

## 英文版
`~/Downloads/Hastrid_Business_Flyer_EN.pdf`（2026-07-13）源文件也丢了，**还没更新价格**，要用得重建一份。
