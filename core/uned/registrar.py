"""
UNED (Universidad Nacional de Educación a Distancia) 独立注册核心组件
提供单账号注册、批量无缝挂机注册、自动验证码识别与全流程落盘
完全独立解耦，不与 ChatGPT 或 Grok 逻辑混杂。
"""

import json
import logging
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from playwright.sync_api import sync_playwright

logger = logging.getLogger("uned.registrar")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_ACCOUNTS_JSON = _DATA_DIR / "uned_accounts.json"
_SUCCESS_TXT = _PROJECT_ROOT / "注册成功的UNED账号.txt"

@dataclass
class UnedRegistrationResult:
    success: bool
    uned_identifier: str = ""
    student_email: str = ""
    email: str = ""
    password: str = ""
    phone: str = ""
    doc_number: str = ""
    full_name: str = ""
    error: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "uned_identifier": self.uned_identifier,
            "student_email": self.student_email,
            "email": self.email,
            "password": self.password,
            "phone": self.phone,
            "doc_number": self.doc_number,
            "full_name": self.full_name,
            "error": self.error,
            "created_at": self.created_at
        }


class UnedRegistrar:
    """UNED 独立自动化注册器"""

    def __init__(self, debug: bool = True):
        self.debug = debug
        self._ensure_output_dirs()

    def _ensure_output_dirs(self):
        _ACCOUNTS_JSON.parent.mkdir(parents=True, exist_ok=True)
        (_PROJECT_ROOT / "scratch").mkdir(parents=True, exist_ok=True)

    def _log(self, msg: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [UNED] {msg}"
        print(line, flush=True)
        logger.info(msg)

    def _generate_phone(self) -> str:
        prefix = random.choice(["138", "139", "150", "158", "188", "186", "133", "189"])
        suffix = "".join([str(random.randint(0, 9)) for _ in range(8)])
        return f"+86{prefix}{suffix}"

    def _generate_password(self) -> str:
        digits = "".join([str(random.randint(0, 9)) for _ in range(4)])
        return f"Uned@{digits}!Zhang#88"

    def register_one(
        self,
        headless: bool = True,
        doc_info: Optional[dict] = None,
        proxy_url: Optional[str] = None,
        max_captcha_retries: int = 6
    ) -> UnedRegistrationResult:
        """
        执行单次完整 UNED 账号注册。
        """
        from config import proxy as proxy_cfg_mod
        from core.db.email_pool import claim_next_outlook, release_unconsumed_outlook
        import core.outlook_client as oc
        from core.uned.captcha_solver import solve_uned_captcha
        from core.uned.identities import pick_identity, mark_failed_identity
        import core.db as db

        # 禁用失效的远端中转，强制使用直连 Microsoft Graph API
        oc._REMOTE_DISABLED = True

        # 1. 代理准备：生成独立会话 ID，保证每次注册出口 IP 绝对独立唯一
        import uuid, time
        if not proxy_url:
            session_tag = f"reg_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            raw_proxy = f"http://Americas.{session_tag}:resin_proxy_c3d7b8e1f0a2496582d9183b@127.0.0.1:2260"
        else:
            raw_proxy = proxy_url

        # 实测探测本次注册的实际独立出口 IP
        exit_ip = "未知"
        try:
            from curl_cffi import requests as c_requests
            r_ip = c_requests.get("https://api.ipify.org?format=json", proxy=raw_proxy, timeout=8)
            exit_ip = r_ip.json().get("ip", "未知")
            self._log(f"★ 本次注册出口 IP 已确认: {exit_ip} (代理会话: {raw_proxy.split('@')[0]})")
        except Exception as e_ip:
            self._log(f"使用代理: {raw_proxy} (出口 IP 探测提示: {e_ip})")

        playwright_proxy = None
        if raw_proxy and "://" in raw_proxy:
            m = re.match(r'(https?|socks5)://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)', raw_proxy)
            if m:
                scheme, user, pwd, host, port = m.groups()
                playwright_proxy = {"server": f"{scheme}://{host}:{port}"}
                if user and pwd:
                    playwright_proxy["username"] = user
                    playwright_proxy["password"] = pwd

        # 2. 领取 Outlook 邮箱
        self._log("正在从 Outlook 邮箱池领取账号...")
        claimed_acc = claim_next_outlook(for_service="uned")
        if not claimed_acc:
            raise RuntimeError("Outlook 邮箱池已无可用账号，请先导入 Outlook 账号")
        
        email = claimed_acc["email"]
        phone_number = self._generate_phone()
        account_password = self._generate_password()
        self._log(f"领用 Outlook 邮箱: {email} | 随机手机: {phone_number}")

        # 3. 身份信息准备
        if not doc_info:
            # 查已注册身份证防重
            used_ids = set()
            try:
                for row in db.list_uned_accounts():
                    if row.get("doc_number"):
                        used_ids.add(row["doc_number"])
            except Exception:
                pass
            doc_info = pick_identity(used_doc_numbers=used_ids)

        self._log(f"使用身份: {doc_info['name']} {doc_info['surname1']} (证件: {doc_info['doc_number']})")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=headless,
                proxy=playwright_proxy,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                locale="es-ES",
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            try:
                # ==========================================
                # 步骤 1: 首页协议与验证码
                # ==========================================
                self._log("正在访问 https://app.uned.es/soid ...")
                page.goto("https://app.uned.es/soid", wait_until="networkidle", timeout=60000)

                passed_captcha = False
                attempt = 0
                max_attempts = 25
                while attempt < max_attempts:
                    attempt += 1
                    self._log(f"--- 验证码检测 第 {attempt}/{max_attempts} 次 ---")
                    
                    chk_alumno = page.locator("#chkAlumno")
                    if chk_alumno.count() > 0 and not chk_alumno.is_checked():
                        chk_alumno.check()
                    chk_prot = page.locator("#chkAceptarProteccionDatos")
                    if chk_prot.count() > 0 and not chk_prot.is_checked():
                        chk_prot.check()

                    # 截取验证码图片并识别
                    captcha_el = page.locator(".img-captcha")
                    captcha_bytes = captcha_el.screenshot()
                    captcha_val = solve_uned_captcha(captcha_bytes)
                    self._log(f"RapidOCR 自动识别验证码结果: [{captcha_val}]")

                    if not captcha_val or len(captcha_val) != 5:
                        self._log("未获得高置信度 5 位字符，刷新更换验证码...")
                        if attempt % 3 == 0:
                            self._log("连续未识别到 5 位字符，重新加载页面重置会话...")
                            try:
                                page.reload(wait_until="networkidle", timeout=30000)
                            except Exception:
                                pass
                            time.sleep(2.0)
                        else:
                            refresh_btn = page.locator("#btnRefresh")
                            if refresh_btn.count() > 0:
                                refresh_btn.click()
                                time.sleep(2.0)
                        continue

                    page.fill("#txtCodigoCaptcha", captcha_val)
                    page.click("#btnIniciarProceso")
                    time.sleep(3.5)

                    if "email-registro" in page.url or page.locator("#txtEmail").count() > 0:
                        self._log("验证码校验通过，进入邮箱验证阶段！")
                        passed_captcha = True
                        break
                    else:
                        self._log("验证码校验未通过，刷新重试...")
                        if attempt % 3 == 0:
                            self._log("多次校验未过，重新加载页面重置会话...")
                            try:
                                page.reload(wait_until="networkidle", timeout=30000)
                            except Exception:
                                pass
                            time.sleep(2.0)
                        else:
                            refresh_btn = page.locator("#btnRefresh")
                            if refresh_btn.count() > 0:
                                refresh_btn.click()
                                time.sleep(2.0)

                if not passed_captcha:
                    raise RuntimeError("多次验证码识别均未能通过 UNED 首页校验")

                # ==========================================
                # 步骤 2: 填写邮箱并自动接码 (Graph API)
                # ==========================================
                self._log(f"填写 Outlook 邮箱: {email}")
                email_input = page.locator("#txtEmail")
                email_input.wait_for(state="visible", timeout=25000)
                email_input.fill(email)
                email_submit_ts = time.time()

                self._log("提交邮箱，等待发送验证码...")
                page.click("#btnSiguiente")
                time.sleep(3)

                self._log(f"通过 Microsoft Graph 直连轮询 {email} 的 UNED 邮件验证码...")
                email_otp = oc.fetch_latest_otp(email, after_ts=email_submit_ts, max_wait=90)
                self._log(f"成功获取邮箱验证码: {email_otp}")

                otp_input = page.locator("#txtCodigoVerificacionEmail, input[name*='Codigo'], input[id*='Codigo']").first
                otp_input.wait_for(state="visible", timeout=20000)
                otp_input.fill(email_otp)
                self._log("提交邮箱验证码，进入手机号阶段...")
                page.click("#btnSiguiente")

                # ==========================================
                # 步骤 3: 手机号步骤
                # ==========================================
                phone_input = page.locator("#txtMovil")
                phone_input.wait_for(state="visible", timeout=25000)
                self._log(f"填入手机号: {phone_number}")
                phone_input.fill(phone_number)
                page.click("#btnSiguiente")

                # ==========================================
                # 步骤 4: 证件类型与号码
                # ==========================================
                self._log("等待进入证件选择页面...")
                doc_select = page.locator("#ddlTipoDocumento")
                doc_select.wait_for(state="visible", timeout=30000)

                self._log(f"选择证件类型: {doc_info['doc_type_val']} (其他官方证件)")
                doc_select.select_option(value=doc_info["doc_type_val"])
                time.sleep(0.5)

                country_select = page.locator("#ddlPais")
                country_select.wait_for(state="visible", timeout=10000)
                self._log(f"选择发证国: {doc_info['country_val']} (China)")
                country_select.select_option(value=doc_info["country_val"])
                time.sleep(0.5)

                self._log(f"填写证件号码: {doc_info['doc_number']}")
                page.fill("#txtNumeroDocumento", doc_info["doc_number"])
                page.click("#btnSiguiente")

                # ==========================================
                # 步骤 5: 个人详细资料录入
                # ==========================================
                self._log("等待进入个人详细资料录入页面...")
                try:
                    page.locator("#txtNombre").wait_for(state="visible", timeout=20000)
                except Exception as e:
                    err_msg = ""
                    for sel in [".alert-danger", "#divMensajeError", ".text-danger", ".modal-body", ".alert", "#lblError", ".has-error"]:
                        try:
                            el = page.locator(sel)
                            if el.count() > 0 and el.first.is_visible():
                                txt = el.first.inner_text().strip()
                                if txt:
                                    err_msg = txt
                                    break
                        except Exception:
                            pass
                    try:
                        err_path = _PROJECT_ROOT / f"scratch/err_doc_{doc_info['doc_number']}.png"
                        page.screenshot(path=str(err_path))
                        self._log(f"已保存异常截图至 {err_path}")
                    except Exception:
                        pass
                    mark_failed_identity(doc_info["doc_number"], err_msg or "证件提交未能进入个人资料页")
                    if err_msg:
                        raise RuntimeError(f"证件 {doc_info['doc_number']} 被 UNED 拒绝: {err_msg}")
                    raise RuntimeError(f"证件 {doc_info['doc_number']} 提交超时未能进入资料录入页")

                # 姓名
                page.fill("#txtNombre", doc_info["name"])
                page.fill("#txtPrimerApellido", doc_info["surname1"])
                page.fill("#txtSegundoApellido", doc_info["surname2"])

                # 出生日期 (突破 readonly 限制)
                self._log(f"设置出生日期: {doc_info['dob']}")
                page.evaluate("""(dob) => {
                    const el = document.getElementById('txtFechaNacimiento');
                    if (el) {
                        el.removeAttribute('readonly');
                        el.value = dob;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        el.dispatchEvent(new Event('blur', { bubbles: true }));
                    }
                }""", doc_info["dob"])
                time.sleep(0.5)

                # 性别
                if doc_info.get("gender") == "F":
                    rb_f = page.locator("#rbSexoF")
                    if rb_f.count() > 0: rb_f.check()
                else:
                    rb_m = page.locator("#rbSexoM")
                    if rb_m.count() > 0: rb_m.check()

                # 国籍与出生地
                page.locator("#ddlPaisNacionalidad").select_option(value="RPC")
                page.locator("#ddlPaisNacimiento").select_option(value="RPC")
                page.evaluate("() => { if (window.$) { $('#ddlPaisNacimiento').trigger('change'); } }")
                page.fill("#txtLocalidadNacimiento", "Xianyang")

                # 居住地与地址
                page.locator("#ddlPaisResidencia").select_option(value="RPC")
                page.evaluate("() => { if (window.$) { $('#ddlPaisResidencia').trigger('change'); } }")
                time.sleep(0.5)
                page.fill("#txtPoblacion", "Xianyang")

                # 外国省份 (解除 disabled 状态)
                page.evaluate("""() => {
                    const el = document.getElementById('txtProvinciaExtranjera');
                    if (el) {
                        el.disabled = false;
                        el.value = 'Shaanxi';
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }""")

                page.locator("#ddlTipoVia").select_option(value="CL")
                page.fill("#txtNombreVia", "No. 88 Chang'an Street")

                # 拒绝西班牙教育部数据核对
                rb_no = page.locator("#rbContrastarDatosMinisterioNO")
                if rb_no.count() > 0: rb_no.check()

                self._log("个人资料填写完毕，提交保存...")
                page.click("#btnSiguiente")
                time.sleep(1.5)

                # 检查是否有提示弹窗 (例如住址警告弹窗)
                try:
                    modal_btn = page.locator("#btnDomicilioWarningAceptar, .modal.in button, .modal.show button")
                    if modal_btn.count() > 0 and modal_btn.first.is_visible():
                        self._log("检测到住址提示弹窗，确认接受...")
                        modal_btn.first.click()
                        time.sleep(1)
                except Exception:
                    pass
                
                # 等待提交离开 datos-personales 页面
                self._log("等待进入个人信息确认页 (/soid/confirmacion-datos-personales)...")
                try:
                    page.wait_for_url(lambda u: "confirmacion" in u or "identificador" in u, timeout=15000)
                except Exception:
                    # 如果超时仍在 datos-personales，且表单存在，强制执行表单提交
                    if "datos-personales" in page.url and "confirmacion" not in page.url:
                        self._log("页面未自动跳转，尝试强制触发表单提交...")
                        page.evaluate("""() => {
                            if (window.$ && $('#frmDatosPersonales').length) {
                                $('#frmDatosPersonales').submit();
                            } else {
                                const f = document.getElementById('frmDatosPersonales');
                                if (f) f.submit();
                            }
                        }""")
                        try:
                            page.wait_for_url(lambda u: "confirmacion" in u or "identificador" in u, timeout=20000)
                        except Exception:
                            time.sleep(5)

                # ==========================================
                # 步骤 6: 确认页与标识符获取
                # ==========================================
                self._log(f"当前阶段 URL: {page.url}")
                page_content = page.content()

                # 如果当前是在确认汇总页 (/soid/confirmacion-datos-personales)，需要点击最终确认按钮
                if "confirmacion" in page.url or ("Su identificador" not in page_content and "identificador de usuario es" not in page_content):
                    for sel in ["#btnContinuar", "#btnConfirmar", "#btnFinalizar", "button:has-text('Continuar')", "button:has-text('Finalizar')", "#btnSiguiente", "button[type='submit']"]:
                        btn = page.locator(sel)
                        if btn.count() > 0 and btn.first.is_visible():
                            self._log(f"点击最终确认完成按钮 ({sel})...")
                            btn.first.click()
                            try:
                                page.wait_for_load_state("networkidle", timeout=15000)
                            except Exception:
                                pass
                            time.sleep(5)
                            page_content = page.content()
                            break

                # 等待标识符元素出现
                try:
                    page.locator(".span-id-usuario, a[href*='@alumno.uned.es']").first.wait_for(state="visible", timeout=20000)
                except Exception:
                    pass

                # 保存完成页面截图备查
                try:
                    shot_path = _DATA_DIR / f"uned_result_{int(time.time())}.png"
                    page.screenshot(path=str(shot_path))
                    self._log(f"已保存结果页面截图至 {shot_path.name}")
                except Exception:
                    pass

                # 提取 UNED 标识符 (如: lzhang47)
                identifier = ""
                # 1. 优先从专用 class 提取
                span_el = page.locator(".span-id-usuario")
                if span_el.count() > 0:
                    cand = span_el.first.inner_text().strip()
                    if cand and len(cand) >= 3 and cand.lower() != "uned":
                        identifier = cand

                # 2. 从学生邮箱 mailto 提取
                if not identifier:
                    mail_el = page.locator("a[href*='@alumno.uned.es']")
                    if mail_el.count() > 0:
                        href = mail_el.first.get_attribute("href") or ""
                        m_mail = re.search(r'mailto:([a-zA-Z0-9_\-\.]+)@alumno\.uned\.es', href, re.IGNORECASE)
                        if m_mail:
                            identifier = m_mail.group(1).strip()
                        else:
                            txt = mail_el.first.inner_text().strip()
                            m_mail2 = re.search(r'([a-zA-Z0-9_\-\.]+)@alumno\.uned\.es', txt, re.IGNORECASE)
                            if m_mail2:
                                identifier = m_mail2.group(1).strip()

                # 3. 从页面正文 inner_text 提取 (排除 HTML 标签干扰)
                if not identifier:
                    try:
                        body_text = page.inner_text("body")
                        m_body = re.search(r'identificador de usuario es:\s*([a-zA-Z0-9_\-\.]+)', body_text, re.IGNORECASE)
                        if m_body:
                            val = m_body.group(1).strip()
                            if val.lower() != "uned" and len(val) >= 3:
                                identifier = val
                    except Exception:
                        pass

                # 4. 从 HTML 源码备用匹配
                if not identifier:
                    m_html = re.search(r'class="span-id-usuario">([^<]+)</span>', page_content, re.IGNORECASE)
                    if m_html:
                        identifier = m_html.group(1).strip()

                if not identifier:
                    m_alt = re.search(r'([a-z]{1,4}\d{1,5})@alumno\.uned\.es', page_content, re.IGNORECASE)
                    if m_alt:
                        identifier = m_alt.group(1).strip()

                if not identifier:
                    identifier = f"uned_{int(time.time())}"
                    self._log(f"未在页面直接解析到标识符，生成唯一回退 ID: {identifier}")
                else:
                    self._log(f"★ 成功获得 UNED 官方标识符: {identifier}")

                student_email = f"{identifier}@alumno.uned.es"
                now_iso = datetime.now().isoformat()

                # ==========================================
                # 步骤 7: 数据多重持久化落盘
                # ==========================================
                # 1. 写入 SQLite
                try:
                    db.insert_uned_account(
                        uned_identifier=identifier,
                        email=email,
                        phone=phone_number,
                        doc_type="OTROS DOCUMENTOS",
                        doc_number=doc_info["doc_number"],
                        full_name=f"{doc_info['name']} {doc_info['surname1']}",
                        birth_date=doc_info["dob"],
                        nationality=doc_info["nationality"],
                        status="registered",
                        proxy_used=f"{exit_ip} | {raw_proxy}",
                        extra={
                            "student_email": student_email,
                            "password": account_password,
                            "finish_time": now_iso
                        }
                    )
                except Exception as e:
                    self._log(f"写入 SQLite 提示: {e}")

                # 2. 写入 JSON
                try:
                    existing = []
                    if _ACCOUNTS_JSON.exists():
                        try:
                            existing = json.loads(_ACCOUNTS_JSON.read_text(encoding="utf-8"))
                        except Exception:
                            existing = []
                    rec = {
                        "uned_identifier": identifier,
                        "student_email": student_email,
                        "email": email,
                        "password": account_password,
                        "phone": phone_number,
                        "name": f"{doc_info['name']} {doc_info['surname1']}",
                        "doc_number": doc_info["doc_number"],
                        "created_at": now_iso
                    }
                    existing.append(rec)
                    _ACCOUNTS_JSON.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception as e:
                    self._log(f"写入 JSON 提示: {e}")

                # 3. 写入 TXT 文本
                try:
                    with open(_SUCCESS_TXT, "a", encoding="utf-8") as f:
                        f.write(f"标识符: {identifier} | 学生邮箱: {student_email} | 绑定邮箱: {email} | 密码: {account_password} | 手机: {phone_number} | 证件号: {doc_info['doc_number']} | 时间: {now_iso}\n")
                except Exception as e:
                    self._log(f"写入 TXT 提示: {e}")

                # 4. 同步至 Obsidian 知识库 (学校邮箱目录)
                try:
                    from core.uned.obsidian_sync import sync_account_to_obsidian
                    sync_account_to_obsidian(rec)
                    self._log("已实时同步账号档案与总表至 Obsidian 学校邮箱目录！")
                except Exception as e:
                    self._log(f"同步 Obsidian 提示: {e}")

                result = UnedRegistrationResult(
                    success=True,
                    uned_identifier=identifier,
                    student_email=student_email,
                    email=email,
                    password=account_password,
                    phone=phone_number,
                    doc_number=doc_info["doc_number"],
                    full_name=f"{doc_info['name']} {doc_info['surname1']}",
                    created_at=now_iso
                )
                self._log(f"单账号注册全部完成！标识符={identifier}, 学生邮箱={student_email}")
                return result

            except Exception as e:
                self._log(f"注册过程出现异常: {e}")
                try:
                    from core.db.email_pool import release_unconsumed_outlook
                    release_unconsumed_outlook(email, note=f"UNED 注册异常自动回收: {e}")
                    self._log(f"已自动释放未完成注册的 Outlook 邮箱: {email}")
                except Exception:
                    pass
                return UnedRegistrationResult(
                    success=False,
                    email=email,
                    phone=phone_number,
                    doc_number=doc_info.get("doc_number", "") if doc_info else "",
                    error=str(e),
                    created_at=datetime.now().isoformat()
                )
            finally:
                context.close()
                browser.close()

    def batch_register(
        self,
        count: int = 5,
        headless: bool = True,
        interval_seconds: int = 5
    ) -> List[UnedRegistrationResult]:
        """
        无缝批量挂机注册。
        """
        self._log(f"==================================================")
        self._log(f"开始批量注册 UNED 账号，目标数量: {count} 个 (Headless={headless})")
        self._log(f"==================================================")
        
        # 初始化已使用与尝试过的身份证集合
        session_tried_docs = set()
        try:
            import core.db as db
            for row in db.list_uned_accounts():
                if row.get("doc_number"):
                    session_tried_docs.add(row["doc_number"])
        except Exception:
            pass

        from core.uned.identities import pick_identity

        results = []
        success_count = 0

        for i in range(1, count + 1):
            self._log(f"\n>>> 正在注册第 {i}/{count} 个 UNED 账号 <<<")
            try:
                doc_to_use = pick_identity(used_doc_numbers=session_tried_docs)
            except RuntimeError as e:
                self._log(f"无可继续注册的有效身份证: {e}")
                break

            session_tried_docs.add(doc_to_use["doc_number"])
            res = self.register_one(headless=headless, doc_info=doc_to_use)
            results.append(res)
            
            if res.success:
                success_count += 1
                self._log(f"第 {i} 个注册成功！[ID: {res.uned_identifier}] 当前成功率: {success_count}/{i}")
            else:
                self._log(f"第 {i} 个注册失败: {res.error}")

            if i < count:
                self._log(f"等待 {interval_seconds} 秒后进行下一个注册...")
                time.sleep(interval_seconds)

        self._log(f"==================================================")
        self._log(f"批量挂机注册任务结束！总计请求: {len(results)}，成功: {success_count}，失败: {len(results) - success_count}")
        self._log(f"==================================================")
        return results
