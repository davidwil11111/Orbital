# LESSONS_ARCHIVE.md (demoted entries, read-on-demand)

4. **Synthetic Stars Don't Generate Flywheel (NEW, 2026-06-30).** Bot stars get you to the trending THRESHOLD but not the trending VALUE. Th… <!--mem id:synthetic-stars-no-flywheel created:2026-06-30 touched:2026-06-30-->

5. **GitHub Trending Threshold = 30 Stars/Day × 14 Days (NEW, 2026-06-30).** Trending algorithm triggers when a repo hits ~30 stars/day sust… <!--mem id:github-trending-30-stars-threshold created:2026-06-30 touched:2026-06-30-->

6. **KOL DM Framework — Specific Beats Pitchy (NEW, 2026-06-30).** Most effective KOL DM pattern: (1) call out ONE specific detail from thei… <!--mem id:kol-dm-framework-specific-beats-pitchy created:2026-06-30 touched:2026-06-30-->

7. **Trending Naturally Attracts Low-Quality Stars (NEW, 2026-06-30).** Even 100% organic projects get inflated star counts when they hit Tr… <!--mem id:trending-attracts-low-quality-stars created:2026-06-30 touched:2026-06-30-->

8. **Decision-to-execution gap on watchlist promotions (NEW, 2026-07-07).** A DECISIONS entry saying "X promoted to v3 active" does NOT mean… <!--mem id:trending-attracts-low-quality-stars created:2026-06-30 touched:2026-06-30-->

10. **Repo-name drift between cron template and v3 set survives for weeks; GitHub honors redirects (NEW, 2026-07-08; updated 2026-07-17).**… <!--mem id:trending-attracts-low-quality-stars created:2026-06-30 touched:2026-06-30-->


[… fixture trimmed …]

10. **GitHub traffic API 是付费投放归因的必需面（2026-10-13 落地）。** 事故背景：@mylifcc 的付费帖用了裸 `github.com` 链接（没用我方 `orbital.top/xz/<handle>` 追踪链接），Umami 的 `u… <!--mem id:github-traffic-token-2026-10-13 created:2026-10-13 touched:2026-10-13-->

## [archived 2026-10-14]

4. **付费帖用「基准确认价 + 实际 CPM 定性」双口径，别把我们的内容 alpha 付给创作者（2026-10-13，Wave 1 复盘 + 用户指正）。** 事故背景：我用「创作者中位浏览量 × CPM」给 KOSX 的 ¥8,400 包定价，算出公允价 $110–22… <!--mem id:cpm-alpha-2026-10-13-wave-1-cpm- created:2026-10-13 touched:2026-10-13-->
**Apply to:** 一切 KOL 付费投放的定价与复盘、媒体包/社群包谈判。 <!--mem id:paid-placement-two-numbers-2026-10-13 created:2026-10-13 touched:2026-10-13-->

5. **向创作者要后台数据，要「逐帖导出」不要「账号日汇总」—— 逐帖 CSV 里有 `URL Clicks`，那是我们唯一读不到的数（2026-10-13）。** 背景：Wave 1 之后我们只能读到公开浏览量（views）与互动数，**读不到 link clicks，也读不… <!--mem id:csv-url-clicks-2026-10-13-wave-1 created:2026-10-13 touched:2026-10-13-->
**2026-10-13 实测（Wave 1 后台数到位，修正「别信公开 views」的边界）：** 两位创作者的帖子分析页截图证实 **公开 views ≈ 后台 impressions，只差 1.3–1.6%**（KC 12,348 → 12,540；lifcc 47,418… <!--mem id:creator-backend-export-2026-10-13 created:2026-10-13 touched:2026-10-13-->
**Apply to:** 一切付费投放的数据交付与创作者数据核验。 <!--mem id:creator-backend-export-2026-10-13 created:2026-10-13 touched:2026-10-13-->

6. **判帖子形态（有无视频/图）不能靠 `extract`／页面读回，要用 JSON 元数据（2026-10-13，lifcc Wave 1 帖实测）。** 事故：10-12 与 10-13 两轮都把 @mylifcc 的 Wave 1 帖记成「纯文本长帖」，实际它挂着一条… <!--mem id:x-post-shape-json-metadata-2026-10-13 created:2026-10-13 touched:2026-10-13-->
 <!--mem id:edgeone-cdn-not-analytics created:2026-10-08 touched:2026-10-08-->

7. **行业基准先看分母，再看我们在漏斗哪一层掉队（2026-10-13，行业数据基准调研）。** 事故背景：连续两天围绕「CPM 锚是不是太 strict」争论，调研后发现价格层我们落在行业最便宜一端（X 广告 Website Traffic $9–13、Okara 创作者… <!--mem id:2026-10-13-cpm-strict-x-website- created:2026-10-13 touched:2026-10-13-->

## [archived 2026-10-16]

4. **Telemetry 生产库读取路径 = `railway ssh` 进服务容器内跑脚本，不是本机直连（2026-10-13 实测）。** 事实：Orbital 遥测落在 Railway 项目 agentos-relay 的 Postgres（表 `pings`，每 in… <!--mem id:telemetry-railway-ssh-2026-10-13 created:2026-10-13 touched:2026-10-13-->

5. **买「打包投放」先定件数与选号口径，再谈价格（2026-10-13，KOSX 第二方案 ¥4,000 研判）。** 对方给的「四个 top 账号转发 + 评论测评」里藏着两处计价膨胀：① 「转发带评论」是一条引用帖，会被拆成「帖位 + 评论」两件卖；② 「top 账号」按… <!--mem id:telemetry-railway-ssh-2026-10-13 created:2026-10-13 touched:2026-10-13-->

6. **自动模板 cold-DM 是不可再生资源：一次烧完，别指望二次触达（2026-10-13，用户："不要问之前自动化x问的了，感觉那边已经burn bridges了，太不真诚了"）。** 事实：2026-08 的 cold-DM 池一次发了 200+ 条（EN 侧至少 1… <!--mem id:template-cold-dm-one-shot-2026-10-13 created:2026-10-13 touched:2026-10-13-->

7. **「付费请大V转发成功」的先例，先查关系结构，再当市场价用（2026-10-13，QClaw / steipete 案例）。** 用户给的一手数据：QClaw 花约 ¥5,000 请 @steipete（Peter Steinberger）转发一次 → +1,000 粉… <!--mem id:relational-endorsement-not-market-price-2026-10-13 created:2026-10-13 touched:2026-10-13-->

8. **量 KOL 中位 views：排除未成熟帖（发布 <24h）和引用帖，否则中位被严重低估、CPM 被高估近 3 倍（2026-10-14，@Xudong07452910 报价复核，用户要求「帮我填数据」时自查发现）。** 事故：10-13 对该号的读数只取了 profi… <!--mem id:kol-median-immature-posts-2026-10-14 created:2026-10-14 touched:2026-10-14-->

9. **KOL 定价：单条按中位，系列按均值（2026-10-14，用户指正：「大黄的帖按理应该有 $100/贴，这是常态，给 ¥290 让人觉着没诚意」）。** 事故：给大黄长期合作定价时把 LESSONS #4「定价按中位」机械套到月度系列上，算出 ¥215/条，比我们自己… <!--mem id:kol-2026-10-14-100-290-lessons-4 created:2026-10-14 touched:2026-10-14-->

## [archived 2026-10-20]

[merged 2026-10-20 into id:2026-10-14-15-builder-heuristic--mb54f]

4. **买用户研究：要「跑完门槛 + 访谈」，不要「填表报告」（2026-10-14，用户："要不我要求跑一个多 session 的 project 之后接受访谈？我估计得不到什么太多有用的反馈"）。** 我先给 15 位 Builder 设计了一页式书面模板 + 由卖方出汇总… <!--mem id:2026-10-14-session-project-15-bu created:2026-10-14 touched:2026-10-14-->

5. **付费试用没有信号；用户研究只付给「自然留存」和「自然流失」的人（2026-10-14，用户："强制别人使用的没有什么信号，别人当工作一样用……比不了自然用户的反馈"）。** 这条修正 LESSONS #14：访谈形式对，但「付钱让人跑门槛再访谈」的前提错——目标用户拿钱… <!--mem id:2026-10-14-lessons-14-discord-ki created:2026-10-14 touched:2026-10-14-->
   **补充（2026-10-14 同日，用户再修正）：** 流失访谈与前 15 分钟录屏也被否：闲聊群里找不到流失者、没人填名册；安装问题用户从朋友处一手反馈「都装上了」，优先于遥测推断（LESSONS #1 子规则）。真用户的长期合作用「floor + 按可验证到达付 +…

## [archived 2026-10-20]

3. **渠道可观测性是可行性门槛：不可观测的渠道 = 无法迭代 = KILL 判据（NEW, 2026-08-31, Card 7 Discord 五轮盲发后 KILL）。** Discord showcase 帖 25 天完全不可观测（web a11y 树无服务器名、trac… <!--mem id:kill-new-2026-08-31-card-7-disco created:2026-08-31 touched:2026-09-01-->
   **「收缩批准请求」子规则（2026-08-31, Card 20 连续两轮 #1 未开工）：** 一张卡连续两轮是最高优先但从未执行，问题几乎总是批准机制而不是格式/证据——「批准整个系列」的二进制门槛太高。**Heuristic：重新提案时把请求收缩到一个具体单元**（… <!--mem id:radar-user-visible-only created:2026-08-24 touched:2026-08-24-->

5. **归因口径不该让漏斗变贵：短链是「可测量」的代价，先问这笔代价值不值（2026-10-14，用户定）。** 背景：@shengtang135754（麦麦提）的 ¥300 帖用裸 `github.com` 链（挂在作者自回复里），我判「逐帖归因为零」并开 ask 请用户让创… <!--mem id:2026-10-14-shengtang135754-300-g created:2026-10-14 touched:2026-10-14-->

7. **可复算的透明报价会让对方自己把水分砍掉，比砍价有效（2026-10-15，大黄回复 KOSX 方案）。** 事实：我们 10-14 发出的方案把每个数字的来源、口径与复算命令全部写明（逐帖中位 × CPM、成员逐号中位、引用帖折价），没有砍价、没有否定任何人。大黄（卖方… <!--mem id:2026-10-15-kosx-10-14-cpm-kosx-6 created:2026-10-15 touched:2026-10-15-->

9. **合作备忘发出前要做两道压测：把付款条款按月份走一遍时间线，把每个激励项算一次「典型情况下值多少钱」（2026-10-15，大黄用户大使备忘自查）。** 事故：我写的 §3 同时有「首月确认当天付」和「每月 5 日结上月」，第二个月会把首月付两次；「真用起来 ¥150」没… <!--mem id:2026-10-15-3-5-150-10-80-heurist created:2026-10-15 touched:2026-10-15-->
