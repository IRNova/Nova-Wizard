<div align="center">
  <img src="https://raw.githubusercontent.com/iiviirv/irnova-site/main/brand/nova-logo-gradient.svg" width="70" alt="Nova">
  <h1>Nova Wizard</h1>
  <p><b>Local OAuth deployer for Nova Proxy on Cloudflare Workers</b></p>
  <p>یک دیپلوی‌ر محلی با OAuth برای نوا پروکسی روی Cloudflare Workers</p>
  <p>
    <a href="https://novaproxy.online/">Website</a> ·
    <a href="https://t.me/irnova_proxy">Telegram Channel</a> ·
    <a href="https://t.me/irnovaproxy_group">Telegram Group</a> ·
    <a href="https://www.youtube.com/@novaproxyir">YouTube</a> ·
    <a href="https://x.com/irNovaProxy">X / Twitter</a>
  </p>
</div>

---

## What is Nova Wizard?

Nova Wizard is a **local** Windows tool that deploys the [Nova Proxy](https://github.com/IRNova/Nova-Proxy) worker to your Cloudflare account using **OAuth** — no API tokens, no API keys, no third-party servers. Everything runs on your own machine.

**نوا ویزارد** یک ابزار محلی ویندوزی است که ورکر نوا پروکسی را با استفاده از OAuth روی حساب Cloudflare شما دیپلوی می‌کند — بدون نیاز به توکن API، بدون سرور شخص ثالث. همه چیز روی کامپیوتر خودتان اجرا می‌شود.

---

## Download / دانلود

[**⬇ Download NovaWizard.exe**](https://github.com/IRNova/Nova-Wizard/releases/download/V1.0.0/NovaWizard.exe) (8.4 MB) — Windows 64-bit, portable

[**⬇ دانلود NovaWizard.exe**](https://github.com/IRNova/Nova-Wizard/releases/download/V1.0.0/NovaWizard.exe) (۸.۴ مگابایت) — ویندوز ۶۴ بیت، قابل حمل

---

## Features

- **OAuth login** — Cloudflare authorization in one click, no token to create or manage
- **Auto-setup** — Worker, KV namespace, and D1 database are created automatically
- **Custom names** — You can rename the worker, KV, and D1 before deploying
- **Private & local** — Your Cloudflare credentials never leave your machine
- **Bilingual** — English and Persian (فارسی) UI
- **Single EXE** — No Python or dependencies needed to run

---

## How to Use

<details>
<summary><b>English</b></summary>

1. **Run** `NovaWizard.exe` — a terminal window opens with a local URL.
2. **Open your browser** — it opens automatically at `http://127.0.0.1:8000/?token=...`
3. **Click "Login with Cloudflare"** — Cloudflare opens in a new tab.
4. **Authorize on Cloudflare** — approve the access request, then close the tab.
5. **Choose your account** — select which Cloudflare account to deploy on.
6. **Name your resources** — worker name, KV namespace, D1 database are pre-filled. You can change them.
7. **Click "Deploy now"** — the worker is downloaded from GitHub, uploaded to Cloudflare, KV + D1 are created and bound.
8. **Done!** — open your panel, set your own admin password on first visit.

> You can rename the worker, KV namespace, and D1 database **before** deploying. The names are random by default but fully editable.

</details>

<details>
<summary><b>فارسی</b></summary>

۱. **اجرا کنید** `NovaWizard.exe` را — یک پنجره ترمینال با لینک محلی باز می‌شود.
۲. **مرورگر را باز کنید** — به‌طور خودکار در آدرس `http://127.0.0.1:8000/?token=...` باز می‌شود.
۳. **روی "ورود با Cloudflare" کلیک کنید** — Cloudflare در یک تب جدید باز می‌شود.
۴. **اجازه دهید** — درخواست دسترسی را تأیید کنید، سپس تب را ببندید.
۵. **حساب خود را انتخاب کنید** — انتخاب کنید روی کدام حساب Cloudflare دیپلوی شود.
۶. **نام منابع را تنظیم کنید** — نام ورکر، فضای KV و دیتابیس D1 از پیش پر شده‌اند. می‌توانید تغییرشان دهید.
۷. **روی "الان دیپلوی کن" کلیک کنید** — ورکر از گیت‌هاب دانلود، روی Cloudflare آپلود، KV و D1 ساخته و متصل می‌شوند.
۸. **تمام!** — پنل خود را باز کنید و در اولین بازدید رمز عبور خود را تنظیم کنید.

> می‌توانید نام ورکر، فضای KV و دیتابیس D1 را **قبل از** دیپلوی تغییر دهید. نام‌ها به‌صورت تصادفی پر می‌شوند اما کاملاً قابل ویرایش هستند.

</details>

---

## Screenshots

*(Coming soon)*

---

## Mobile Versions

| Platform | Status | Details |
|----------|--------|---------|
| **Android** | 🚀 Coming soon | Merged with <a href="https://github.com/IRNova/Nova-Radar">Nova Radar</a> Android — a single app for deployment + proxy |
| **iOS** | 🔧 In development | Standalone iOS app, currently under development |

---

## Requirements / نیازمندی‌ها

- **Windows** 7 / 8 / 10 / 11 (64-bit) — از EXE آماده استفاده کن
- **Linux** — Python 3.8+ و اجرای مستقیم (ببین پایین)
- A Cloudflare account / حساب Cloudflare
- Internet connection / اتصال به اینترنت

---

## Linux / لینوکس

Nova Wizard runs natively on Linux — no EXE needed.

نوا ویزارد روی لینوکس بدون نیاز به EXE اجرا می‌شود.

### Quick start / اجرای سریع

```bash
# 1. Clone the repo
git clone https://github.com/IRNova/Nova-Wizard.git
cd Nova-Wizard

# 2. Make sure static/index.html is present (from your build machine)
#    The static/ folder is required but not in the public repo.

# 3. Run directly with Python
python3 nova_wizard.py
```

### Requirements / پیش‌نیازها

- Python 3.8+ (built-in on most distros)
- `static/index.html` in the same directory

### Note / نکته

The `static/` folder is **not** included in the public GitHub repo. You need to copy it from your Windows build machine or keep it locally.

پوشه `static/` در مخزن عمومی گیت‌هاب **نیست**. باید از روی سیستم ویندوزی که باهاش ساختی کپی کنی یا محلی نگه‌ش داری.

---

## Build from Source

```bash
# 1. Clone the Nova-Proxy worker
git clone https://github.com/IRNova/Nova-Proxy.git

# 2. Set up Python 3.14+
pip install pyinstaller Pillow

# 3. Build the EXE
pyinstaller --onefile --name NovaWizard --add-data "static;static" --icon app.ico nova_wizard.py

# Note: the static/ folder (index.html, etc.) is required at build time
# but is not distributed in source
```

---

## Links

| | |
|---|---|
| 🌐 **Website** | [https://novaproxy.online/](https://novaproxy.online/) |
| 📢 **Telegram Channel** | [https://t.me/irnova_proxy](https://t.me/irnova_proxy) |
| 💬 **Telegram Group** | [https://t.me/irnovaproxy_group](https://t.me/irnovaproxy_group) |
| ▶️ **YouTube** | [https://www.youtube.com/@novaproxyir](https://www.youtube.com/@novaproxyir) |
| 🐦 **X / Twitter** | [https://x.com/irNovaProxy](https://x.com/irNovaProxy) |
| 📦 **Nova Proxy Worker** | [https://github.com/IRNova/Nova-Proxy](https://github.com/IRNova/Nova-Proxy) |

---

## Support / حمایت

If you like this project, support us with a donation.

اگه از پروژه خوشت اومده، با دونیت از ما حمایت کن.

🔗 **Donation link / لینک دونیت:** [https://daramet.com/NovaPr](https://daramet.com/NovaPr)

**TON :**

```
UQD51lGC35rP_SbVYgbFA7CEEii4GVMFgqj4N8fiGi6m425w
```

---

## License

MIT
