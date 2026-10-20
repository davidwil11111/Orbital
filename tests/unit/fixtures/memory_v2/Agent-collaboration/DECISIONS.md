<!--format DECISIONS entries: '## <slug>' then Chose / Reason / Rejected. Supersede or replace old entries when a decision changes; never leave contradictions.-->
# DECISIONS

## D1 — demo 与剩余 spec 推进 <!--mem id:d1-demo created:2026-09-01 touched:2026-09-01-->
Chosen: 2026-09-01 用户确认基础文档沉淀完成后，授权把剩余 SPEC-03～SPEC-09 全部跑完（含 SPEC-08 demo fixture 与 SPEC-09 交付收敛）；执行方式 = claude-code 作为编排者派发子 agent（低端模型做实现…
Reason: 基础文档（竞品、场景、方向池、PRD、路线图、SPEC-00 契约）已完成，用户明确指示「让 claude code 派发子 agent 把剩下的 spec 跑完，用低端模型执行子 agent 任务」。
Rejected: 继续 demo 后置等逐 spec 确认；主 session 亲自实现全部 spec（用户指定编排方式）。

## D2 — 语言约定 <!--mem id:d2 created:2026-09-01 touched:2026-09-01-->
Chosen: 文档中文为主，术语与 repo 名用英文。
Reason: 用户中文交流；Kimi 为中国公司；技术术语保英文准确。

## D3 — 竞品研究方法 <!--mem id:d3 created:2026-09-01 touched:2026-09-01-->
Chosen: fanout 3 个 worker 并行联网核实（Claude Code / Codex / 其他+趋势），原始研究存 docs/research/，主 agent 核实后综合进 docs/02-competitive-landscape.md。
Reason: 竞品信息必须联网核实且标注来源日期（项目规则）；并行省时。

## D4 — 文档编号体系 <!--mem id:d4 created:2026-09-01 touched:2026-09-01-->
Chosen: docs/00 简报、01 Orbital 现状、02 竞品综合、10 场景、11 方向池；原始研究层 docs/research/。
Reason: 规划文档与原始证据分层，评审可读、论据可溯。

## D5 — 核心立论（作业主线） <!--mem id:d5 created:2026-09-01 touched:2026-09-01-->
Chosen: Team Feature = Orbital「project as unit」逻辑的自然延伸：session→project（已完成）→ team（下一步）。产品采用两层文件模型：经 Manager 编译的 durable project knowledge、配置…
Reason: README 实证 Orbital roadmap 无任何团队字样（单人假设），且竞品 Claude Code/Codex 同样卡在单人假设——这是「定位雷同」质疑的正面解法。分层后同时满足 clean clone 可运行、项目学习可审查，以及本地运行数据持久可观…
Rejected: 把 team feature 讲成功能堆砌清单（不构成差异化叙事）。

## D6 — demo 运行边界 <!--mem id:d6-demo created:2026-09-01 touched:2026-09-01-->
Chosen: 交付 repo 自包含，不依赖 Orbital 安装、daemon 或本地 API；沿用 Orbital 的项目文件语义，由 repo 内的 CLI、事件调度器、Skills 和看板共同执行文件协议。
Reason: Kimi 应能只 clone 一个 repo 即理解和运行原型；要求额外安装 Orbital 会破坏交付闭环。
Rejected: 调用 Orbital 本地 API 的 companion demo。

## D7 — 角色与事件驱动 Manager <!--mem id:d7-manager created:2026-09-01 touched:2026-09-01-->
Chosen: Manager/Member 是与 agent 类型正交的角色；成员 Report Submitted 后由文件事件和 `teamd` 启动新的短生命周期 Manager Agent Run，自动串行完成集成与知识编译。
Reason: 任意 agent 都能担当 Manager，且不依赖向某个长期交互窗口注入事件；项目文件而非 session 保存连续性。
Rejected: 固定 Codex 为 Manager；等待用户提醒 Manager 检查 inbox；把长期 agent session 作为事实来源。

## D8 — 工作对象与成员入口 <!--mem id:d8 created:2026-09-01 touched:2026-09-01-->
Chosen: 分离 Confirmed Tasks、Potential Tasks、Open Questions；`/team claim <project-name> <task-id-or-query>` 唯一匹配时原子认领并返回上下文，Potential Task 必须…
Reason: 执行承诺、IM 提取出的候选和待澄清问题有不同生命周期；分离后才能避免 agent 抢占未确认工作或在关键问题缺失时猜测。

## D9 — 跨 session spec 执行 <!--mem id:d9-session-spec created:2026-09-01 touched:2026-09-01-->
Chosen: 实现拆为 SPEC-00～SPEC-09，每个 spec 在单独 session 完成；handoff 使用 Spec Index + Completion Record + Orbital PROJECT_STATE/DECISIONS/LESSONS/INDE…
Reason: 每阶段保持单一可验收结果，并让新 session 无需聊天历史即可冷启动。

## D10 — Git checkpoint 与 push 策略 <!--mem id:d10-git-checkpoint-push created:2026-09-01 touched:2026-09-01-->
Chosen: 2026-09-01 应用户要求在工作区根目录完成 `git init`（main 分支）+ remote `origin` = https://github.com/zqiren/orbital-team-collab.git；本地 git identity 使…
Reason: 用户 2026-09-01 明确要求 setup git，并在 SPEC-00 完成后明确要求“commit 然后 push，每一次完成之后都 commit 和 push 一次”。
Rejected: 等 SPEC-01 再 init；每次 spec push 都重复询问；提交 orbital 运行时数据；未经授权向 Kimi/其他人发送交付物。

## D11 — SPEC-00 design review 收敛 <!--mem id:d11-spec-00-design-review created:2026-09-01 touched:2026-09-01-->
Chosen: 2026-09-01 用户确认两层文件模型并将产品入口改为 `/team`；其余契约项采用建议默认值：仅 `Queued`、`Running`、`Retryable` Integration Job 占用 project integration slot，`Awa…
Reason: 消除命令歧义、跨 project ID 歧义和 integration head-of-line blocking，并让 dashboard、knowledge producer 与后续独立 spec 有共同契约。
Rejected: 把本地 runtime/session logs 提交到 Git；继续使用歧义的 `/project <project-or-subcommand>` 语法；让 Awaiting Knowledge 独占 integration slot；promote 自动…

## D12 — 原型实现技术边界 <!--mem id:d12 created:2026-09-01 touched:2026-09-01-->
Chosen: Team Workspace runtime、domain、`teamctl`、`teamd` 和本地 Dashboard adapter 统一使用 Python 3.11+；规范数据采用 JSON Schema Draft 2020-12，锁定 `jsonsch…
Reason: 单一 domain/storage package 能让 CLI、daemon、Dashboard 和 adapter 复用状态机；低依赖更适合 clean-clone 作业。拆分 merged/completed 避免 Dashboard 和恢复逻辑把“代码已合…
Rejected: Python/Node 双 runtime 业务层；React/Vite + 独立 API 数据库；让 agent 或前端直接写 JSON；代码 merge 后提前发 `integration.completed`。

## D13 — Durable knowledge 的 Git 闭环 <!--mem id:d13-durable-knowledge-git created:2026-09-01 touched:2026-09-01-->
Chosen: Knowledge Proposal apply 后只 stage allowlisted canonical memory path，并创建独立的本地 knowledge commit；Knowledge Change Summary 同时记录 source m…
Reason: 只有 commit 后 durable knowledge 才真的能随 clone/PR 传播；独立 commit 让代码 merge 与知识编译分别审查和恢复，并避免覆盖用户未提交工作。
Rejected: 只修改工作树却宣称 git-native；amend 成员/code merge；自动提交无关工作树变化；创建空 knowledge commit；自动 push。

## D14 — File runtime package 与安全 marker <!--mem id:d14-file-runtime-package-marker created:2026-09-01 touched:2026-09-01-->
Chosen: 后续 domain、CLI、daemon、adapter 统一复用 `src/orbital_team/` 单 package；规范 schema 仍以 `schemas/v1/orbital-team.schema.json` 为源，并作为 wheel data…
Reason: SPEC-02～07 需要稳定的共享 storage 入口；marker 让 runtime-only reset 能验证精确目标，同时不把 runtime 或绝对 workspace 路径放进 Git。
Rejected: 每个调用方自行读写 JSON；复制第二份可漂移 schema；仅凭目录名执行 reset。

## D15 — Member CLI actor 绑定 <!--mem id:d15-member-cli-actor created:2026-09-01 touched:2026-09-01-->
Chosen: `teamctl member join` 把 Member identity 绑定到当前 named-branch worktree；后续 claim/start/block/report 从当前 worktree 的唯一 binding 推导 `member:…
Reason: 冻结命令语法没有重复携带 member 参数，且 actor 不能只信任 agent 自报；worktree binding 同时给 SPEC-03 adapter 和 SPEC-04 Report/commit review 一个确定性的身份与 Git 边界。
Rejected: 每次 mutation 接受任意 `--member`；按环境变量或 agent 自报 actor；让 slash adapter 另建 identity 状态。
