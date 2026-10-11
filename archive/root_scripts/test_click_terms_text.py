import json
import time
from core.cloakbrowser_driver import build_cloak_driver

driver, _ = build_cloak_driver(proxy='http://127.0.0.1:7890', headless=True)
try:
    # 1. Load stableaudio_cookies.json
    with open('stableaudio_cookies.json', encoding='utf-8') as f:
        cookies = json.load(f)
        
    driver.get('https://stableaudio.com/')
    time.sleep(2)
    driver.page.context.add_cookies(cookies)
    
    # 2. Inject localStorage Auth0 keys
    auth0_key = "@@auth0spajs@@::NrmFAn7R9YEtZAKiXjbH5Gd9KHBkQ10x::https://stableaudio.com::openid profile email offline_access"
    auth0_val = json.dumps({
        "body": {
            "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6IjZ4amxzNTlibnpzbEI3RlB4b3Y3dyJ9.eyJodHRwczovL3N0YWJsZWF1ZGlvLmNvbS9lbWFpbCI6InZlcm9uaWNhY2xhcmsyNzQyNWlpQG91dGxvb2suY29tIiwiaHR0cHM6Ly9zdGFibGVhdWRpby5jb20vZW1haWxfdmVyaWZpZWQiOmZhbHNlLCJpc3MiOiJodHRwczovL3N0YWJsZWF1ZGlvLnVzLmF1dGgwLmNvbS8iLCJzdWIiOiJhdXRoMHw2YWMyMWFhNjQyMWQ0NzZhMTRlNGIwNjQiLCJhdWQiOlsiaHR0cHM6Ly9zdGFibGVhdWRpby5jb20iLCJodHRwczovL3N0YWJsZWF1ZGlvLnVzLmF1dGgwLmNvbS91c2VyaW5mbyJdLCJpYXQiOjE3OTExMDU3NzcsImV4cCI6MTc5MTEyMDE3Nywic2NvcGUiOiJvcGVuaWQgcHJvZmlsZSBlbWFpbCBvZmZsaW5lX2FjY2VzcyIsImF6cCI6Ik5ybUZBbjdSOVlFdFpBS2lYamJINUdkOUtIQmtRMTB4IiwicGVybWlzc2lvbnMiOltdfQ.m-As_vBEEm1szZC9gIRBkSaFHQO5c74CCampvHY79KjmQf1dPHVXgIZMkllcPnInxEwHwCeqI1RJvUpy0FUHpL_9KQiIP9FFG6xZw7RqQ_x_TkRC-Zude3X6IzTNINLoS0tWet7Lh9bFsdWm7X-GPiGf3hXu9oNRK7sM2ogHWBaTenyaB2YNY73Am3wwuu7Ey5W8u9lwuJJA6JyLi8UCo9Yedsx5fr6cZhh3p9vLucM-fpdSK_yn4e2Js1OQPdFYJATdQv2NXfPVTW-Jm-LlADCixTI6tf_r1CTHwxNlI4ER09ls8l0_efvZbbQWyaMxww1aVXAQuwSs6PWN07CMvg",
            "refresh_token": "v1.MXzG7fhBXsX1zZg-gQEiAq0IPcoKx58vA5kDyty-KEX9YZGxu6iY4tKeUFe0NO05B570mEHb23HIC0oHSuVJqkA",
            "scope": "openid profile email offline_access",
            "expires_in": 14400,
            "token_type": "Bearer",
            "audience": "https://stableaudio.com",
            "oauthTokenScope": "openid profile email offline_access",
            "client_id": "NrmFAn7R9YEtZAKiXjbH5Gd9KHBkQ10x"
        },
        "expiresAt": 1791120176
    })
    
    driver.page.evaluate('''(data) => {
        localStorage.setItem(data.k, data.v);
        localStorage.setItem("@@auth0spajs@@::NrmFAn7R9YEtZAKiXjbH5Gd9KHBkQ10x::@@user@@", JSON.stringify({
            "email": "veronicaclark27425ii@outlook.com",
            "email_verified": false,
            "sub": "auth0|6ac21aa6421d476a14e4b064"
        }));
    }''', {"k": auth0_key, "v": auth0_val})
    
    # 3. Navigate to generate or terms
    driver.get("https://stableaudio.com/generate")
    time.sleep(5)
    print("[*] Landed URL:", driver.page.url)
    driver.page.screenshot(path="stableaudio_session_restore.png")
    
    # 4. If terms modal is present, click the text and Agree
    click_res = driver.page.evaluate('''() => {
        // Find text with "I agree to Stability AI's Terms"
        const spans = Array.from(document.querySelectorAll('*')).filter(el => {
            const txt = (el.innerText || '');
            return txt.includes("I agree to Stability AI's Terms") && el.children.length === 0;
        });
        if (spans.length > 0) {
            // Click parent or span
            spans[0].click();
            if (spans[0].parentElement) spans[0].parentElement.click();
        }
        
        // Find Agree button
        const agreeBtn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Agree');
        if (agreeBtn && !agreeBtn.disabled) {
            agreeBtn.click();
            return { clickedAgree: true };
        }
        return { clickedAgree: false, agreeDisabled: agreeBtn ? agreeBtn.disabled : null };
    }''')
    print("[*] Click terms result:", json.dumps(click_res, indent=2))
    time.sleep(3)
    driver.page.screenshot(path="after_click_terms_text.png")
    
    # 5. Check if we reached studio / generate page
    studio_info = driver.page.evaluate('''() => {
        return {
            title: document.title,
            url: window.location.href,
            creditsText: Array.from(document.querySelectorAll('*')).map(el => (el.innerText || '').trim()).find(t => t.includes('Free — 50') || t.includes('50 🎵')),
            promptInput: !!document.querySelector('textarea, input[placeholder*=\"prompt\"], input[placeholder*=\"audio\"]')
        };
    }''')
    print("[*] Studio state:\n", json.dumps(studio_info, indent=2))

finally:
    driver.quit()
