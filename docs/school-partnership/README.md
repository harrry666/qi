# 美容学院合作方案（源文件）

面向美容学院校长的一页纸合作方案 PDF。第一个对象是罗兰岗 **VR Professional Beauty Academy**。

## 内容重心（2026-07-18 改过一次）
主线是**毕业生创业数据看板**：学院给渠道，Hastrid 给数据。价格（前 6 个月免费 + 封顶 $39.99）放第 03 块，作用是回应「$29.99 太贵」这个异议，不是主角。

初版主线是 20% 推荐分成，Harry 决定不做分成，已整段替换。同时砍掉了「Harry 去上课」的内容，他的定位是平台方，不做讲师。

## 两个已知弱点，谈之前要有准备
- **看板签约当天是空的**，数据要等毕业生真开通，大约半年后才有第一批。文里第 02 块「数据什么时候有」已经把这条明写出来，不遮。校长大概率会追问「那我现在能看到什么」。
- **看板还没开发**。签了就是欠的工作量：要给 business 加学院来源字段 + 毕业生开通时的数据共享同意勾选 + 学院侧只读聚合页面。

## 文件
- `proposal.html` — 正文（A4 两页，沿用传单/手册的黑字+暖奶油+宋体品牌风）
  - 第 1 页：方案本身（数据看板 → 怎么运转 → 价格）
  - 第 2 页：看板界面示例图 + 隐私边界说明，方便当场给校长看界面
- `assets/school_dashboard_sample.png` — 看板示例截图，**里面是演示数据，PDF 里已明确标注**
- `build.sh` — Chrome 无头生成 PDF 到 `~/Downloads/Hastrid美容学院合作方案.pdf`

### 重新生成示例截图
本地起 `python app.py`，用 `scripts/add_school.py` 建学院并造几家毕业生店，然后 playwright 打开
`/school/<token>`，先 `document.querySelector('.link-box').remove()` 去掉带 localhost 的链接框，
再 `full_page` 截图（`device_scale_factor=2` 保证打印清晰）存回 `assets/`。

## 待确认
- 价格块用的是改版后的 $15 / $10每人封顶$39.99。代码已落（commit fa7d429），但 **Stripe 的阶梯 price 还没建**，真收钱前要先配 `STRIPE_SEAT_PRICE_ID`

## 第二批目标院校（爸拍的院校表，都在 City of Industry 一带）
Rosemead / Temple City / Victory Career / JD Academy。VR Professional 谈成后当参考案例去谈这批。
