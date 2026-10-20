<!--format INDEX is a navigation map ONLY: '- path — one sentence' bullets under '## <area>' headings. No dates, status, decisions, or lessons here — those live in PROJECT_STATE.md / DECISIONS.md / LESSONS.md.-->
# INDEX

## Memory & instructions (orbital/)
- orbital/PROJECT_STATE.md — current state snapshot (overwrite-in-place, not a changelog).
- orbital/DECISIONS.md, orbital/LESSONS.md — durable decisions; durable heuristics + numbered playbooks.
- orbital/ASKS.md — open asks waiting on the user（待拍板事项的唯一登记处；agent 不改此文件，条目以 ask id 在记忆与日报中被引用）。
- orbital/instructions/{project_goals,user_directives}.md — mission/scope/rules + numbered user directives (source of truth for outreach rul…
- orbital/skills/ — per-task skills (article-writing 中文长文双版竞稿系统, competitor-watch, x-cold-dm, adjacent-repo-scan v4, x-kol-rate-inquiry, orb…

## Strategy (strategy/)
- strategy/ACTIVE-value-prop-layers.md — canonical value-prop layer playbook (L0–L4 stack + canonical problem statement + objection ladder);…
- strategy/ACTIVE-growth-experiments.md — weekly growth ritual: ICE backlog, verdicts, learning log, round history (latest round = R19).
- strategy/ACTIVE-competitive-landscape.md — v3 competitive set + adjacent patterns (source of truth for 竞品评估、v3 判例与对外禁语)。
- strategy/ACTIVE-strategy-reframe-2026-04.md, strategy/ACTIVE-product-philosophy.md — substrate-building year/3 tracks + product philosophy…
- strategy/ARCHIVE-*.md — archived positioning reframes + Paperclip analysis.

## waic-2026/
- waic-2026/ — exposure playbook, side-event list + source PDF, EN/ZH panelist dossiers (关键人物/), 不优先名单.

## archive/
- archive/ — historical snapshots: MiraclePlus 实际提交版申请全文（申请口径 source of truth）、memory snapshot、DIAGNOSIS-mobile-desktop-offline.

## content-bank/
- content-bank/{index.md,drafts/,published/,animations/,x-posts/} + demos/2026-08-11-project-agent-demo-scenarios.md — content pipeline: mas…
- content-bank/pitch-kit.md — canonical outreach intro + verbatim cold-DM source.
- content-bank/style-reference-*.md, content-bank/zh-slogan-translations.md — per-channel voice references（含观猹/特工宇宙文风）+ ZH slogan renderings…

## download-site/
- download-site/index.html — 国内可达的中文官网与下载页（产品叙事、系统识别、OSS 国内直连、GitHub 备用线路、SHA256）。
- download-site/assets/ — 从产品仓复用的 Geist 字体与真实界面截图。
- download-site/{sync-release-to-oss.sh,SETUP.md} — GitHub Release 同步阿里云 OSS 脚本与上线手册.

## agent_output/
- agent_output/site-reviews/ — orbital.top 周度巡检日报（每周一 trg_1e809a50），一日一文件；skill: orbital-top-site-review。台账/基线对照 = agent_output/2026-10-08-o…
- agent_output/orbital-bp/ — Orbital BP：deliverables 存 PPTX，.build 存构建脚本与预览图；design-system-sentient-style.md 是 BP 视觉母题库，改任何对外 PPT 前先读。
- agent_output/{x-cold-dm-pool*,x-social-circle-exclude,x-follow-dm-tracker,tutti-*,non-x-influencer-list-*,outreach-tier-classification,tie…
- agent_output/release-radar/ — 每日发布雷达（trg_c61c8d79），一日一文件，命中即产 X/HN 储备 draft。
- agent_output/competitor-dynamics/ — 竞品动态扫描日报（每周一/周四 trg_030781a2；v3 11 家 + adjacent watchlist，只报 Δ），一日一文件；skill: adjacent-repo-scan（v4 扫描模…
- agent_output/*-x-painpoint-radar.md — X 跨 agent 交接雷达（一日一文件，每日 10:30 CST；现役 target = 显式跨 agent 项目连续性提问）。
- agent_output/2026-10-16-dahuang-probe-post-replies.md — 大黄需求探针帖读数 + 回复区分类（「现在怎么凑合」）+ 回复区自荐的竞品替代方案清单。
- agent_output/2026-10-20-github-funnel-review.md — 脉冲期 GitHub 漏斗 11 层拆解（曝光→uniques→star→README-zh→clone→安装→key→project→turn→回访→issue），含 OS…
- agent_output/telemetry/ — 生产遥测读数（一日一文件，Railway Postgres）；2026-10-13-telemetry-audit.md 是口径 source of truth（真实外部安装定义、counters 只当下界、Umami do…
- agent_output/event-radar/ — 每日活动雷达日报（一日一文件）+ event-roster.md 活动台账（去重基准、状态、优先城市）；skill: event-radar。
- agent_output/kol-placement-tracking/ — KOL 付费投放归因台账（一日一文件）+ assets/ 创作者后台截图；kol-creator-data-export-request.md 是向创作者要「逐帖后台导出」的模板。
- agent_output/watcha-feedback/ — 观猹用户反馈日报（一日一文件）。
- agent_output/2026-10-10-watcha-conference-{booth-prep,talk-prep}.md + 2026-10-10-watcha-booth-form-answers.md + 2026-10-11-watcha-booth-ga…
- agent_output/2026-10-13-x-paid-benchmarks-and-growth-references.md + 2026-10-13-paid-partnership-ask-list.md — X 付费投放 CPM 基准（一手报价 + 外部对照）与…
- agent_output/2026-10-14-glow-partners-mcn-proposal-review.md + 2026-10-16-chishang-mcn-proposal-review.md — 两家 MCN 提案评估（结构要点 + 缺口 + 重启条件 +…
- agent_output/2026-10-19-yc-progress-update.md + 2026-10-18-three-month-progress.md — 申请表用进展底稿：YC Progress 英文稿（截至 10-19）+ 近三个月（7-18→10-18）事…
- agent_output/2026-10-15-waytoagi-channel-review.md — WaytoAGI（通往AGI之路）是什么 + 可作宣发渠道的活动梳理。
- agent_output/2026-10-1{1,2,3}-*.md + 2026-10-16-coaligne-evaluation.md + 2026-10-16-ai-memory-evaluation.md + 2026-10-17-hollyclaw-evaluat…

## 展会印刷物料
- output/pdf/watcha-2026/ — 观猹 A5 双面传单、可编辑构建脚本、最终 PDF 预览与展位尺寸适配说明。
- output/pdf/goai-2026/ — GOAI 2026 成品物料（A5 单页 / 台卡 / 访谈卡 / 海报 / 贴纸等），可复用于观猹展位。

## Archive (read on demand)
- orbital/PROJECT_STATE_ARCHIVE.md — older entries demoted from PROJECT_STATE.md (read on demand).
- orbital/DECISIONS_ARCHIVE.md — older entries demoted from DECISIONS.md (read on demand).
- orbital/LESSONS_ARCHIVE.md — older entries demoted from LESSONS.md (read on demand).
