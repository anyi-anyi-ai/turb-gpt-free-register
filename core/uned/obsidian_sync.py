"""
UNED 账号与 Obsidian 笔记库自动同步组件
将已注册的 UNED 学校邮箱资产实时同步到指定的 Obsidian 目录:
J:\\obsidian库\\ThinkDoKit-Full-1.2.0\\邮箱\\学校邮箱
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger("uned.obsidian_sync")

DEFAULT_OBSIDIAN_DIR = Path(r"J:\obsidian库\ThinkDoKit-Full-1.2.0\邮箱\学校邮箱")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ACCOUNTS_JSON = _PROJECT_ROOT / "data" / "uned_accounts.json"
_IDENTITIES_JSON = _PROJECT_ROOT / "data" / "uned_identities.json"


def _get_identity_map() -> Dict[str, dict]:
    """读取身份证缓存以补全中文真实姓名等信息"""
    if _IDENTITIES_JSON.exists():
        try:
            items = json.loads(_IDENTITIES_JSON.read_text(encoding="utf-8"))
            return {x.get("doc_number", ""): x for x in items if x.get("doc_number")}
        except Exception:
            pass
    return {}


def format_account_markdown(acc: Dict[str, Any], raw_name: str = "") -> str:
    """生成单个账号的漂亮 Obsidian Markdown 笔记内容"""
    identifier = acc.get("uned_identifier") or acc.get("identifier") or "未知"
    student_email = acc.get("student_email") or f"{identifier}@alumno.uned.es"
    email = acc.get("email") or ""
    pwd = acc.get("password") or ""
    phone = acc.get("phone") or ""
    doc_number = acc.get("doc_number") or ""
    en_name = acc.get("name") or acc.get("full_name") or ""
    display_name = f"{raw_name} ({en_name})" if raw_name and raw_name != en_name else (raw_name or en_name)
    created_at = acc.get("created_at", "")
    if created_at and "T" in created_at:
        created_at = created_at.replace("T", " ")[:19]

    # 激活状态提取
    status = acc.get("status") or "registered"
    extra = acc.get("extra") or {}
    if isinstance(extra, str):
        try: extra = json.loads(extra)
        except Exception: extra = {}
    is_activated = (status == "activated") or bool(extra.get("activated"))
    status_text = "✅ 已激活 (密码已生效，统一身份认证与学生邮箱已开通)" if is_activated else "⏳ 待激活 (待提交 password.aspx 密码验证)"
    status_short = "已激活" if is_activated else "待激活"

    # 转发状态提取
    fwd = acc.get("forwarding_enabled")
    if fwd is None:
        fwd = extra.get("forwarding_enabled")
    is_forwarded = (fwd == 1) or (fwd is True) or (str(fwd) == "1")
    forwarding_text = "✅ 已开启 (已自动转发至绑定个人 Outlook 邮箱并保留副本)" if is_forwarded else "❌ 未开启"
    forwarding_badge = "✅ 已开启" if is_forwarded else "❌ 未开启"
    forwarding_short = "已开启" if is_forwarded else "未开启"

    md = f"""---
title: UNED 学校邮箱 - {identifier}
created: {created_at}
status: {status_short}
forwarding: {forwarding_short}
tags:
  - 学校邮箱
  - UNED
  - 教育邮箱
  - 开发者优惠
---

# 🎓 UNED 西班牙国立远程大学学生邮箱

> [!INFO] 账号基本信息
> - **标识符 (用户名)**: `{identifier}`
> - **学生邮箱**: `{student_email}`
> - **账户密码**: `{pwd}`
> - **激活状态**: {status_text}
> - **转发状态**: {forwarding_text}
> - **绑定 Outlook 邮箱**: `{email}`
> - **绑定手机号**: `{phone}`
> - **认证姓名**: {display_name}
> - **身份证号**: `{doc_number}`
> - **注册时间**: `{created_at}`

---

## 🔑 凭据速查与复制

```
账号/标识符: {identifier}
学生邮箱: {student_email}
账户密码: {pwd}
激活状态: {status_short}
转发状态: {forwarding_badge}
绑定Outlook: {email}
手机号: {phone}
身份证号: {doc_number} ({display_name})
```

---

## 🌐 常用入口与使用指引

- **UNED 校园网入口**: [https://app.uned.es](https://app.uned.es)
- **UNED 学生邮箱登录 (Office 365)**: [https://outlook.office365.com/owa/?path=/options/forwarding](https://outlook.office365.com/owa/?path=/options/forwarding)
- **Microsoft 365 门户**: [https://portal.office.com](https://portal.office.com)
- **GitHub Student Developer Pack 申请**: [https://education.github.com/pack](https://education.github.com/pack)

---
*本文件由 UNED 自动化注册系统实时生成并同步。*
"""
    return md


def sync_account_to_obsidian(acc: Dict[str, Any], target_dir: Optional[Path] = None) -> Optional[Path]:
    """同步单个账号到 Obsidian"""
    dest_dir = target_dir or DEFAULT_OBSIDIAN_DIR
    if not dest_dir.exists():
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"创建 Obsidian 目标目录失败: {e}")
            return None

    id_map = _get_identity_map()
    doc_num = acc.get("doc_number", "")
    raw_name = id_map.get(doc_num, {}).get("raw_name", "")
    identifier = acc.get("uned_identifier") or "account"
    
    # 优先查找是否已有该 identifier 结尾的已有笔记文件，避免名称细微变动造成重名孤岛
    existing_files = list(dest_dir.glob(f"*_{identifier}.md"))
    if existing_files:
        file_path = existing_files[0]
    else:
        name_part = raw_name if raw_name else (acc.get("name") or "user")
        file_name = f"UNED_{name_part}_{identifier}.md".replace(" ", "_")
        file_path = dest_dir / file_name

    md_content = format_account_markdown(acc, raw_name=raw_name)
    file_path.write_text(md_content, encoding="utf-8")
    logger.info(f"已同步账号笔记至 Obsidian: {file_path}")

    # 同时更新总览汇总表
    update_summary_table(target_dir=dest_dir)
    return file_path


def update_summary_table(target_dir: Optional[Path] = None):
    """更新 UNED 所有账号的 Markdown 汇总总表"""
    dest_dir = target_dir or DEFAULT_OBSIDIAN_DIR
    if not dest_dir.exists():
        return

    import core.db as db
    records = []
    try:
        records = db.list_uned_accounts()
    except Exception:
        if _ACCOUNTS_JSON.exists():
            try:
                records = json.loads(_ACCOUNTS_JSON.read_text(encoding="utf-8"))
            except Exception:
                records = []

    if not records:
        return

    id_map = _get_identity_map()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 去重保留最新有效记录 (按 doc_number 去重)
    unique_accs = {}
    for r in records:
        doc = r.get("doc_number", "")
        if doc:
            unique_accs[doc] = r

    lines = [
        "---",
        "title: UNED 学校邮箱汇总总表",
        f"updated: {now_str}",
        "tags:",
        "  - 学校邮箱",
        "  - UNED",
        "  - 汇总资产",
        "---",
        "",
        "# 📚 UNED 学校邮箱资产汇总总表",
        "",
    ]

    # 加载 JSON 文件作为辅查
    json_map = {}
    if _ACCOUNTS_JSON.exists():
        try:
            j_list = json.loads(_ACCOUNTS_JSON.read_text(encoding="utf-8"))
            for j in j_list:
                if j.get("doc_number"):
                    json_map[j["doc_number"]] = j
        except Exception:
            pass

    # 统计激活与转发数量
    fwd_total = 0
    act_total = 0
    for doc, acc in unique_accs.items():
        payload = acc.get("payload", {})
        if isinstance(payload, str):
            try: payload = json.loads(payload)
            except Exception: payload = {}
        extra = payload.get("extra", {}) or acc.get("extra", {})
        if isinstance(extra, str):
            try: extra = json.loads(extra)
            except Exception: extra = {}
        j_rec = json_map.get(doc, {})

        status = acc.get("status") or j_rec.get("status") or "registered"
        is_act = (status == "activated") or bool(extra.get("activated")) or bool(j_rec.get("extra", {}).get("activated"))
        if is_act:
            act_total += 1

        fwd = acc.get("forwarding_enabled")
        if fwd is None:
            fwd = extra.get("forwarding_enabled")
        if fwd is None:
            fwd = j_rec.get("forwarding_enabled")
        if (fwd == 1) or (fwd is True) or (str(fwd) == "1"):
            fwd_total += 1

    lines.extend([
        f"> **最后同步时间**: `{now_str}` | **有效账号总数**: `{len(unique_accs)}` 个 | **已开通邮箱**: `{act_total}` 个 | **已开通自动转发**: `{fwd_total}` 个",
        "",
        "| 序号 | 标识符 / 用户名 | 学生邮箱 (@alumno.uned.es) | 账户密码 | 激活状态 | 转发状态 | 绑定 Outlook 邮箱 | 绑定的手机号 | 认证姓名 | 证件号码 | 注册时间 |",
        "| :---: | :--- | :--- | :--- | :---: | :---: | :--- | :--- | :--- | :--- | :--- |",
    ])

    for idx, (doc, acc) in enumerate(unique_accs.items(), start=1):
        ident = acc.get("uned_identifier", "")
        payload = acc.get("payload", {})
        if isinstance(payload, str):
            try: payload = json.loads(payload)
            except Exception: payload = {}
        extra = payload.get("extra", {}) or acc.get("extra", {})
        if isinstance(extra, str):
            try: extra = json.loads(extra)
            except Exception: extra = {}
        
        j_rec = json_map.get(doc, {})
        student_email = acc.get("student_email") or extra.get("student_email") or j_rec.get("student_email") or f"{ident}@alumno.uned.es"
        pwd = acc.get("password") or extra.get("password") or j_rec.get("password") or "Uned@2026!Zhang#88"
        email = acc.get("email", "") or j_rec.get("email", "")
        phone = acc.get("phone", "") or j_rec.get("phone", "")
        raw_name = id_map.get(doc, {}).get("raw_name", "") or acc.get("full_name") or acc.get("name") or ""
        created = (acc.get("created_at") or j_rec.get("created_at") or "")[:19].replace("T", " ")

        status = acc.get("status") or j_rec.get("status") or "registered"
        is_act = (status == "activated") or bool(extra.get("activated")) or bool(j_rec.get("extra", {}).get("activated"))
        status_badge = "✅ 已激活" if is_act else "⏳ 待激活"

        fwd = acc.get("forwarding_enabled")
        if fwd is None:
            fwd = extra.get("forwarding_enabled")
        if fwd is None:
            fwd = j_rec.get("forwarding_enabled")
        is_fwd = (fwd == 1) or (fwd is True) or (str(fwd) == "1")
        fwd_badge = "✅ 已开启" if is_fwd else "❌ 未开启"

        lines.append(
            f"| {idx} | `{ident}` | `{student_email}` | `{pwd}` | {status_badge} | {fwd_badge} | `{email}` | `{phone}` | {raw_name} | `{doc}` | {created} |"
        )

    lines.extend([
        "",
        "---",
        "## 🔗 快捷服务链接",
        "- [UNED 校园网入口](https://app.uned.es)",
        "- [Office 365 邮箱登录](https://outlook.office365.com/owa/?path=/options/forwarding)",
        "- [GitHub Student Developer Pack](https://education.github.com/pack)",
        ""
    ])

    summary_file = dest_dir / "UNED学校邮箱汇总表.md"
    summary_file.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"已更新 Obsidian 汇总总表: {summary_file}")


def sync_all_to_obsidian(target_dir: Optional[Path] = None):
    """同步当前所有已知账号到 Obsidian"""
    dest_dir = target_dir or DEFAULT_OBSIDIAN_DIR
    import core.db as db
    records = []
    try:
        records = db.list_uned_accounts()
    except Exception:
        pass

    json_map = {}
    if _ACCOUNTS_JSON.exists():
        try:
            j_list = json.loads(_ACCOUNTS_JSON.read_text(encoding="utf-8"))
            for j in j_list:
                if j.get("doc_number"):
                    json_map[j["doc_number"]] = j
        except Exception:
            pass

    if not records and json_map:
        records = list(json_map.values())

    id_map = _get_identity_map()
    count = 0
    # 按 doc_number 去重，优先保留包含有效 student_email 和密码的记录
    merged = {}
    for r in records:
        doc = r.get("doc_number", "")
        if not doc:
            continue
        payload = r.get("payload", {})
        if isinstance(payload, str):
            try: payload = json.loads(payload)
            except Exception: payload = {}
        extra = payload.get("extra", {}) or r.get("extra", {})
        if isinstance(extra, str):
            try: extra = json.loads(extra)
            except Exception: extra = {}
        
        j_rec = json_map.get(doc, {})
        pwd = r.get("password") or extra.get("password") or j_rec.get("password", "")
        student_email = r.get("student_email") or extra.get("student_email") or j_rec.get("student_email", "")

        merged[doc] = {
            "uned_identifier": r.get("uned_identifier") or j_rec.get("uned_identifier"),
            "student_email": student_email or f"{r.get('uned_identifier')}@alumno.uned.es",
            "email": r.get("email") or j_rec.get("email"),
            "password": pwd,
            "status": r.get("status") or j_rec.get("status") or "registered",
            "forwarding_enabled": r.get("forwarding_enabled") if r.get("forwarding_enabled") is not None else j_rec.get("forwarding_enabled", 0),
            "extra": extra or j_rec.get("extra") or {},
            "phone": r.get("phone") or j_rec.get("phone"),
            "doc_number": doc,
            "name": r.get("full_name") or r.get("name") or j_rec.get("name"),
            "created_at": r.get("created_at") or j_rec.get("created_at")
        }

    for doc, acc in merged.items():
        sync_account_to_obsidian(acc, target_dir=dest_dir)
        count += 1

    update_summary_table(target_dir=dest_dir)
    return count


if __name__ == "__main__":
    c = sync_all_to_obsidian()
    print(f"成功同步 {c} 个账号至 Obsidian 目录！")
