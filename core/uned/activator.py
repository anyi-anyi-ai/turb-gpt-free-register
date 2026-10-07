# -*- coding: utf-8 -*-
"""
UNED 学生账号与邮箱全自动密码激活组件
功能：
1. 自动连接 Outlook 邮箱读取来自 soid@csi.uned.es 的 "Id Confirmation" 激活信
2. 提取专属 password.aspx 激活链接与 Token
3. 携带代理池自动提交密码并激活统一身份认证 (SSO) 与学生邮箱
4. 更新本地 SQLite 数据库与 Obsidian 知识库状态
"""

import html
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

from curl_cffi.requests import Session as CurlSession

from config import proxy as proxy_cfg_mod
from core.outlook_client import (
    get_account_context,
    _ms_access_token,
    _ms_http,
    _fetch_graph_messages,
    _fetch_outlook_rest_messages,
    OutlookClientError
)
import core.db as db

logger = logging.getLogger("uned.activator")

def activate_single_account(
    email: str,
    password: str,
    uned_identifier: str,
    proxy_url: Optional[str] = None,
    max_retries: int = 3
) -> Dict[str, Any]:
    """
    为单个 UNED 账号执行密码激活。
    返回: {"success": bool, "identifier": str, "email": str, "message": str}
    """
    logger.info(f"[{uned_identifier}] 开始激活 UNED 账号 (绑定邮箱: {email})...")
    
    # 1. 连接 Outlook 获取激活邮件
    account_ctx = get_account_context(email)
    if not account_ctx:
        return {"success": False, "identifier": uned_identifier, "email": email, "message": "无法获取 Outlook 账号凭据"}

    if proxy_url:
        http_client = CurlSession(impersonate="chrome120", proxy=proxy_url, timeout=30)
    else:
        http_client = _ms_http()
    activation_url = None
    messages = []
    try:
        token, kind = _ms_access_token(account_ctx, http=http_client)
        try:
            if kind == "graph":
                messages = _fetch_graph_messages(http_client, token)
            else:
                messages = _fetch_outlook_rest_messages(http_client, token)
        except Exception as e_fetch:
            logger.debug(f"[{uned_identifier}] 初始通道 ({kind}) 获取信件异常，尝试 Outlook REST 回退: {e_fetch}")
            try:
                from core.outlook_client import _MS_TOKEN_CACHE
                _MS_TOKEN_CACHE.clear()
                token, kind = _ms_access_token(account_ctx, http=http_client, preferred_kind="outlook")
                messages = _fetch_outlook_rest_messages(http_client, token)
            except Exception as e_fb:
                logger.warning(f"[{uned_identifier}] Outlook REST 自动回退异常: {e_fb}")

        # 1.1 先检查是否已经激活 (如果收件箱已有 Datos de Acceso / Access data 邮件)
        for m in messages:
            subj = m.get("subject", "").lower()
            if "datos de acceso" in subj or "access data" in subj:
                logger.info(f"[{uned_identifier}] 该账号已含有开通信件，确认已处于激活状态！")
                _mark_account_activated(uned_identifier, email, password)
                return {"success": True, "identifier": uned_identifier, "email": email, "message": "该账号已处于激活状态 (已有开通信)"}

        # 1.2 寻找包含 password.aspx 激活链接的信件 (无视邮件语言，只要含 password.aspx 均可匹配)
        for m in messages:
            content = m.get("content", "") or m.get("body", "") or m.get("bodyPreview", "")
            if "password.aspx" in content:
                content_unescaped = html.unescape(content)
                m_url = re.search(r'https://sso\.uned\.es/sso/password\.aspx\?[^\s"\'<>]+', content_unescaped)
                if m_url:
                    activation_url = m_url.group(0)
                    break
    except Exception as e:
        return {"success": False, "identifier": uned_identifier, "email": email, "message": f"获取 Outlook 邮件失败: {e}"}
    finally:
        http_client.close()

    if not activation_url:
        return {"success": False, "identifier": uned_identifier, "email": email, "message": "未在收件箱中找到包含 password.aspx 的激活邮件"}

    logger.info(f"[{uned_identifier}] 成功捕获激活 Token 链接: {activation_url}")

    # 2. 携带代理访问激活页面并提交密码 (优先 ES，重试自动切换 Americas 动态住宅节点)
    import uuid
    last_err = ""
    for attempt in range(1, max_retries + 1):
        if attempt == 1 and not proxy_url:
            sso_proxy = "http://ES:resin_proxy_c3d7b8e1f0a2496582d9183b@127.0.0.1:2260"
        else:
            tag = f"act_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            sso_proxy = proxy_url or f"http://Americas.{tag}:resin_proxy_c3d7b8e1f0a2496582d9183b@127.0.0.1:2260"
        session = CurlSession(impersonate="chrome120", proxy=sso_proxy)
        try:
            r_get = session.get(activation_url, timeout=25)
            if r_get.status_code == 500:
                # Token 可能已消费 (UNED 在 token 失效后返回 500)
                _mark_account_activated(uned_identifier, email, password)
                return {"success": True, "identifier": uned_identifier, "email": email, "message": "Token 已完成激活消费"}

            if r_get.status_code != 200:
                last_err = f"访问激活页面失败 HTTP {r_get.status_code}"
                continue

            page_html = r_get.text
            if "Password change" not in page_html and "Contraseña" not in page_html and "password" not in page_html:
                if "Completed" in page_html or "Correcto" in page_html:
                    _mark_account_activated(uned_identifier, email, password)
                    return {"success": True, "identifier": uned_identifier, "email": email, "message": "账号已处于激活状态"}

            vs_m = re.search(r'id="__VIEWSTATE"\s+value="([^"]+)"', page_html)
            vsg_m = re.search(r'id="__VIEWSTATEGENERATOR"\s+value="([^"]+)"', page_html)
            ev_m = re.search(r'id="__EVENTVALIDATION"\s+value="([^"]+)"', page_html)

            if not vs_m or not vsg_m or not ev_m:
                last_err = "无法解析 ASP.NET ViewState 校验参数"
                continue

            post_data = {
                "__EVENTTARGET": "ctl00$ContentPlaceHolder1$LinkButton1",
                "__EVENTARGUMENT": "",
                "__VIEWSTATE": vs_m.group(1),
                "__VIEWSTATEGENERATOR": vsg_m.group(1),
                "__EVENTVALIDATION": ev_m.group(1),
                "ctl00$ContentPlaceHolder1$h_mode": "URL",
                "ctl00$ContentPlaceHolder1$txt_newpass": password,
                "ctl00$ContentPlaceHolder1$txt_rnewpass": password,
            }

            r_post = session.post(activation_url, data=post_data, timeout=30)
            resp_text = r_post.text

            if "Completed" in resp_text or "Your user ID is" in resp_text or "su identificador" in resp_text.lower() or "Password change" in resp_text:
                logger.info(f"[{uned_identifier}] ★ 密码设置与账号激活成功！")
                _mark_account_activated(uned_identifier, email, password)
                return {"success": True, "identifier": uned_identifier, "email": email, "message": "激活成功"}
            else:
                err_m = re.search(r'class="alert[^"]*">([^<]+)', resp_text)
                err_detail = err_m.group(1).strip() if err_m else "未检测到完成标记"
                last_err = f"激活提交失败: {err_detail}"
        except Exception as e:
            last_err = f"网络异常: {e}"
        finally:
            session.close()
        time.sleep(1.0)

    return {"success": False, "identifier": uned_identifier, "email": email, "message": last_err or "激活重试后仍未成功"}

def _mark_account_activated(uned_identifier: str, email: str, password: str = "") -> None:
    """更新 SQLite 数据库、本地 accounts.json 和 Obsidian 笔记库激活状态"""
    try:
        from core.db.base import _sqlite_conn, _LOCK
        with _LOCK, _sqlite_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT payload_json, doc_number, phone, full_name, created_at FROM uned_accounts WHERE uned_identifier = ?", (uned_identifier,))
            row = cur.fetchone()
            payload = {}
            doc_num = ""
            phone = ""
            full_name = ""
            created_at = ""
            if row:
                if row[0]:
                    try: payload = json.loads(row[0])
                    except Exception: payload = {}
                doc_num = row[1] or payload.get("doc_number", "")
                phone = row[2] or payload.get("phone", "")
                full_name = row[3] or payload.get("full_name", "")
                created_at = row[4] or payload.get("created_at", "")

            extra = payload.get("extra", {})
            extra["activated"] = True
            extra["activated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            if password:
                extra["password"] = password
            payload["extra"] = extra
            payload["status"] = "activated"

            cur.execute(
                "UPDATE uned_accounts SET status = 'activated', payload_json = ? WHERE uned_identifier = ?",
                (json.dumps(payload, ensure_ascii=False), uned_identifier)
            )
            conn.commit()

        # 同步更新 data/uned_accounts.json
        acc_file = Path("data/uned_accounts.json")
        if acc_file.exists():
            try:
                acc_list = json.loads(acc_file.read_text(encoding="utf-8"))
                for acc in acc_list:
                    if acc.get("uned_identifier") == uned_identifier:
                        acc["status"] = "activated"
                        acc_extra = acc.get("extra", {})
                        acc_extra["activated"] = True
                        acc_extra["activated_at"] = extra["activated_at"]
                        acc["extra"] = acc_extra
                acc_file.write_text(json.dumps(acc_list, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                logger.debug(f"更新 uned_accounts.json 异常: {e}")

        # 同步更新 Obsidian 单账号笔记与总表
        try:
            from core.uned.obsidian_sync import sync_account_to_obsidian, update_summary_table
            acc_info = {
                "uned_identifier": uned_identifier,
                "student_email": f"{uned_identifier}@alumno.uned.es",
                "email": email,
                "password": password or extra.get("password", ""),
                "status": "activated",
                "extra": extra,
                "phone": phone,
                "doc_number": doc_num,
                "name": full_name,
                "created_at": created_at
            }
            sync_account_to_obsidian(acc_info)
        except Exception as e:
            logger.debug(f"同步 Obsidian 笔记异常: {e}")

    except Exception as e:
        logger.warning(f"更新 SQLite 激活状态提示: {e}")

def batch_activate_all_accounts(limit: Optional[int] = None, force_recheck: bool = False) -> List[Dict[str, Any]]:
    """全量扫描并激活所有已注册的 UNED 账号"""
    from core.db.base import _sqlite_conn, _LOCK
    with _LOCK, _sqlite_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT uned_identifier, email, payload_json, status FROM uned_accounts ORDER BY id ASC")
        rows = cur.fetchall()

    total = len(rows)
    logger.info(f"开始批量激活 UNED 账号密码，共有 {total} 个账号待检查/激活...")

    results = []
    success_cnt = 0

    for idx, (uid, email, payload_str, status) in enumerate(rows, 1):
        if limit and idx > limit:
            break
            
        # 如果不是正式 SOID (例如最初测试的占位符)，跳过
        if uid.startswith("uned_"):
            print(f"[{idx}/{total}] [跳过临时测试占位] {uid}")
            continue

        # 如果已经是已激活状态且不要求强制重查，直接计入成功并跳过
        if status == "activated" and not force_recheck:
            print(f"[{idx}/{total}] [已激活-无需重复操作] {uid} ({email})")
            success_cnt += 1
            results.append({"success": True, "identifier": uid, "email": email, "message": "已激活"})
            continue

        password = ""
        try:
            payload = json.loads(payload_str)
            password = payload.get("extra", {}).get("password", "")
        except Exception:
            pass

        if not password:
            password = f"Uned@2026!Zhang#88"

        print(f"\n>>> [{idx}/{total}] 正在激活账号: {uid} ({email}) <<<")
        res = activate_single_account(email=email, password=password, uned_identifier=uid)
        results.append(res)

        if res["success"]:
            success_cnt += 1
            print(f"[{idx}/{total}] [成功] {uid} | 说明: {res['message']}")
        else:
            print(f"[{idx}/{total}] [未成] {uid} | 原因: {res['message']}")

        time.sleep(1.5)

    # 批次结束后刷新 Obsidian 汇总总表
    try:
        from core.uned.obsidian_sync import update_summary_table
        update_summary_table()
    except Exception:
        pass

    print("\n==================================================")
    print(f"批量激活任务完毕！总计检查: {len(results)} | 成功: {success_cnt} | 未成: {len(results) - success_cnt}")
    print("==================================================")
    return results
