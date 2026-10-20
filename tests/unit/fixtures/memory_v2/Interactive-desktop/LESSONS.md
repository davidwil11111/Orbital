<!--format LESSONS entries: numbered durable heuristics and playbooks. Add on error recovery or non-obvious workaround; keep real playbooks intact.-->
# 经验与排查记录

### 2026-10-16 — 先问清"要产的东西"再选型，别把后台能力当需求
**What happened:** 用户要"快速生产符合引擎 infra 的视频素材"，我一上来按引擎包结构（4K/2K/1080p/兜底四档 + 加密）把"多分辨率/分档"当成了选型要点，写了大量 ffmpeg 分档方案。用户纠正："多分辨率都不重要我们有后台处理的能力"——…
**Do instead:** 面对"生产符合 X 规格的素材"类需求，先逐项确认哪些是**生产端约束**、哪些是**下游/后台已解决**的，只对前者做选型。用户的 infra 文档描述的是"成品形态"，不等于"生产端要做的事"。
**Keywords:** research-scoping, requirement-clarification, video-asset, engine-infra

### 2026-10-03 — correction
**What happened:** 参考站未能打开时，按文字将“水彩”理解成柔边与半透明；用户随后上传截图，实际重点是清晰的俯视手绘岩石池底、苔藓与锦鲤色斑。
**Do instead:** 先查看真实参考图，区分纹理细节、轮廓对比与像素分辨率；不要用更多模糊和特效代替美术材质。首页文案暗色遮罩不等同于产品实际水色。
**Keywords:** koi-pond, visual-reference, painterly, clarity

### 2026-10-03 — error-recovery
**What happened:** 当前会话没有可用浏览器连接，原生 Chrome 控制未获准，Playwright 无头 Chrome 在沙箱内以 SIGABRT 退出；此前项目使用过 Playwright 不代表新会话仍可启动浏览器。
**Do instead:** 在授权限制内使用 Node VM 驱动原型脚本验证状态与事件，保留独立浏览器验收脚本；明确标记 Canvas 桩验证不能证明视觉、CSS 布局或流畅度，不生成虚假截图验收记录。
**Keywords:** koi-pond, Chrome, sandbox, Playwright, verification

- 像素场景的重绘缓存需要包含拖动预览中的 DOM 坐标；仅比较已保存 placements 会导致拖动时只有选框移动、物件到松手才跳转。静态预览服务修改 ES modules 后，为入口和依赖一起更新版本查询参数，避免浏览器继续使用旧模块。

### 2026-10-08 — 原型 E v2 分层与验收
- 互补裁切层的抗锯齿边缘按普通 source-over 叠加会损失透明度；头身再各自呼吸会出现细缝。先用 lighter 合回同一离屏层、同步变换，保留独立表情/眼/嘴层，可消除颈部裂缝而无需重新生图。
- 表情具有180ms淡入时，测试每次只画同一个时间戳会一直截到淡入起点，误以为差分无效。先画切换帧，再推进到淡入完成帧做视觉检查。
- 多个Chromium候选启动失败必须保留全部异常，不能让最后一个“默认版本未安装”覆盖前一个“系统端口权限拒绝”。v2使用独立输出目录，失败写 status=blocked/failed；旧截图和文档含v2字样均不代表当前验收通过。

### 2026-10-08 — 风景壁纸：不要「复刻具体摄影师作品」
**What happened:** 用户提出"用 AI 复刻著名摄影师的美照来保证受欢迎度"。查证：以某张仍在版权期的具体照片为蓝本做 AI 生成，很可能构成衍生作品侵权（VLP Law Group、PPOC 等均持此立场）；且地标本身也可能受限（埃菲尔铁塔夜间灯光秀版权归 S…
**Do instead:** 只把优质作品当"构图语言库"学习（取景/用光/调色），素材只用公有领域/CC0/官方公开图/自拍；用「地标锚点卡 + 机位/时段/天气/季节矩阵」自己生成；商用避开有灯光/建筑版权或商标限制的地标。可信来源逐张留档。
**Keywords:** wallpaper, landscape, copyright, derivative-work, landmark, AI-generation

### 2026-10-08 — 管理端浏览器工具访问不了 localhost
**What happened:** codex 沙箱里无头 Chromium 启动失败（MachPortRendezvous 系统端口被拒）；管理代理侧能起本地 http.server（curl 200），但浏览器工具按策略拒绝导航到 localhost（hostname re…
**Do instead:** 真实浏览器验收只能由用户在允许启动 Chromium 的环境运行测试脚本；完成判据以 v2/browser-check.json 的 status: passed 为准。另外派发 codex 续做任务时，附上"哪些文件已存在、哪些检查已通过"的现状…
**Keywords:** proto-e, v2, browser-acceptance, localhost-policy, codex-dispatch

### 2026-10-16 — 截图能直接看、无头 Chromium 这次能起：先试再说"做不到"
**What happened:** 上一轮对话里声称"当前模型读不了图"，还花了多次尝试用 Swift Vision 做 OCR 并失败；本轮直接用 Read 工具打开 PNG 就看到了完整表格。同样，LESSONS 里记着"沙箱拒绝 Chromium"，但本轮用 ~/Libr…
**Do instead:** 遇到图片先 Read 一次；遇到浏览器验收先按 prototypes/tests/*-browser.mjs 的启动方式试一次（候选 executablePath 全部尝试、保留每个失败原因），只有真的失败才标 blocked。环境限制随会话变化，…
**Keywords:** image-read, screenshot, headless-chromium, playwright, verification

### 2026-10-16 — 测试预期要先读演示图里的节点级覆盖
**What happened:** 触发器画布 v2 验收里"双击→惊起飞鸟"失败，实际走到"摸头·亮灯"——因为当时处于夜段，而演示图给「夜·星空」配了节点级覆盖 dblclick→亮灯，覆盖优先于全局规则，行为是对的，错的是测试预期。
**Do instead:** 写验收脚本前先把演示数据里的"覆盖/例外"列一遍，按当前变量（时段/天气/空闲）推导预期；一个失败项先核对是实现错还是预期错，再改代码。
**Keywords:** trigger-canvas, test-expectation, override-priority

### 2026-10-16 — 悬停计时器和长按计时器会打架
**What happened:** 触发器画布里长按水面热点 0.8s 后正确播了「长按回应」，但 1s 时悬停停留计时器也到点，把它顶成了「悬停回应」。原因：pointerdown 没有取消 pointermove 启动的悬停计时。
**Do instead:** 指针按下即清掉悬停计时并把悬停态清零；悬停回调里再校验"当前没有按压"。凡是多个基于时间的手势（悬停/长按/双击）共存，先画一张"哪个状态下哪些计时器必须停"的表。
**Keywords:** trigger-canvas, gesture, hover, longpress, timer

### 2026-10-16 — 中转站密钥 401 先分清"域名错"还是"模型没放开"
**What happened:** 用户给的「token hub」文生图密钥，在 tokenhub.tencentcloudmaas.com（文档里的国际/广州域名）全部 401「Key 不存在」；换到 tokenhub.tencentmaas.com 才能列模型，但对所有生图…
**Do instead:** 拿到第三方密钥先用免费的 /v1/models 探测域名；拿到 401006 一类"不在允许列表"的错就停止换路径，直接写入 ASKS 请用户放开模型，把其它能跑通的链路（这次是腾讯云图生视频）先跑通并交付。
**Keywords:** tokenhub, api-key, 401006, allowed-list, tencent-vclm, kling
