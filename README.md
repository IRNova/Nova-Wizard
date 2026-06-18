<div align="center">
  <img src="https://raw.githubusercontent.com/iiviirv/irnova-site/main/brand/nova-logo-gradient.svg" width="70" alt="Nova">
  <h1>Nova Wizard</h1>
  <p><b>Local OAuth deployer for Nova Proxy on Cloudflare Workers</b></p>
  <p>
    <a href="README.fa.md">🇮🇷 فارسی</a>
  </p>
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

---

## Quick Install — One Click via Website

The fastest way to deploy Nova Proxy is through our official website:

[**🚀 Install from novaproxy.online**](https://novaproxy.online/install)

No download, no setup — just click, authorize Cloudflare, and you're done. This method is faster than running the local tool.

The local **Nova Wizard** (below) is an alternative for users who prefer running everything offline on their own machine.

---

## Download

[**⬇ Download NovaWizard.exe**](https://github.com/IRNova/Nova-Wizard/releases/download/V1.0.0/NovaWizard.exe) (8.4 MB) — Windows 64-bit, portable

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

1. **Run** `NovaWizard.exe` — a terminal window opens with a local URL.
2. **Open your browser** — it opens automatically at `http://127.0.0.1:8000/?token=...`
3. **Click "Login with Cloudflare"** — Cloudflare opens in a new tab.
4. **Authorize on Cloudflare** — approve the access request, then close the tab.
5. **Choose your account** — select which Cloudflare account to deploy on.
6. **Name your resources** — worker name, KV namespace, D1 database are pre-filled. You can change them.
7. **Click "Deploy now"** — the worker is downloaded from GitHub, uploaded to Cloudflare, KV + D1 are created and bound.
8. **Done!** — open your panel, set your own admin password on first visit.

> You can rename the worker, KV namespace, and D1 database **before** deploying. The names are random by default but fully editable.

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

## Requirements

- **Windows** 7 / 8 / 10 / 11 (64-bit)
- **Linux** — Python 3.8+ (run directly, see below)
- A Cloudflare account
- Internet connection

---

## Linux

Nova Wizard runs natively on Linux — no EXE needed.

### Quick start

```bash
# 1. Clone the repo
git clone https://github.com/IRNova/Nova-Wizard.git
cd Nova-Wizard

# 2. Make sure static/index.html is present (from your build machine)
#    The static/ folder is required but not in the public repo.

# 3. Run directly with Python
python3 nova_wizard.py
```

### Requirements

- Python 3.8+ (built-in on most distros)
- `static/index.html` in the same directory

### Note

The `static/` folder is **not** included in the public GitHub repo. You need to copy it from your Windows build machine or keep it locally.

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

## Support

If you like this project, support us with a donation.

**TON:**

```
UQD51lGC35rP_SbVYgbFA7CEEii4GVMFgqj4N8fiGi6m425w
```

---

## License

MIT
