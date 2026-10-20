<!--format PROJECT_STATE is what is true NOW: current focus, in-progress work, blockers, next steps. Overwrite stale lines; never append dated history. Every line must be understandable without this session's context: concrete names, no unexplained shorthand, no cross-references by list number. One fact = one entry, never duplicated into another section. A dated fact is `[due:YYYY-MM-DD]` right after the list marker (shows on the calendar). Write timeless ("due Jul 28", never "tomorrow"). Things waiting on the user (their decision, their action, something they said they will do) do NOT go here: they are asks in orbital/ASKS.md (see that file's header); a `[user]` line written here is moved there automatically. Never auto-decide: spending money, sending external messages as the user, or irreversible/destructive acts always go to the user, whatever the autonomy setting. Machine attributes (id, created, touched) live in a daemon-managed mem-comment on the next line — never write or edit these comments; leave them exactly where they are.-->
# PROJECT_STATE

## 当前阶段
SPEC-00～09 全部完成且已 push：本地 main == origin/main（HEAD=f78dca2，含 09-02 DESIGN.html design book 与 dashboard 迭代）。09-06 沙箱复测后大部分旧 EPERM 限制不再复现（详见 L…

## 本轮工作（最近完成）
- 第四份用户访谈已归档 docs/research/interview-tencent-peer-20260908.md：企微截图 OCR 重建（受访者为腾讯同事、团队级 Multica 浅度用户、姓名 OCR 未识别暂记 tencent-peer）。核心指认：**IM 信息不…
- 竞对叙事定位研究（经 claude-code 子 agent 三轮问答，全文在 orbital/sub_agents/claude-code/c6684f10.jsonl）：本方案 vs @claude/@codex/@grok 属两种范式——它们是 mention-driv…

## 下一步
1. 工作树有未提交的 skills 迁移（仓库根 `skills/` 已删、`orbital/skills/` 新增 7 个 skill），非本 session 所为，是否纳入 commit 待定。
2. 发送给 Kimi 或其他外部对象仍需用户单独明确授权。
  <!--mem id:dc84d8 created:2026-09-08 touched:2026-09-11 resolved:2026-09-09-->
3. tencent-peer 访谈（docs/research/interview-tencent-peer-20260908.md）受访者姓名待确认：企微标签 OCR 未识别（仅"甘"字提示），确认后重命名文件并同步 INDEX 与跨访谈对比表。
  <!--mem id:df866e created:2026-09-01 touched:2026-09-08 resolved:2026-09-15-->
4. 竞对叙事定位研究结论（mention-driven vs project-resident、Dosu 对标、资产叙事 vs 自动化叙事）尚未沉淀为 docs/research/ 文档，是否写入正稿待用户定。
  <!--mem id:f67239 created:2026-09-11 touched:2026-09-11 resolved:2026-09-15-->
5. 可选环境证据：普通本机浏览器可视化 walkthrough（bind 与 HTTP GET smoke 已在收尾环境真实通过），以及已登录 provider 中的 external Codex/Claude Code Manager/Member rehearsal；不得用…
6. Git checkpoint 链（本地 main，全部完成）：SPEC-00 `902a870`、SPEC-01 `06691b4`、SPEC-02 `83cbf6e`、SPEC-04 `6e30a89`/`fae75d7`、SPEC-05 `ed5c52e`/`4e501…

## Blockers / limitations
- git push 仍缺 HTTPS 凭据：沙箱内 `GIT_TERMINAL_PROMPT=0 git push --dry-run origin main` = "could not read Username"；当前已由用户本机推送（本地 main == origin/m…
- 浏览器级可视化 walkthrough 仍留用户普通本机；本沙箱 loopback bind + 真实 HTTP GET smoke 已两轮复测通过；更严格 sandbox 下 verifier 用 `--dashboard-policy allow` 如实记录失败。
- 编排器：claude-code 子 agent 可用（主用）；codex 0.144.5 / gemini 0.25.2 CLI 存在但 codex 建 PATH alias 报 warning 级 EPERM；claude/dsh/cursor 不在 shell PATH（…
- 非 POSIX 权限与 runner process-tree 行为未做实机验证；根 repo 尚未声明开源许可证；README clone URL 现已可用（repo 已推送 origin）。
