# Project: UNED Student Email Activation & Office 365 Auto-Forwarding

## Architecture
- **Data Source**: SQLite database `f:\github\turb-gpt-free-register\turb.sqlite3` (`uned_accounts` and `email_pool` tables).
- **Network Routing**: Docker Resin proxy at `http://127.0.0.1:2260`.
  - Spain route `http://ES:resin_proxy_c3d7b8e1f0a2496582d9183b@127.0.0.1:2260` used for UNED SSO (`sso.uned.es`), IdP (`idp.uned.es`), and `serviwebng.uned.es`.
  - Microsoft SSO and Outlook Web routed via `ES` or `NL`.
- **Pipeline Stages**:
  - **Stage 0 (Credential Recovery & Setting)**: If account password is not recognized on UNED, submit recovery request to `https://app.uned.es/soid/recordar-password` with solved OCR captcha (`core/uned/captcha_solver.py`), read fresh token from Outlook REST API (`core/outlook_client.py`), and submit password via Playwright.
  - **Stage 1 (Student Mailbox Activation)**: Login UNED SSO portal via Playwright, visit `https://serviwebng.uned.es/correo/activar-correo.asp`, submit activation, confirm response (`20951: Cuenta de correo ya activada`).
  - **Stage 2 (Office 365 Email Forwarding)**: Access Microsoft SSO via `https://login.microsoftonline.com/?login_hint={uid}@alumno.uned.es`, federate via UNED SSO, navigate to Outlook Web settings -> Forwarding, enable forwarding to personal Outlook email, check "Keep a copy of forwarded messages", and save.
  - **Stage 3 (Persistence & Knowledge Sync)**: Update SQLite row (`forwarding_enabled=1, status='activated'`), update Obsidian individual notes and `UNED学校邮箱汇总表.md`.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Account Queue Loading | Query 52 pending accounts with `forwarding_enabled=0` from `turb.sqlite3` | M1 | R1 |
| 2 | Stage 0 Auto-Recovery | Automatic password reset via `recordar-password` + RapidOCR + Outlook REST for unconfirmed accounts | M1 | R2 |
| 3 | Stage 1 Mailbox Activation | Automated UNED SSO login and activation at `serviwebng.uned.es` via ES proxy | M1 | R2 |
| 4 | Stage 2 O365 Forwarding | Automated Office 365 forwarding configuration with "keep a copy" checked | M1 | R2 |
| 5 | Proxy Routing & Resilience | Spanish Resin route routing, asset blocking, and backoff for `Cuenta Bloqueada` | M1 | R3 |
| 6 | Canary Validation | End-to-end execution on 1 Category A and 1 Category B test account | M1 | R1, R2, R3 |
| 7 | Full Batch Execution | Complete batch processing of all 52 remaining accounts | M2 | R1, R2, R3, R4 |
| 8 | Obsidian Sync Enhancement | Add `转发状态` (forwarding status) to summary table and individual notes | M3 | R4 |
| 9 | Multi-Agent Verification | Reviewers, Challengers, and Forensic Auditor verification against acceptance criteria | M4 | Acceptance Criteria |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Pipeline Hardening & Canary Validation | Harden batch runner with ES proxy, recordar-password auto-recovery, ctx2 asset blocking, and run canary | none | IN_PROGRESS |
| M2 | Full Batch Processing | Process all remaining accounts in `turb.sqlite3` to reach 100% forwarding | M1 | PLANNED |
| M3 | Obsidian Asset Synchronization | Update `UNED学校邮箱汇总表.md` and individual notes with forwarding status | M2 | PLANNED |
| M4 | Comprehensive Multi-Agent Audit | Reviewers, Challengers, and Forensic Auditor verification | M3 | PLANNED |

## Interface Contracts
### Pipeline Driver ↔ SQLite Database
- Query: `SELECT id, uned_identifier, email, doc_type, doc_number, full_name, payload_json FROM uned_accounts WHERE forwarding_enabled = 0`
- Success Update: `UPDATE uned_accounts SET forwarding_enabled = 1, status = 'activated', updated_at = datetime('now') WHERE uned_identifier = ?`
- Failure/Lock Update: Record retry queue / transient timestamp, do not set `forwarding_enabled = 1`.

### Pipeline Driver ↔ Obsidian Sync
- Module: `core/uned/obsidian_sync.py`
- Function: `sync_account_to_obsidian(acc_dict, target_dir)`
- Table generator: `update_summary_table(target_dir)`

## Code Layout
- `batch_full_pipeline.py` / `core/uned/runner.py`: Main batch pipeline entry points.
- `core/uned/activator.py`: Stage 0/1 activation functions.
- `core/uned/captcha_solver.py`: RapidOCR ONNX captcha solver.
- `core/outlook_client.py`: Microsoft Graph & REST Outlook reader.
- `core/uned/obsidian_sync.py`: Obsidian dossier and summary table synchronization.
- `turb.sqlite3`: Local SQLite database.
