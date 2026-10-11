"""CloakBrowser adapter used by the existing async registration flow.

The reference project launches Cloak's native fingerprint Chromium directly
instead of creating a second Playwright CDP client.  This small handle keeps
the BitBrowser-compatible lifecycle expected by ``common.browser`` while
letting Cloak own the browser context and fingerprint.
"""

from __future__ import annotations

import os
import uuid

from config import (
    CLOAK_EXTRA_ARGS,
    CLOAK_FINGERPRINT_SEED,
    CLOAK_GEOIP,
    CLOAK_HEADLESS,
    CLOAK_HUMANIZE,
    CLOAK_LICENSE_KEY,
    CLOAK_LOCALE,
    CLOAK_TIMEZONE,
    CLOAK_USER_DATA_DIR,
)


def _proxy_url(browser_options: dict | None = None) -> str | None:
    options = dict(browser_options or {})
    if str(options.get("proxyType") or "").lower() == "noproxy":
        return None
    raw = options.get("proxy_str") or options.get("proxyUrl") or options.get("proxy")
    if raw:
        return str(raw).replace("socks5h://", "socks5://")
    try:
        from common import proxy_switch

        value = proxy_switch.effective_proxy_url()
        return str(value or "").replace("socks5h://", "socks5://") or None
    except Exception:
        return None


class CloakBrowserHandle:
    provider_name = "cloak"

    def __init__(self, context, *, profile_id: str, name: str):
        self.context = context
        self.profile_id = str(profile_id)
        self.name = str(name)
        self.browser = getattr(context, "browser", None) or context

    async def close_browser_async(self, profile_id: str | None = None):
        if profile_id and str(profile_id) != self.profile_id:
            return {"success": True}
        try:
            await self.context.close()
        except Exception:
            pass
        return {"success": True}

    def close_browser(self, profile_id: str | None = None):
        # common.browser.teardown prefers the async method for this provider.
        return {"success": True}

    def delete_browser(self, profile_id: str | None = None):
        try:
            from common.browser_registry import unregister

            unregister(profile_id or self.profile_id)
        except Exception:
            pass
        return {"success": True}

    async def launch(self, name: str, browser_options: dict | None = None):
        try:
            from cloakbrowser import launch_context_async, launch_persistent_context_async
        except ImportError as exc:
            raise RuntimeError(
                "未安装 CloakBrowser，请执行：pip install \"cloakbrowser[geoip]>=0.4.10\""
            ) from exc

        args = list(CLOAK_EXTRA_ARGS or [])
        if "--disable-features=BlockThirdPartyCookies" not in args:
            args.append("--disable-features=BlockThirdPartyCookies")
        if CLOAK_FINGERPRINT_SEED:
            args.append(f"--fingerprint={CLOAK_FINGERPRINT_SEED.strip()}")
        proxy = _proxy_url(browser_options)

        headless_val = browser_options.get("headless") if browser_options and "headless" in browser_options else None
        if headless_val is None:
            env_h = os.environ.get("OUTLOOK_HEADLESS", "").strip()
            if env_h in ("0", "false", "False"):
                headless_val = False
            elif env_h in ("1", "true", "True"):
                headless_val = True
            else:
                headless_val = bool(CLOAK_HEADLESS)

        options = {
            "headless": bool(headless_val),
            "humanize": bool(CLOAK_HUMANIZE),
            "geoip": bool(CLOAK_GEOIP),
        }
        if proxy:
            options["proxy"] = proxy
        if args:
            options["args"] = args
        if CLOAK_LOCALE.strip():
            options["locale"] = CLOAK_LOCALE.strip()
        if CLOAK_TIMEZONE.strip():
            options["timezone"] = CLOAK_TIMEZONE.strip()
        if CLOAK_LICENSE_KEY.strip():
            options["license_key"] = CLOAK_LICENSE_KEY.strip()

        user_data_dir = CLOAK_USER_DATA_DIR.strip()
        try:
            if user_data_dir:
                context = await launch_persistent_context_async(user_data_dir, **options)
            else:
                context = await launch_context_async(**options)
        except ImportError as exc:
            # geoip2 is optional. Cloak's native fingerprint remains usable
            # without automatic language/timezone lookup.
            if not options.get("geoip") or "geoip" not in str(exc).lower():
                raise
            options["geoip"] = False
            print("  CloakBrowser geoip2 unavailable; continuing with native fingerprint")
            if user_data_dir:
                context = await launch_persistent_context_async(user_data_dir, **options)
            else:
                context = await launch_context_async(**options)
        try:
            await context.add_init_script("""
                (() => {
                    // 1. Storage sandbox fallback for cross-origin iframes
                    function patchStorage(type) {
                        try {
                            const s = window[type];
                            if (s) { s.getItem('__probe__'); return; }
                        } catch (e) {}
                        const mem = new Map();
                        const shim = {
                            getItem: (k) => mem.has(String(k)) ? mem.get(String(k)) : null,
                            setItem: (k, v) => mem.set(String(k), String(v)),
                            removeItem: (k) => mem.delete(String(k)),
                            clear: () => mem.clear(),
                            key: (i) => Array.from(mem.keys())[i] ?? null,
                            get length() { return mem.size; }
                        };
                        try {
                            Object.defineProperty(window, type, {
                                get: () => shim,
                                set: () => {},
                                configurable: true,
                                enumerable: true
                            });
                        } catch (_) {}
                    }
                    patchStorage('sessionStorage');
                    patchStorage('localStorage');

                    // 2. userAgentData polyfill (matches HTTP Sec-CH-UA headers)
                    if (!navigator.userAgentData) {
                        const brands = [
                            { brand: "Chromium", version: "146" },
                            { brand: "Not-A.Brand", version: "24" },
                            { brand: "Google Chrome", version: "146" }
                        ];
                        const uad = {
                            brands: brands,
                            mobile: false,
                            platform: "Windows",
                            getHighEntropyValues: async (hints) => {
                                const values = {
                                    architecture: "x86",
                                    bitness: "64",
                                    brands: brands,
                                    fullVersionList: [
                                        { brand: "Chromium", version: "146.0.0.0" },
                                        { brand: "Not-A.Brand", version: "24.0.0.0" },
                                        { brand: "Google Chrome", version: "146.0.0.0" }
                                    ],
                                    mobile: false,
                                    model: "",
                                    platform: "Windows",
                                    platformVersion: "15.0.0",
                                    uaFullVersion: "146.0.0.0"
                                };
                                const res = {};
                                for (const h of hints || []) {
                                    if (h in values) res[h] = values[h];
                                }
                                return res;
                            },
                            toJSON: function() {
                                return { brands: this.brands, mobile: this.mobile, platform: this.platform };
                            }
                        };
                        try {
                            Object.defineProperty(Object.getPrototypeOf(navigator), 'userAgentData', {
                                get: () => uad,
                                configurable: true,
                                enumerable: true
                            });
                        } catch (_) {
                            try {
                                Object.defineProperty(navigator, 'userAgentData', {
                                    get: () => uad,
                                    configurable: true,
                                    enumerable: true
                                });
                            } catch (_) {}
                        }
                    }

                    // 3. deviceMemory polyfill (genuine desktop standard)
                    if (!navigator.deviceMemory) {
                        try {
                            Object.defineProperty(Object.getPrototypeOf(navigator), 'deviceMemory', {
                                get: () => 8,
                                configurable: true,
                                enumerable: true
                            });
                        } catch (_) {
                            try {
                                Object.defineProperty(navigator, 'deviceMemory', {
                                    get: () => 8,
                                    configurable: true,
                                    enumerable: true
                                });
                            } catch (_) {}
                        }
                    }

                    // 4. Notification.permission ('default' instead of headless 'denied')
                    if (window.Notification) {
                        try {
                            Object.defineProperty(Notification, 'permission', {
                                get: () => 'default',
                                configurable: true,
                                enumerable: true
                            });
                        } catch (_) {}
                    }

                    // 5. window.chrome.runtime
                    if (window.chrome && !window.chrome.runtime) {
                        window.chrome.runtime = {
                            OnInstalledReason: { CHROME_UPDATE: "chrome_update", INSTALL: "install", SHARED_MODULE_UPDATE: "shared_module_update", UPDATE: "update" },
                            OnRestartRequiredReason: { APP_UPDATE: "app_update", OS_UPDATE: "os_update", PERIODIC: "periodic" },
                            PlatformArch: { ARM: "arm", ARM64: "arm64", MIPS: "mips", MIPS64: "mips64", X86_32: "x86-32", X86_64: "x86-64" },
                            PlatformNaclArch: { ARM: "arm", MIPS: "mips", MIPS64: "mips64", X86_32: "x86-32", X86_64: "x86-64" },
                            PlatformOs: { ANDROID: "android", CROS: "cros", LINUX: "linux", MAC: "mac", OPENBSD: "openbsd", WIN: "win" },
                            RequestUpdateCheckStatus: { NO_UPDATE: "no_update", THROTTLED: "throttled", UPDATE_AVAILABLE: "update_available" },
                            connect: function() {},
                            sendMessage: function() {}
                        };
                    }
                })();
            """)
        except Exception:
            pass

        profile_id = f"cloak-{uuid.uuid4().hex}"
        handle = CloakBrowserHandle(context, profile_id=profile_id, name=name)
        try:
            from common.browser_registry import register

            register(profile_id, name=name, provider="cloak")
        except Exception:
            pass

        try:
            from core.live_browser_hub import LiveBrowserHub
            is_headed = not bool(options.get("headless"))
            if is_headed:
                LiveBrowserHub.open_browser(8799)
            def _on_page(p):
                try:
                    LiveBrowserHub.get_hub().attach_page(p, auto_open=False)
                except Exception:
                    pass
            context.on("page", _on_page)
            if context.pages:
                LiveBrowserHub.get_hub().attach_page(context.pages[0], auto_open=False)
        except Exception:
            pass

        print(f"  CloakBrowser profile created: {name} ({profile_id})")
        return handle, profile_id, context
