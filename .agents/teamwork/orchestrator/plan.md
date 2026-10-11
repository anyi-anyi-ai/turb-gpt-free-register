# Execution Plan: UNED Email Activation & O365 Forwarding

## Phase 0: Survey & Technical Exploration (Current)
- [ ] Dispatch 3 parallel Explorers:
  - **Explorer 1 (Codebase & Pipelines)**: Explore repository scripts, Playwright automation, Microsoft Graph/REST API logic, O365 forwarding configuration scripts, login_hint flows.
  - **Explorer 2 (Database & Account Queue)**: Inspect `turb.sqlite3`, `uned_accounts` schema, the 52 pending accounts with `forwarding_enabled = 0`, credential completeness (passwords, recovery personal outlook, etc.).
  - **Explorer 3 (Environment, Proxy & Obsidian)**: Inspect Resin proxy endpoint (`http://NL:...@127.0.0.1:2260`), network reachability, and Obsidian destination (`J:\obsidian库\ThinkDoKit-Full-1.2.0\邮箱\学校邮箱\UNED学校邮箱汇总表.md`).
- [ ] Synthesize explorer findings into `PROJECT.md`.

## Phase 1: Pipeline Validation (Canary / Milestone 1)
- [ ] Dispatch Worker to verify the complete 3-stage pipeline end-to-end on 1-2 pending accounts or dry-run.
- [ ] Verify Stage 0 (Password retrieval/setting if needed), Stage 1 (UNED portal activation), Stage 2 (O365 forwarding + keep copy).
- [ ] Verify SQLite status update and error handling logic.

## Phase 2: Full Batch Execution (Milestone 2)
- [ ] Dispatch Worker to process all remaining pending accounts sequentially/concurrently with robust retry and error recovery.
- [ ] Real-time logging and status persistence in SQLite.
- [ ] Handle any transient `Cuenta Bloqueada` or network blips.

## Phase 3: Asset Sync & Verification (Milestone 3)
- [ ] Dispatch Worker to update Obsidian summary table and individual account notes.
- [ ] Dispatch Reviewers, Challengers, and Forensic Auditor to independently verify:
  - 100% of accounts in `turb.sqlite3` have `forwarding_enabled = 1` (or justified permanent failures).
  - Forwarding settings and "keep copy" verified.
  - Obsidian markdown table matches database state.
- [ ] Synthesize results and report victory to Sentinel.
