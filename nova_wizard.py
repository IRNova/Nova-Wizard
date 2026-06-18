#!/usr/bin/env python3
"""
Nova OAuth Wizard — OAuth-only Cloudflare Worker deployer.
"""
from __future__ import annotations

import base64, hashlib, json, mimetypes, os, secrets, ssl, threading, traceback
import uuid as uuid_mod
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib import request as urlrequest, error as urlerror
from urllib.parse import quote, urlparse, parse_qs, urlencode

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
WORKER_FILE = BASE_DIR / "worker.js"
GITHUB_WORKER_URL = "https://raw.githubusercontent.com/IRNova/Nova-Proxy/refs/heads/main/worker.js"
CF_API_BASE = "https://api.cloudflare.com/client/v4"
LOCAL_TOKEN = secrets.token_urlsafe(32)
COOKIE_NAME = "nova_token"

OAUTH_CLIENT_ID = "54d11594-84e4-41aa-b438-e81b8fa78ee7"
OAUTH_AUTH_URL = "https://dash.cloudflare.com/oauth2/auth"
OAUTH_TOKEN_URL = "https://dash.cloudflare.com/oauth2/token"
OAUTH_SCOPES = ["account:read","user:read","workers:write","workers_kv:write","workers_scripts:write","d1:write","pages:write","pages:read","zone:read"]
OAUTH_REDIRECT_PORT = 8976

_oauth_state = ""
_oauth_code_verifier = ""
_oauth_token = None
_oauth_account_id = ""
_oauth_account_name = ""
_deployment_history: list = []

# ─── helpers ───────────────────────────────────────────────

def dumps(d): return json.dumps(d, ensure_ascii=False, separators=(",",":")).encode("utf-8")
def read_json(h):
    l = int(h.headers.get("Content-Length") or "0")
    r = h.rfile.read(l) if l else b"{}"
    return json.loads(r.decode("utf-8")) if r else {}

def safe_name(v, fallback="nova-panel"):
    c = "".join(ch.lower() if ch.isalnum() else "-" for ch in (v or "").strip())
    while "--" in c: c = c.replace("--", "-")
    return c.strip("-") or fallback

_WORDS_A = ["sunny","nova","swift","neon","atlas","orbit","pixel","rocket","falcon","crystal","rainbow","mango","coral","luna","pearl","turbo"]
_WORDS_B = ["panel","bridge","node","core","wave","path","gate","proxy","stack","vault","spark","portal","cloud","river","garden","comet"]
_STORE_WORDS = ["vault","store","cache","locker","garden","stash","bucket","shelf"]

def fetch_worker_from_github():
    print("  [fetch] Downloading worker.js from GitHub...")
    try:
        with urlrequest.urlopen(urlrequest.Request(GITHUB_WORKER_URL,
            headers={"User-Agent":"Mozilla/5.0"}), timeout=30) as r:
            code = r.read()
            if len(code) < 100: raise _CFErr(f"Downloaded file too small ({len(code)} bytes)")
            WORKER_FILE.write_bytes(code)
            print(f"  [fetch] OK — {len(code)} bytes saved")
            return len(code)
    except urlerror.HTTPError as e:
        raw = e.read()
        raise _CFErr(f"GitHub HTTP {e.code}: {raw.decode('utf-8','replace')[:200]}")
    except Exception as e:
        raise _CFErr(f"GitHub fetch failed: {e}")

def rand_name(max_len=55):
    for _ in range(50):
        s = f"{secrets.choice(_WORDS_A)}-{secrets.choice(_WORDS_B)}-{secrets.choice(_WORDS_A)}-{secrets.token_hex(3)}"
        s = safe_name(s, "nova-panel")[:max_len].strip("-")
        if s: return s
    return f"nova-panel-{secrets.token_hex(4)}"[:max_len]

def suggest():
    w = rand_name(55)
    kv = safe_name(f"{w}-{secrets.choice(_STORE_WORDS)}", "nova-panel-vault")[:60].strip("-") or f"nova-{secrets.token_hex(4)}-vault"[:60]
    d1 = safe_name(f"{w}-db", "nova-panel-db")[:32].strip("-") or f"nova-db-{secrets.token_hex(4)}"[:32]
    return {"worker_name": w, "kv_namespace": kv, "d1_name": d1}

# ─── OAuth PKCE ────────────────────────────────────────────

def gen_state(): return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
def gen_verifier(): return base64.urlsafe_b64encode(secrets.token_bytes(33)).decode().rstrip("=")
def gen_challenge(v): return base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip("=")

def oauth_url():
    global _oauth_state, _oauth_code_verifier
    _oauth_state = gen_state()
    _oauth_code_verifier = gen_verifier()
    p = urlencode({"client_id": OAUTH_CLIENT_ID, "response_type": "code",
        "redirect_uri": f"http://localhost:{OAUTH_REDIRECT_PORT}/oauth/callback",
        "scope": " ".join(OAUTH_SCOPES), "state": _oauth_state,
        "code_challenge": gen_challenge(_oauth_code_verifier), "code_challenge_method": "S256"})
    return OAUTH_AUTH_URL + "?" + p

def _make_ssl_ctx(verify=True):
    ctx = ssl.create_default_context()
    if not verify:
        # Fallback only — some Windows Python installs ship without a CA bundle, which breaks
        # TLS verification. We try verified first (below) and only use this if that fails.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx

# Verified opener (secure). If the machine has no CA certs, we lazily fall back to an unverified
# one inside exchange_token, with a printed warning — never silently insecure by default.
_OAUTH_OPENER = urlrequest.build_opener(urlrequest.HTTPSHandler(context=_make_ssl_ctx(True)))
_OAUTH_OPENER_INSECURE = None

def exchange_token(code, verifier):
    global _OAUTH_OPENER_INSECURE
    d = urlencode({"client_id": OAUTH_CLIENT_ID, "code": code, "code_verifier": verifier,
        "redirect_uri": f"http://localhost:{OAUTH_REDIRECT_PORT}/oauth/callback",
        "grant_type": "authorization_code"}).encode()
    req = urlrequest.Request(OAUTH_TOKEN_URL, data=d,
        headers={"Content-Type":"application/x-www-form-urlencoded","Accept":"application/json","User-Agent":"Mozilla/5.0"})
    try:
        with _OAUTH_OPENER.open(req, timeout=30) as r:
            return json.loads(r.read())
    except urlerror.URLError as e:
        # If TLS verification failed (e.g. machine has no CA bundle), retry once unverified + warn.
        if isinstance(getattr(e, "reason", None), ssl.SSLError) or "CERTIFICATE" in str(getattr(e, "reason", "")).upper():
            print("  [warn] TLS certificate could not be verified on this machine — retrying without verification.")
            if _OAUTH_OPENER_INSECURE is None:
                _OAUTH_OPENER_INSECURE = urlrequest.build_opener(urlrequest.HTTPSHandler(context=_make_ssl_ctx(False)))
            try:
                with _OAUTH_OPENER_INSECURE.open(req, timeout=30) as r:
                    return json.loads(r.read())
            except urlerror.HTTPError as e2:
                raw = e2.read();
                try: body = json.loads(raw)
                except: body = raw.decode("utf-8","replace")
                raise _CFErr(f"Token exchange failed (HTTP {e2.code}): {body}", status=e2.code)
        raise _CFErr(f"Token exchange network error: {getattr(e,'reason',e)}")
    except urlerror.HTTPError as e:
        raw = e.read()
        try: body = json.loads(raw)
        except: body = raw.decode("utf-8","replace")
        print(f"  [OAuth token exchange HTTP {e.code}] {body}")
        raise _CFErr(f"Token exchange failed (HTTP {e.code}): {body}", status=e.code)
    except Exception as e:
        import traceback as _tb
        _tb.print_exc()
        print(f"  [OAuth token exchange error] type={type(e).__name__} repr={repr(e)}")
        raise _CFErr(f"Token exchange failed ({type(e).__name__}): {repr(e)}")

# ─── OAuth callback server (port 8976) ─────────────────────

_oauth_result: Dict = {}
_oauth_error: str = ""
_oauth_event = threading.Event()
_oauth_processed_states: set = set()

class OAuthCBHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _oauth_state, _oauth_code_verifier, _oauth_result, _oauth_token, _oauth_account_id, _oauth_account_name
        p = urlparse(self.path)
        if p.path == "/oauth/callback":
            q = parse_qs(p.query)
            code = (q.get("code") or [None])[0]
            state = (q.get("state") or [None])[0]
            err = (q.get("error") or [None])[0]
            # If already connected, return success (handles duplicate callbacks)
            if _oauth_token and state in _oauth_processed_states:
                ok = True; msg = "Already connected - close this tab"
            elif err:
                _oauth_error = f"Cloudflare: {err}"; _oauth_result = {"ok": False, "error": _oauth_error}
                ok = False; msg = _oauth_error
            elif not code or state != _oauth_state:
                _oauth_error = "Invalid state"; _oauth_result = {"ok": False, "error": _oauth_error}
                ok = False; msg = _oauth_error
            else:
                _oauth_processed_states.add(state)
                try:
                    t = exchange_token(code, _oauth_code_verifier)
                    if t and t.get("access_token"):
                        _oauth_token = t["access_token"]
                        _oauth_error = ""
                        _oauth_result = {"ok": True, "access_token": t["access_token"], "refresh_token": t.get("refresh_token","")}
                        # Fetch the first account so the UI can show whose account this is.
                        try:
                            _acc = CFClient(_oauth_token).req("GET", "/accounts?per_page=1")
                            _first = (_acc.get("result") or [{}])[0]
                            _oauth_account_id = _first.get("id") or ""
                            _oauth_account_name = _first.get("name") or ""
                        except Exception: pass
                        ok = True; msg = "Connected - close this tab"
                    else:
                        _oauth_error = f"Token failed: {t.get('error_description',t.get('error','?')) if t else 'no response'}"
                        _oauth_result = {"ok": False, "error": _oauth_error}
                        ok = False; msg = _oauth_error
                except _CFErr as e:
                    _oauth_error = str(e)
                    _oauth_result = {"ok": False, "error": _oauth_error}
                    ok = False; msg = _oauth_error
            # Bilingual (EN + FA) callback page. Built without backslashes inside f-string
            # expressions (that pattern raises SyntaxError on Python <= 3.11). IRNova brand.
            icon = "&#9989;" if ok else "&#10060;"
            if ok:
                en_status = "Connected"
                fa_status = "وصل شد"  # وصل شد
                en_hint = "This window can be closed. The wizard continues automatically &mdash; switch back to it."
                fa_hint = ("این پنجره را می‌توانی "
                           "ببندی. دستیار خودکار "
                           "ادامه می‌دهد — به آن برگرد.")  # close window / wizard continues
                extra_html = ('<p style="color:#9aa4b8;margin-top:12px;font-size:.95rem">' + en_hint + '</p>'
                              '<p style="color:#9aa4b8;margin-top:4px;font-size:.95rem" dir="rtl">' + fa_hint + '</p>')
            else:
                en_status = "Sign-in failed"
                fa_status = "ورود ناموفق بود"  # ورود ناموفق بود
                btn_label = "Try again &middot; دوباره"  # Try again · دوباره
                extra_html = ('<p style="color:#fca5a5;margin:8px 0 0;font-size:.9rem">' + msg + '</p>'
                              '<button onclick="window.close();if(window.opener)window.opener.location.reload()" '
                              'style="padding:11px 24px;border-radius:12px;border:none;'
                              'background:linear-gradient(120deg,#22d3ee,#818cf8,#a855f7);color:#05060a;'
                              'font-weight:700;font-size:14px;cursor:pointer;margin-top:14px">' + btn_label + '</button>')
            html = (
                '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Nova</title>'
                '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=Vazirmatn:wght@400;600;700&display=swap" rel="stylesheet"></head>'
                '<body style="margin:0;background:#05060a;color:#eef1f7;'
                'font-family:Inter,Vazirmatn,system-ui,-apple-system,Segoe UI,sans-serif;'
                'display:grid;place-items:center;min-height:100vh">'
                '<div style="text-align:center;padding:36px;max-width:440px">'
                '<div style="width:48px;height:48px;border-radius:12px;margin:0 auto 18px;'
                'background:linear-gradient(120deg,#22d3ee,#818cf8,#a855f7);display:flex;'
                'align-items:center;justify-content:center;font-weight:900;color:#05060a;font-size:24px">N</div>'
                '<h1 style="font-size:1.4rem;font-weight:800;margin:0 0 14px">Nova Wizard</h1>'
                '<p style="font-size:1.1rem;margin:0;font-weight:600">' + icon + ' ' + en_status
                + ' <span style="color:#9aa4b8">/</span> ' + fa_status + '</p>'
                + extra_html +
                '</div></body></html>'
            )
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",str(len(html.encode())))
            self.end_headers(); self.wfile.write(html.encode())
            _oauth_event.set()
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, *a): pass

def start_oauth_server():
    s = ThreadingHTTPServer(("127.0.0.1", OAUTH_REDIRECT_PORT), OAuthCBHandler)
    threading.Thread(target=s.serve_forever, daemon=True).start()

# ─── CF API ────────────────────────────────────────────────

class CFClient:
    def __init__(self, token, timeout=70):
        self.token = token; self.timeout = timeout
        self.opener = urlrequest.build_opener()
    def req(self, method, path, *, json_body=None, data=None, ctype=None, extra_headers=None, accept_404=False):
        h = {"Authorization": f"Bearer {self.token}", "Accept": "application/json", "User-Agent": "nova-wizard/3.0"}
        if extra_headers: h.update(extra_headers)
        if json_body: data = dumps(json_body); h["Content-Type"] = "application/json"
        elif ctype: h["Content-Type"] = ctype
        r = urlrequest.Request(CF_API_BASE + path, data=data, headers=h, method=method.upper())
        try:
            with self.opener.open(r, timeout=self.timeout) as resp:
                raw = resp.read()
                p = json.loads(raw.decode("utf-8")) if raw else {"success": True, "result": None}
                if isinstance(p, dict) and p.get("success") is False:
                    raise _CFErr(format_err(p), status=resp.status)
                return p
        except urlerror.HTTPError as e:
            if accept_404 and e.code == 404: return {"success": True, "result": None}
            raw = e.read()
            try: p = json.loads(raw.decode("utf-8")) if raw else None
            except: p = raw.decode("utf-8","replace") if raw else None
            raise _CFErr(format_err(p) if p else f"HTTP {e.code}", status=e.code)
        except urlerror.URLError as e: raise _CFErr(f"Network: {e.reason}")

class _CFErr(RuntimeError):
    def __init__(self, msg, status=None): super().__init__(msg); self.status = status

def format_err(p):
    if isinstance(p, dict):
        es = p.get("errors") or []
        ms = []
        for e in es:
            if isinstance(e, dict): ms.append(f"[{e.get('code')}] {e.get('message')}" if e.get('code') else str(e.get('message')))
            else: ms.append(str(e))
        if ms: return "CF: " + " | ".join(ms)
        if p.get("message"): return str(p["message"])
    return str(p)

# ─── resource ops ──────────────────────────────────────────

def list_ns(cf, aid):
    out = []; page = 1
    while True:
        p = cf.req("GET", f"/accounts/{quote(aid)}/storage/kv/namespaces?per_page=100&page={page}")
        out.extend(p.get("result") or [])
        info = p.get("result_info") or {}
        if page >= int(info.get("total_pages") or 1): return out
        page += 1

def find_kv(cf, aid, title):
    for ns in list_ns(cf, aid):
        if ns.get("title") == title: return ns
    return None

def get_or_create_kv(cf, aid, title):
    ex = find_kv(cf, aid, title)
    if ex: return {"id": ex["id"], "title": title, "reused": True}
    p = cf.req("POST", f"/accounts/{quote(aid)}/storage/kv/namespaces", json_body={"title": title})
    r = p.get("result") or {}
    return {"id": r.get("id"), "title": r.get("title") or title, "reused": False}

def get_or_create_d1(cf, aid, name):
    try:
        p = cf.req("GET", f"/accounts/{quote(aid)}/d1/database?name={quote(name)}")
        rs = p.get("result") or []
        if rs: return {"id": rs[0].get("uuid") or rs[0].get("id"), "name": rs[0].get("name") or name, "reused": True}
    except: pass
    p = cf.req("POST", f"/accounts/{quote(aid)}/d1/database", json_body={"name": name})
    r = p.get("result") or {}
    return {"id": r.get("uuid") or r.get("id"), "name": r.get("name") or name, "reused": False}

def get_subdomain(cf, aid):
    try:
        p = cf.req("GET", f"/accounts/{quote(aid)}/workers/subdomain")
        r = p.get("result") or {}
        s = r.get("subdomain") or r.get("name")
        if s: return s
    except: pass
    des = safe_name(f"nova-{secrets.token_hex(4)}", "nova-panel")
    for m in ("PUT","POST","PATCH"):
        try:
            p = cf.req(m, f"/accounts/{quote(aid)}/workers/subdomain", json_body={"subdomain": des})
            r = p.get("result") or {}
            return r.get("subdomain") or des
        except: pass
    raise _CFErr("Could not set workers.dev subdomain")

def enable_subdomain(cf, aid, name):
    for m in ("POST","PUT","PATCH"):
        try:
            cf.req(m, f"/accounts/{quote(aid)}/workers/scripts/{quote(name)}/subdomain", json_body={"enabled": True})
            return
        except: pass
    cf.req("GET", f"/accounts/{quote(aid)}/workers/scripts/{quote(name)}")  # check exists
    raise _CFErr("Worker uploaded but subdomain enable failed")

def build_multi(meta, code):
    bound = "----wiz" + secrets.token_hex(16)
    nl = "\r\n"
    parts = []
    for key, fname, ct, data in [("metadata", None, "application/json", dumps(meta)), ("worker.js", "worker.js", "application/javascript+module", code)]:
        d = f'Content-Disposition: form-data; name="{key}"'
        if fname: d += f'; filename="{fname}"'
        parts.append(f"--{bound}{nl}{d}{nl}Content-Type: {ct}{nl}{nl}".encode("utf-8") + data + nl.encode("utf-8"))
    parts.append(f"--{bound}--{nl}".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={bound}"

def build_pages_multi(code):
    bound = "----wiz" + secrets.token_hex(16)
    nl = "\r\n"
    parts = []
    for key, fname, ct, data in [("manifest", None, "application/json", b"{}"), ("_worker.js", "_worker.js", "application/javascript", code)]:
        d = f'Content-Disposition: form-data; name="{key}"'
        if fname: d += f'; filename="{fname}"'
        parts.append(f"--{bound}{nl}{d}{nl}Content-Type: {ct}{nl}{nl}".encode("utf-8") + data + nl.encode("utf-8"))
    parts.append(f"--{bound}--{nl}".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={bound}"

# ─── deploy ────────────────────────────────────────────────

def deploy(cf, aid, worker_name, kv_title, d1_name, extra_env, deploy_type):
    kv = get_or_create_kv(cf, aid, kv_title)
    kv_id = kv.get("id")
    if not kv_id: raise _CFErr("KV namespace ID not found")

    d1_db = None; d1_id = None
    if d1_name:
        d1_db = get_or_create_d1(cf, aid, d1_name)
        d1_id = d1_db.get("id")

    # We deliberately do NOT pre-set a password or bind ADMIN/UUID/KEY. The worker treats an env
    # ADMIN/KEY/UUID as an already-configured admin password and would skip its /install page —
    # silently choosing the password for the user. Instead we leave those unset so the worker sends
    # the user to /install on first visit to pick THEIR OWN password. The worker auto-generates and
    # pins its encryption key (auto_key) and node UUID (worker_uuid) in KV on first run.
    bindings = [
        {"type": "kv_namespace", "name": "KV", "namespace_id": kv_id},
    ]
    if d1_id: bindings.append({"type": "d1", "name": "DB", "database_id": d1_id})
    for k, v in extra_env.items():
        if v: bindings.append({"type": "plain_text", "name": k, "text": v})

    # global_fetch_strictly_public lets backend mode reach a same-account gray-cloud VPS hostname
    # without Cloudflare returning 522 (self-loop). nodejs_compat is required by the worker.
    meta = {"main_module": "worker.js", "compatibility_date": "2025-01-01",
            "compatibility_flags": ["nodejs_compat", "global_fetch_strictly_public"], "bindings": bindings}
    code = WORKER_FILE.read_bytes()

    if deploy_type == "pages":
        body, ct = build_pages_multi(code)
        try:
            ex = cf.req("GET", f"/accounts/{quote(aid)}/pages/projects/{quote(worker_name)}", accept_404=True)
            kv_ns = {"KV": {"namespace_id": kv_id}}
            d1_b = {}
            if d1_id: d1_b = {"DB": {"database_id": d1_id, "type": "d1"}}
            # No ADMIN/UUID/KEY — let the worker's /install page take the user's own password.
            ev = {}
            for k, v in extra_env.items():
                if v: ev[k] = {"type":"plain_text","value":v}
            proj = {"name": worker_name, "production_branch": "main", "compatibility_date": "2025-01-01", "compatibility_flags": ["nodejs_compat", "global_fetch_strictly_public"], "kv_namespaces": kv_ns, "d1_databases": d1_b, "env_vars": ev}
            if ex and ex.get("result"):
                cf.req("PATCH", f"/accounts/{quote(aid)}/pages/projects/{quote(worker_name)}", json_body=proj)
            else:
                cf.req("POST", f"/accounts/{quote(aid)}/pages/projects", json_body=proj)
        except _CFErr as e:
            if e.status == 404: cf.req("POST", f"/accounts/{quote(aid)}/pages/projects", json_body=proj)
            else: raise
        cf.req("POST", f"/accounts/{quote(aid)}/pages/projects/{quote(worker_name)}/deployments", data=body, ctype=ct)
        sub = None
        try:
            pj = cf.req("GET", f"/accounts/{quote(aid)}/pages/projects/{quote(worker_name)}")
            sub = (pj.get("result") or {}).get("subdomain")
        except: pass
        url = f"https://{worker_name}.{sub}" if sub else f"https://{worker_name}.pages.dev"
    else:
        body, ct = build_multi(meta, code)
        cf.req("PUT", f"/accounts/{quote(aid)}/workers/scripts/{quote(worker_name)}", data=body, ctype=ct)
        enable_subdomain(cf, aid, worker_name)
        asub = get_subdomain(cf, aid)
        url = f"https://{worker_name}.{asub}.workers.dev" if asub else ""

    # panel_url points at /install so the user sets their own password on first visit.
    return {"worker_name": worker_name, "worker_url": url, "panel_url": url + "/install" if url else "",
        "set_password": True,
        "kv_namespace": kv, "d1_database": d1_db, "deploy_type": deploy_type}

def cleanup(cf, aid, worker_name, kv_id, kv_title, d1_id, deploy_type):
    r = {"worker": False, "kv": False, "d1": False, "notes": []}
    if worker_name:
        try:
            if deploy_type == "pages":
                cf.req("DELETE", f"/accounts/{quote(aid)}/pages/projects/{quote(worker_name)}", accept_404=True)
            else: cf.req("DELETE", f"/accounts/{quote(aid)}/workers/scripts/{quote(worker_name)}", accept_404=True)
            r["worker"] = True
        except _CFErr as e:
            if e.status == 404: r["notes"].append("Worker not found")
            else: raise
    if not kv_id and kv_title:
        ns = find_kv(cf, aid, kv_title)
        if ns: kv_id = ns.get("id") or ""
    if kv_id:
        try: cf.req("DELETE", f"/accounts/{quote(aid)}/storage/kv/namespaces/{quote(kv_id)}", accept_404=True); r["kv"] = True
        except _CFErr as e:
            if e.status == 404: r["notes"].append("KV not found")
            else: raise
    if d1_id:
        try: cf.req("DELETE", f"/accounts/{quote(aid)}/d1/database/{quote(d1_id)}", accept_404=True); r["d1"] = True
        except _CFErr as e:
            if e.status == 404: r["notes"].append("D1 not found")
            else: raise
    return r

# ─── HTTP handler ──────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "NovaWizard/3.0"

    def authed(self, parsed=None):
        if parsed:
            qt = (parse_qs(parsed.query).get("token") or [""])[0]
            if secrets.compare_digest(qt, LOCAL_TOKEN): return True
        for part in (self.headers.get("Cookie") or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                if k == COOKIE_NAME and secrets.compare_digest(v, LOCAL_TOKEN): return True
        return False

    def send_json(self, data, status=200):
        raw = dumps(data)
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers(); self.wfile.write(raw)

    def send_html(self, html, status=200):
        raw = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers(); self.wfile.write(raw)

    def send_file(self, path, set_cookie=False):
        if not path.exists() or not path.is_file(): return self.send_error(404)
        raw = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Cache-Control","no-store")
        if set_cookie: self.send_header("Set-Cookie", f"{COOKIE_NAME}={LOCAL_TOKEN}; Path=/; SameSite=Strict")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers(); self.wfile.write(raw)

    def unauth(self):
        # Bilingual (EN + FA), IRNova brand. Shown if the page is opened without the secure token.
        page = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Nova</title>'
            '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=Vazirmatn:wght@400;600;700&display=swap" rel="stylesheet"></head>'
            '<body style="margin:0;background:#05060a;color:#eef1f7;'
            'font-family:Inter,Vazirmatn,system-ui,-apple-system,Segoe UI,sans-serif;'
            'display:grid;place-items:center;min-height:100vh">'
            '<div style="max-width:460px;padding:32px;text-align:center;border:1px solid rgba(255,255,255,.12);'
            'border-radius:16px;background:rgba(255,255,255,.04)">'
            '<div style="width:46px;height:46px;border-radius:11px;margin:0 auto 16px;'
            'background:linear-gradient(120deg,#22d3ee,#818cf8,#a855f7);display:flex;align-items:center;'
            'justify-content:center;font-weight:900;color:#05060a;font-size:22px">N</div>'
            '<h1 style="font-size:1.3rem;font-weight:800;margin:0 0 12px">Nova Wizard</h1>'
            '<p style="color:#9aa4b8;margin:0 0 6px">Open the secure link shown in the terminal window.</p>'
            '<p style="color:#9aa4b8;margin:0 0 14px" dir="rtl">لینکِ امن را که در پنجرهٔ ترمینال نشان داده شده باز کن.</p>'
            '<code style="display:block;padding:13px;border-radius:10px;background:#0b0e16;'
            'border:1px solid rgba(255,255,255,.09);color:#22d3ee;font-size:.85rem">'
            'http://127.0.0.1:8000/?token=&hellip;</code>'
            '</div></body></html>'
        )
        self.send_html(page, 401)

    def do_GET(self):
        global _oauth_error
        try:
            p = urlparse(self.path)
            if p.path == "/":
                if self.authed(p): return self.send_file(STATIC_DIR / "index.html", True)
                return self.unauth()
            if not self.authed(p): return self.send_json({"ok":False,"error":"Unauth"}, 401)
            if p.path == "/api/config":
                return self.send_json({"ok":True, "oauth_connected": bool(_oauth_token),
                    "oauth_info": {"account_id":_oauth_account_id,"account_name":_oauth_account_name} if _oauth_token else {},
                    "deployments": _deployment_history, "suggestion": suggest(),
                    "worker_exists": WORKER_FILE.exists()})
            if p.path == "/api/fetch-worker":
                try:
                    sz = fetch_worker_from_github()
                    return self.send_json({"ok":True, "size": sz, "path": str(WORKER_FILE)})
                except _CFErr as e: return self.send_json({"ok":False,"error":str(e)}, 400)
            if p.path == "/favicon.ico":
                self.send_response(204); self.end_headers(); return
            if p.path == "/api/oauth/url":
                _oauth_error = ""
                return self.send_json({"ok":True, "url": oauth_url()})
            if p.path == "/api/oauth/status":
                return self.send_json({"ok":True, "connected": bool(_oauth_token),
                    "account_id": _oauth_account_id, "account_name": _oauth_account_name,
                    "error": _oauth_error if _oauth_error else None})
            t = (STATIC_DIR / p.path.lstrip("/")).resolve()
            if STATIC_DIR.resolve() in t.parents and self.authed(p): return self.send_file(t)
            self.send_error(404)
        except Exception as e:
            traceback.print_exc(); self.send_json({"ok":False,"error":str(e)}, 500)

    def do_POST(self):
        try:
            p = urlparse(self.path)
            if not self.authed(p): return self.send_json({"ok":False,"error":"Unauth"}, 401)
            body = read_json(self)

            if p.path == "/api/accounts":
                if not _oauth_token: return self.send_json({"ok":False,"error":"Not authenticated"}, 401)
                cf = CFClient(_oauth_token)
                r = cf.req("GET", "/accounts?per_page=100")
                accts = [{"id": a.get("id"), "name": a.get("name")} for a in (r.get("result") or [])]
                return self.send_json({"ok":True, "accounts": accts})

            if p.path == "/api/deploy":
                if not _oauth_token: return self.send_json({"ok":False,"error":"Not authenticated"}, 401)
                if not WORKER_FILE.exists():
                    try: fetch_worker_from_github()
                    except _CFErr as e: return self.send_json({"ok":False,"error":f"Fetch worker.js failed: {e}"}, 400)
                cf = CFClient(_oauth_token)
                aid = (body.get("account_id") or _oauth_account_id or "").strip()
                if not aid: return self.send_json({"ok":False,"error":"Account ID required"}, 400)
                dt = (body.get("deploy_type") or "worker").strip()
                wn = safe_name(body.get("worker_name") or "", rand_name(55))
                kv = safe_name(body.get("kv_namespace") or "", f"{wn}-vault")
                d1 = safe_name(body.get("d1_name") or "", f"{wn}-db")
                extra = {}
                for k in ["PROXYIP","NAT64","HOST","PAGES_URL","DEBUG","GO2SOCKS5","BACKEND_URL"]:
                    v = (body.get(k.lower()) or "").strip()
                    if v: extra[k] = v
                result = deploy(cf, aid, wn, kv, d1, extra, dt)
                result["id"] = secrets.token_hex(8)
                result["account_id"] = aid
                result["status"] = "active"
                _deployment_history.insert(0, result)
                if len(_deployment_history) > 50: _deployment_history[:] = _deployment_history[:50]
                return self.send_json({"ok":True, "result": result, "suggestion": suggest()})

            if p.path == "/api/delete_deploy":
                if not _oauth_token: return self.send_json({"ok":False,"error":"Not authenticated"}, 401)
                cf = CFClient(_oauth_token)
                aid = (body.get("account_id") or _oauth_account_id or "").strip()
                wn = safe_name(body.get("worker_name") or "")
                kv_id = (body.get("kv_id") or "").strip()
                kv_t = safe_name(body.get("kv_namespace") or "", "") if body.get("kv_namespace") else ""
                d1_id = (body.get("d1_id") or "").strip()
                dt = (body.get("deploy_type") or "worker").strip()
                if not aid: return self.send_json({"ok":False,"error":"Account ID required"}, 400)
                res = cleanup(cf, aid, wn, kv_id, kv_t, d1_id, dt)
                did = (body.get("deployment_id") or "").strip()
                if did:
                    _deployment_history[:] = [d for d in _deployment_history if d.get("id") != did]
                res["message"] = "Cleanup done"
                return self.send_json({"ok":True, "result": res})

            self.send_json({"ok":False,"error":"Not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            st = 500
            if isinstance(e, _CFErr):
                st = e.status if (e.status and 400 <= e.status < 600) else 400
            self.send_json({"ok":False,"error":str(e)}, st)

    def log_message(self, fmt, *a):
        print(f"  [{self.log_date_time_string()}] {fmt % a}")

# ─── main ──────────────────────────────────────────────────

def main():
    start_oauth_server()
    host = os.environ.get("NOVA_HOST", "127.0.0.1")
    port = int(os.environ.get("NOVA_PORT", "8000"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/?token={LOCAL_TOKEN}"
    print()
    print("=" * 54)
    print("  Nova OAuth Wizard")
    print("  Cloudflare Worker + KV + D1 Deployer")
    print("=" * 54)
    print(f"  URL:  {url}")
    print(f"  JS:   {WORKER_FILE}")
    if not WORKER_FILE.exists(): print("  [!] worker.js not found!")
    print()
    print("  1. Open browser")
    print("  2. Click 'Login with Cloudflare'")
    print("  3. Authorize on Cloudflare")
    print("  4. Enter names + Deploy")
    print()
    print("  Ctrl+C to stop.")
    print("=" * 54)
    print()
    import webbrowser as _wb
    threading.Timer(1.5, lambda: _wb.open(url)).start()
    try: httpd.serve_forever()
    except KeyboardInterrupt: print("\n  Stopped.")

if __name__ == "__main__": main()
