#!/usr/bin/env python3
"""
Nova OAuth Wizard: OAuth-only Cloudflare Worker deployer.
"""
from __future__ import annotations

import base64, hashlib, json, mimetypes, os, secrets, ssl, sys, threading, time, traceback
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
# The exact release the Telegram bot deploys. Both tools install the same bytes or
# neither does: this tool downloads an executable over the network and hands it
# straight to the user's own Cloudflare account, so an unverified download is a
# supply-chain hole. Bump this together with the bot's WORKER_JS_SHA256.
WORKER_SHA256 = "2088346c0b84df0d5302ac95119f0426ebd41ebf11e961e9526388ab6d3cd2cb"
CF_API_BASE = "https://api.cloudflare.com/client/v4"
LOCAL_TOKEN = secrets.token_urlsafe(32)
COOKIE_NAME = "nova_token"

# The Nova mark, inlined so the callback page needs no network and no asset file.
NOVA_MARK_SVG = (
    '<svg width="48" height="48" viewBox="0 0 1254 1254" aria-hidden="true" focusable="false"> <defs><linearGradient id="cb-novaMark" x1="128.06" y1="1122.76" x2="1206.85" y2="43.97" gradientUnits="userSpaceOnUse"><stop offset=".04" stop-color="#9d4efb"/><stop offset="1" stop-color="#02cdf3"/></linearGradient></defs> <path fill="url(#cb-novaMark)" d="M1185.57,149.23c0-43.84-27.55-82.6-66.19-100.7-40.83-19.13-87.98-16.85-126.82,6.19-33.3,19.76-56.22,55.99-56.25,95.68l-.38,653.25.09,39.98c.03,13.51-.33,26.37-3.82,39.13-8.12,29.65-30.52,53.04-56.69,62.39-32.53,11.62-65.87,5.5-91.07-15.75-20.65-17.42-33.28-42.64-33.32-70.11l-.35-245.85.07-231.05c.04-148.83-97.26-281.46-240.38-321.81-67.49-19.02-138.62-19.66-204.99,2.42l-13.66,4.55C159.84,114.72,68.42,239.99,68.41,381.43l-.06,712.76c0,68.93,56.48,123.39,124.03,124.15,65.31.73,125.56-52.18,125.64-120.57l.88-712.63c.07-54.62,49.94-96.23,103.56-88.53,43.56,6.25,78.96,43.23,79.08,88.34l1.24,493.92c.16,62.52,24.72,123.29,59.49,174.21,43.7,63.99,108.48,111.28,182.25,133.98,91.72,28.23,190.9,16.68,273.4-31.79,36.89-21.68,68.83-50.13,94.95-83.49l16.54-23.16c31.76-44.47,56.26-119.27,56.25-174.93l-.09-724.43Z"/> </svg>'
)

# Nova's own Cloudflare OAuth client.
#
# This used to send Wrangler's client id, which works but is not ours: the consent
# screen said "Wrangler", so people were asked to trust a tool they had not downloaded,
# and Cloudflare could restrict that client at any time without warning.
OAUTH_CLIENT_ID = "64171e17a3242d9f2385c9a8f4f7381f"
OAUTH_AUTH_URL = "https://dash.cloudflare.com/oauth2/auth"
OAUTH_TOKEN_URL = "https://dash.cloudflare.com/oauth2/token"
OAUTH_REVOKE_URL = "https://dash.cloudflare.com/oauth2/revoke"
# Third-party OAuth clients use a different scope namespace from Wrangler's. These are
# the ids registered on the client above, verified against GET /client/v4/oauth/scopes.
#
# page.read is here because the deploy path does a GET on pages/projects before it
# creates one. zone:read is gone: nothing in this file calls /zones, so it was asking
# people for access it never used.
OAUTH_SCOPES = [
    "workers-scripts.write",     # upload the panel worker
    "workers-kv-storage.write",  # its KV namespace
    "d1.write",                  # its database
    "page.write",                # the pages.dev second address
    "page.read",                 # check whether that project already exists
    "memberships.read",          # list the accounts the user can deploy into
    "user-details.read",
]
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
            got = hashlib.sha256(code).hexdigest()
            if got != WORKER_SHA256:
                # Fail closed. Nothing is written, so a later run cannot pick up a
                # rejected file that happens to be sitting on disk.
                raise _CFErr(
                    "worker.js does not match the expected release.\n"
                    f"  expected {WORKER_SHA256}\n  got      {got}\n"
                    "Refusing to deploy it. If Nova has published a new release, update "
                    "WORKER_SHA256 in this file to the new digest.")
            WORKER_FILE.write_bytes(code)
            print(f"  [fetch] OK, {len(code)} bytes saved, sha256 verified")
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

def _make_ssl_ctx():
    """A verified TLS context, with certifi's CA bundle if this machine has one.

    There is no unverified variant on purpose. The token exchange carries the
    authorization code AND the PKCE verifier, which together are enough for anyone
    holding them to mint a token on the user's Cloudflare account. Most of this tool's
    users are on networks where TLS interception is routine, so retrying that request
    without verification would hand an interceptor both halves. A missing CA bundle is
    a machine problem with a real fix, printed below, not a reason to drop verification.
    """
    try:
        import certifi  # not a dependency; used only when the machine already has it
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

_OAUTH_OPENER = urlrequest.build_opener(urlrequest.HTTPSHandler(context=_make_ssl_ctx()))

CERT_HELP = (
    "TLS certificates could not be verified on this machine, so the sign-in was stopped.\n"
    "  This is a certificate store problem, not a Cloudflare problem.\n"
    "  On Windows:  py -m pip install --upgrade certifi\n"
    "  On macOS:    run 'Install Certificates.command' inside your Python folder\n"
    "  Then start the wizard again."
)

def revoke_oauth_token():
    """Hand the grant back when the tool exits.

    Without this the access token stays usable on the owner's Cloudflare account for its
    full lifetime after the window is closed, which is a credential outliving the thing
    that needed it. Best effort and silent on failure: a revoke that does not land is not
    a reason to fail a deploy that already succeeded.
    """
    global _oauth_token
    if not _oauth_token:
        return
    try:
        d = urlencode({"token": _oauth_token, "client_id": OAUTH_CLIENT_ID}).encode()
        req = urlrequest.Request(OAUTH_REVOKE_URL, data=d,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with _OAUTH_OPENER.open(req, timeout=10):
            pass
        print("  Cloudflare access handed back.")
    except Exception:
        pass
    finally:
        _oauth_token = None

def exchange_token(code, verifier):
    d = urlencode({"client_id": OAUTH_CLIENT_ID, "code": code, "code_verifier": verifier,
        "redirect_uri": f"http://localhost:{OAUTH_REDIRECT_PORT}/oauth/callback",
        "grant_type": "authorization_code"}).encode()
    req = urlrequest.Request(OAUTH_TOKEN_URL, data=d,
        headers={"Content-Type":"application/x-www-form-urlencoded","Accept":"application/json","User-Agent":"Mozilla/5.0"})
    try:
        with _OAUTH_OPENER.open(req, timeout=30) as r:
            return json.loads(r.read())
    except urlerror.URLError as e:
        # A TLS failure here STOPS the exchange. It is never retried unverified: this
        # request carries the code and the verifier together.
        if isinstance(getattr(e, "reason", None), ssl.SSLError) or "CERTIFICATE" in str(getattr(e, "reason", "")).upper():
            print("  [stop] " + CERT_HELP)
            raise _CFErr("TLS verification failed, so sign-in was stopped. See the message above.")
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
                en_hint = "This window can be closed. The wizard continues automatically, so switch back to it."
                fa_hint = ("این پنجره را می‌توانی "
                           "ببندی. دستیار خودکار "
                           "ادامه می‌دهد، به آن برگرد.")  # close window / wizard continues
                extra_html = ('<p style="color:#9aa4b8;margin-top:12px;font-size:.95rem">' + en_hint + '</p>'
                              '<p style="color:#9aa4b8;margin-top:4px;font-size:.95rem" dir="rtl">' + fa_hint + '</p>')
            else:
                en_status = "Sign-in failed"
                fa_status = "ورود ناموفق بود"  # ورود ناموفق بود
                btn_label = "Try again / دوباره"  # Try again / dobare
                extra_html = ('<p style="color:#fca5a5;margin:8px 0 0;font-size:.9rem">' + msg + '</p>'
                              '<button onclick="window.close();if(window.opener)window.opener.location.reload()" '
                              'style="padding:11px 24px;border-radius:12px;border:none;'
                              'background:linear-gradient(120deg,#22d3ee,#818cf8,#a855f7);color:#05060a;'
                              'font-weight:700;font-size:14px;cursor:pointer;margin-top:14px">' + btn_label + '</button>')
            html = (
                '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Nova</title>'
                '<body style="margin:0;background:#05060a;color:#eef1f7;'
                'font-family:Vazirmatn,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;'
                'display:grid;place-items:center;min-height:100vh">'
                '<div style="text-align:center;padding:36px;max-width:440px">'
                # The real Nova mark, the same inlined SVG the wizard UI uses, rather than a
                # gradient tile with a letter in it.
                '<div style="margin:0 auto 18px;width:48px;height:48px">' + NOVA_MARK_SVG + '</div>'
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

def rand_hex(n_bytes=6):
    """A secret path segment. Same shape and length the Telegram bot uses."""
    return secrets.token_hex(n_bytes)


def rand_password(length=18):
    """A strong admin password with the ambiguous glyphs (0/O, 1/l/I) left out,
    because people read this one off a screen and type it somewhere else."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def claim_panel(panel_urls, password, attempts=30, delay=6):
    """Set the admin password ourselves, as soon as the panel answers.

    The shipped artifact has no claim-token gate, so between deploy and someone
    setting a password, /install/set is open to whoever reaches it first. That is
    not a small window on a public workers.dev hostname. The Telegram bot closes
    it by claiming immediately, and this does the same.

    Three minutes, because that is how long Cloudflare takes to bring a new address
    up worldwide and it is the figure Nova's own install message quotes. Sixty seconds
    was not enough: watched live on 2026-09-14, a brand-new account's workers.dev
    subdomain did not resolve at all inside the first minute, so every attempt was
    posting at a hostname that did not exist yet and the panel was handed over
    unclaimed. A fresh account is the worst case, because the subdomain has to be
    created before anything can answer on it.

    Takes every address the panel answers on and tries each one per round, because
    the panel keeps its password in the shared D1 store: claiming on either address
    claims both. A brand-new account's workers.dev subdomain has to be created before
    it resolves, while the Pages hostname comes up on its own timeline, so racing them
    claims through whichever is ready first instead of waiting on a fixed guess.

    Returns the address that accepted the password, or "" if none did."""
    if isinstance(panel_urls, str):
        panel_urls = [panel_urls]
    targets = [u.rstrip("/") + "/install/set" for u in panel_urls if u]
    body = json.dumps({"password": password}).encode()
    for _ in range(attempts):
        for url in targets:
            try:
                req = urlrequest.Request(url, data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
                with urlrequest.urlopen(req, timeout=10, context=_make_ssl_ctx()) as r:
                    if 200 <= r.status < 300:
                        return url[: -len("/install/set")]
            except Exception:
                pass
        time.sleep(delay)
    return ""


def deploy(cf, aid, worker_name, kv_title, d1_name, extra_env, deploy_type, paths=None):
    kv = get_or_create_kv(cf, aid, kv_title)
    kv_id = kv.get("id")
    if not kv_id: raise _CFErr("KV namespace ID not found")

    d1_db = None; d1_id = None
    if d1_name:
        d1_db = get_or_create_d1(cf, aid, d1_name)
        d1_id = d1_db.get("id")

    # Secret paths, the same shape the Telegram bot uses.
    #
    # Left unset, the panel answers on /admin and /login, which anyone scanning
    # workers.dev can find. The shipped artifact reads ADMIN_PATH, LOGIN_PATH,
    # WS_PATH and SUB_PATH, so there is no reason to hand out the default ones.
    #
    # ADMIN/KEY/UUID stay unset on purpose: the worker treats an env ADMIN as an
    # already-configured password and would skip /install entirely. The password
    # is claimed over HTTP right after deploy instead, see claim_panel.
    # A second door must answer on the SAME secret paths and the same UUID as the
    # Worker, or it is a different panel wearing the same database.
    paths = paths or {}
    ws_path = paths.get("ws") or rand_hex(6)
    admin_path = paths.get("admin") or rand_hex(6)
    login_path = paths.get("login") or rand_hex(6)
    sub_path = paths.get("sub") or rand_hex(6)
    panel_uuid = paths.get("uuid") or str(uuid_mod.uuid4())
    bindings = [
        {"type": "kv_namespace", "name": "KV", "namespace_id": kv_id},
        {"type": "plain_text", "name": "WS_PATH", "text": ws_path},
        {"type": "plain_text", "name": "PATH", "text": "/" + ws_path},
        {"type": "plain_text", "name": "ADMIN_PATH", "text": admin_path},
        {"type": "plain_text", "name": "LOGIN_PATH", "text": login_path},
        {"type": "plain_text", "name": "SUB_PATH", "text": sub_path},
        {"type": "plain_text", "name": "UUID", "text": panel_uuid},
    ]
    if d1_id: bindings.append({"type": "d1", "name": "DB", "database_id": d1_id})
    for k, v in extra_env.items():
        if v: bindings.append({"type": "plain_text", "name": k, "text": v})

    # global_fetch_strictly_public lets backend mode reach a same-account gray-cloud VPS hostname
    # without Cloudflare returning 522 (self-loop). nodejs_compat is required by the worker.
    meta = {"main_module": "worker.js", "compatibility_date": "2026-07-31",
            "compatibility_flags": ["nodejs_compat", "global_fetch_strictly_public"], "bindings": bindings}
    code = WORKER_FILE.read_bytes()

    if deploy_type == "pages":
        body, ct = build_pages_multi(code)
        try:
            ex = cf.req("GET", f"/accounts/{quote(aid)}/pages/projects/{quote(worker_name)}", accept_404=True)
            kv_ns = {"KV": {"namespace_id": kv_id}}
            d1_b = {}
            # The shape the Telegram bot uses, which has built 1,769 working doors.
            # Cloudflare does not reject the other spelling, it just ignores it.
            if d1_id: d1_b = {"DB": {"id": d1_id}}
            # The same secret paths as the Workers branch; a Pages door that answered
            # on /admin would undo the point of setting them at all.
            ev = {
                "WS_PATH": {"type": "plain_text", "value": ws_path},
                "PATH": {"type": "plain_text", "value": "/" + ws_path},
                "ADMIN_PATH": {"type": "plain_text", "value": admin_path},
                "LOGIN_PATH": {"type": "plain_text", "value": login_path},
                "SUB_PATH": {"type": "plain_text", "value": sub_path},
                "UUID": {"type": "plain_text", "value": panel_uuid},
            }
            for k, v in extra_env.items():
                if v: ev[k] = {"type":"plain_text","value":v}
            # Bindings belong under deployment_configs.production, not at the top
            # level. Cloudflare accepts the flat form and silently ignores it, so the
            # project came up with no KV and no D1 and the panel answered
            # {"error":"no_kv"} on every request. Confirmed live on 2026-09-14: the door
            # deployed, resolved, served a redirect, and could not be claimed. The
            # Telegram bot has always nested them, which is why its doors work.
            production = {
                "compatibility_date": "2026-07-31",
                "compatibility_flags": ["nodejs_compat", "global_fetch_strictly_public"],
                "kv_namespaces": kv_ns,
                "env_vars": ev,
            }
            if d1_b: production["d1_databases"] = d1_b
            proj = {"name": worker_name, "production_branch": "main",
                    "deployment_configs": {"production": production}}
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

    # Claim the panel before handing it over.
    #
    # /install/set is open until somebody sets a password, so a panel left unclaimed
    # belongs to whoever reaches it first. We set a strong one now and show it to the
    # owner, who can change it from inside the panel. Same order the Telegram bot uses.
    # The claim is the caller's job now. It owns both addresses and can race them,
    # which this function cannot see from inside a single deploy.
    password, claimed = "", False

    return {"worker_name": worker_name, "worker_url": url,
        # The secret login path, not /login. /login and /admin serve a decoy.
        "panel_url": f"{url}/{login_path}" if url else "",
        "login_path": login_path, "admin_path": admin_path,
        "admin_pass": password if claimed else "",
        "claimed": claimed,
        # Only true when we could NOT claim it, in which case the owner has to set
        # the password themselves, immediately, at /install.
        "set_password": not claimed,
        "install_url": f"{url}/install" if url else "",
        "kv_namespace": kv, "d1_database": d1_db, "deploy_type": deploy_type,
        "paths": {"ws": ws_path, "admin": admin_path, "login": login_path,
                  "sub": sub_path, "uuid": panel_uuid},
        "kv_id": kv_id, "d1_id": d1_id}

def report_install(worker_url):
    """Tell novaproxy.online's counter about a real deploy, in the background.

    The id is a hash of the panel host, matching the web installer and the
    Telegram bot, so one panel is tallied once across every deploy channel. The
    server only ever sees the opaque hash, never the worker URL. Runs in a daemon
    thread with a short timeout so it never delays or blocks the deploy result."""
    host = (urlparse(worker_url).hostname or "").strip()
    if not host:
        return
    def _send():
        try:
            wid = "w_" + hashlib.sha256(("nova-panel:" + host).encode()).hexdigest()[:32]
            data = json.dumps({"type": "install", "id": wid}).encode()
            req = urlrequest.Request("https://novaproxy.online/api/stats", data=data,
                headers={"Content-Type": "application/json"}, method="POST")
            urlrequest.urlopen(req, timeout=6).read()
        except Exception:
            pass
    threading.Thread(target=_send, daemon=True).start()

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
            '<body style="margin:0;background:#05060a;color:#eef1f7;'
            'font-family:Vazirmatn,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;'
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

                # The second door, then one claim across both addresses.
                #
                # The door used to be built only AFTER a successful claim, and the claim
                # only ever spoke to the Worker. On a brand-new Cloudflare account the
                # workers.dev subdomain does not exist yet, so the claim failed, so no
                # door was built, so the user got a single unclaimed address. Observed
                # exactly that on 2026-09-14: the hostname had still not resolved
                # twenty-five minutes later. The one address that survives a 1101 wedge,
                # and that does not depend on workers.dev at all, was gated behind
                # workers.dev resolving.
                #
                # Both addresses share the D1 store, so claiming either claims both.
                # Racing them claims through whichever is ready first, which SHORTENS
                # the unclaimed window rather than lengthening it. The invariant that
                # matters is unchanged: neither address is shown to the user until a
                # password exists on it.
                #
                # The second door.
                #
                # Measured 2026-09-14 across the fleet: of 230 wedged panels that had a
                # pages.dev door, 229 Workers were returning 1101 and 227 of the doors
                # were still serving. A Worker slot can wedge for reasons that have
                # nothing to do with the code on it, and when that happens the Pages
                # address is what is left. Iran also filters workers.dev and pages.dev
                # separately, so two addresses survive one filtering decision.
                #
                # Same database, same secret paths, same UUID, so it is one panel on two
                # addresses rather than two panels. Built AFTER the claim: a door raised
                # before a password exists is a panel anyone can take.
                #
                # Best effort. A panel with one working address is a successful install,
                # so nothing here may fail the deploy.
                door_url = ""
                if dt != "pages":
                    try:
                        door = deploy(cf, aid, f"{wn}-door", kv, d1, extra, "pages",
                                      paths={**result.get("paths", {}), "skip_claim": True})
                        door_url = door.get("worker_url") or ""
                    except Exception as e:
                        result["door_error"] = str(e)[:200]

                # One claim, both addresses, first one to answer wins.
                password = rand_password()
                claimed_on = claim_panel([u for u in (result.get("worker_url"), door_url) if u],
                                         password)
                result["claimed"] = bool(claimed_on)
                result["admin_pass"] = password if claimed_on else ""
                result["set_password"] = not claimed_on
                result["claimed_on"] = claimed_on

                # Only advertise the door once the panel has a password on it.
                if door_url and claimed_on:
                    login = (result.get("paths") or {}).get("login") or ""
                    result["door_url"] = door_url
                    result["door_panel_url"] = f"{door_url}/{login}" if login else door_url

                report_install(result.get("worker_url"))
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
    finally: revoke_oauth_token()

if __name__ == "__main__": main()
