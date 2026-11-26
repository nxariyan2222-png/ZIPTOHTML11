#!/usr/bin/env python3
# 𝑴𝑨𝑻𝑹𝑰𝑿 𝑴𝑶𝑫𝑺 📈 — Ultimate Website-to-ZIP & ZIP-to-HTML Bot
# Features: Vite Chunks Support, React Router Fix, Full Asset Inlining.

import asyncio
import ipaddress
import json
import os
import re
import shutil
import socket
import tempfile
import time
import zipfile
import base64
import mimetypes
import html as html_lib
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag, unquote
from urllib.request import Request
from urllib.error import URLError
from html.parser import HTMLParser

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.request import HTTPXRequest
from telegram.error import TimedOut, NetworkError, BadRequest
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler

BRAND = "𝑴𝑨𝑻𝑹𝑰𝑿 𝑴𝑶𝑫𝑺 📈"

# --- ENVIRONMENT VARIABLES ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "8959111543"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1003636772569"))
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/MATRIX_TRADER777").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", "10000"))

MAX_FILES = 100
MAX_FILE_BYTES = 3 * 1024 * 1024
MAX_TOTAL_BYTES = 35 * 1024 * 1024
MAX_ZIP_BYTES = 45 * 1024 * 1024
TIMEOUT = 15
USER_COOLDOWN = 20
USER_AGENT = "Mozilla/5.0 (compatible; MatrixModsWebsiteBackup/2.0)"
last_request = {}

MAX_UPLOAD_ZIP = 19 * 1024 * 1024
MAX_UNPACKED_BYTES = 150 * 1024 * 1024

# ================= WEBSITE TO ZIP LOGIC (Unchanged) =================
class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for key in ("href", "src", "poster"):
            if a.get(key):
                self.links.append(a[key])
        if a.get("srcset"):
            for part in a["srcset"].split(","):
                candidate = part.strip().split()
                if candidate:
                    self.links.append(candidate[0])

def clean_name(value):
    value = re.sub(r"[^\w.-]+", "_", value.strip(), flags=re.UNICODE)
    return value.strip("._")[:55] or "website_backup"

def safe_public_url(url):
    url, _ = urldefrag(url.strip())
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    if p.username or p.password:
        return None
    try:
        port = p.port
        if port and not (1 <= port <= 65535):
            return None
        host = p.hostname.encode("idna").decode("ascii")
        addresses = socket.getaddrinfo(host, port or (443 if p.scheme == "https" else 80))
        if not addresses:
            return None
        for item in addresses:
            ip = ipaddress.ip_address(item[4][0])
            if not ip.is_global:
                return None
    except Exception:
        return None
    return p.geturl()

def fetch_public(url):
    session = requests.Session()
    current = url
    for _ in range(5):
        current = safe_public_url(current)
        if not current:
            raise ValueError("Blocked unsafe or non-public URL")
        response = session.get(
            current, headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT, stream=True, allow_redirects=False
        )
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("Invalid redirect")
            current = urljoin(current, location)
            continue
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                response.close()
                raise ValueError("File exceeds 3 MB limit")
            chunks.append(chunk)
        response.close()
        return b"".join(chunks), content_type, current
    raise ValueError("Too many redirects")

def local_path(url, host, content_type=""):
    """Preserve real asset extensions; extensionless URLs use their MIME type."""
    p = urlparse(url)
    path = unquote(p.path or "/")
    if path.endswith("/"):
        path += "index.html"
    suffix = Path(path).suffix.lower()
    if not suffix:
        mime = (content_type or "").split(";", 1)[0].strip().lower()
        guessed = mimetypes.guess_extension(mime) if mime else None
        # Normalize common MIME aliases to browser-friendly extensions.
        ext_map = {
            "text/html": ".html", "application/javascript": ".js",
            "text/javascript": ".js", "application/x-javascript": ".js",
            "text/css": ".css", "image/svg+xml": ".svg",
            "application/json": ".json", "font/woff2": ".woff2",
        }
        path += ext_map.get(mime, guessed or ".bin")
    parts = [x for x in path.split("/") if x not in ("", ".", "..")]
    if not parts:
        parts = ["index.html"]
    rel = Path(*parts)
    if p.query:
        suffix = re.sub(r"\W+", "_", p.query)[:30]
        rel = rel.with_name(rel.stem + "_" + suffix + rel.suffix)
    return Path(clean_name(host)) / rel

def build_zip(start_url, zip_base, work_dir):
    host = urlparse(start_url).netloc.lower()
    work = Path(work_dir) / (zip_base + "_files")
    zip_path = Path(work_dir) / (zip_base + ".zip")
    work.mkdir(parents=True, exist_ok=True)

    queue, seen, saved = [start_url], set(), []
    total_bytes = 0

    while queue and len(seen) < MAX_FILES and total_bytes < MAX_TOTAL_BYTES:
        url = queue.pop(0)
        url = safe_public_url(url)
        if not url or url in seen:
            continue
        if urlparse(url).netloc.lower() != host:
            continue
        seen.add(url)
        try:
            data, content_type, final_url = fetch_public(url)
            if not final_url or urlparse(final_url).netloc.lower() != host:
                continue
            if total_bytes + len(data) > MAX_TOTAL_BYTES:
                break
            target = work / local_path(url, host, content_type)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            saved.append(target)
            total_bytes += len(data)

            if "html" in content_type or urlparse(url).path.lower().endswith((".html", ".htm", "/")):
                parser = LinkParser()
                parser.feed(data.decode("utf-8", errors="replace"))
                for link in parser.links:
                    absolute = safe_public_url(urljoin(url, link))
                    if absolute and urlparse(absolute).netloc.lower() == host and absolute not in seen:
                        queue.append(absolute)

            if "css" in content_type or urlparse(url).path.lower().endswith(".css"):
                css = data.decode("utf-8", errors="replace")
                refs = re.findall(r"""url\(\s*['"]?([^'")]+)""", css, flags=re.I)
                refs += re.findall(r"""@import\s+['"]([^'"]+)""", css, flags=re.I)
                for ref in refs:
                    ref = ref.strip()
                    if ref.startswith(("data:", "javascript:", "#")):
                        continue
                    absolute = safe_public_url(urljoin(url, ref))
                    if absolute and urlparse(absolute).netloc.lower() == host and absolute not in seen:
                        queue.append(absolute)
        except Exception:
            continue
        time.sleep(0.03)

    if not saved:
        raise RuntimeError("No public files could be downloaded. The site may block requests.")

    readme = work / "MATRIX_MODS_INFO.txt"
    readme.write_text(f"{BRAND}\nWEBSITE TO ZIP BACKUP\n\nSource: {start_url}\nFiles: {len(saved)}\n", encoding="utf-8")
    
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in work.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(work))
    if zip_path.stat().st_size > MAX_ZIP_BYTES:
        raise RuntimeError("ZIP is too large for Telegram (45 MB limit).")
    return zip_path, len(saved)


# ================= ULTIMATE ZIP TO HTML LOGIC =================
def extract_project_zip(zip_path, root):
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        if len(zf.infolist()) > 5000:
            raise ValueError("ZIP mein 5000 se zyada files hain.")
        for item in zf.infolist():
            target = (root / item.filename.replace("\\", "/")).resolve()
            if target != root.resolve() and root.resolve() not in target.parents:
                raise ValueError("ZIP mein unsafe path mila.")
            total += item.file_size
            if total > MAX_UNPACKED_BYTES:
                raise ValueError("Extracted ZIP 150 MB se badi hai.")
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(item) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

def asset_data_uri(path):
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return "data:%s;base64,%s" % (mime, base64.b64encode(path.read_bytes()).decode("ascii"))

def local_asset(base_dir, value, root):
    value = (value or "").strip()
    if not value or value.startswith(("#", "data:", "blob:", "http:", "https:", "//", "javascript:", "mailto:")):
        return None
    value = value.split("#", 1)[0].split("?", 1)[0]
    decoded = unquote(value)
    if decoded.startswith("/"):
        decoded = decoded.lstrip("/")
        path = (root / decoded).resolve()
    else:
        path = (base_dir / decoded).resolve()
    return path if path.is_file() and (path == root or root in path.parents) else None

def resolve_iframe_html(base_dir, value, root):
    """Resolve iframe HTML, including versioned files such as panel_v_114.html
    when the page requests panel.html?v=114.
    """
    exact = local_asset(base_dir, value, root)
    if exact and exact.suffix.lower() in (".html", ".htm"):
        return exact

    requested = (value or "").strip().split("#", 1)[0].split("?", 1)[0]
    requested = unquote(requested).replace("\\", "/")
    basename = Path(requested.lstrip("/")).name
    stem = Path(basename).stem
    suffix = Path(basename).suffix.lower()
    if suffix not in (".html", ".htm"):
        return None

    # Common build output: panel.html?v=114 is shipped as panel_v_114.html.
    dirs = []
    if requested.startswith("/"):
        dirs.append((root / Path(requested.lstrip("/")).parent).resolve())
    else:
        dirs.append((base_dir / Path(requested).parent).resolve())
    dirs.append(root.resolve())

    candidates = []
    seen_dirs = set()
    for directory in dirs:
        if directory in seen_dirs or not directory.is_dir():
            continue
        seen_dirs.add(directory)
        candidates.extend(directory.glob(stem + "_v_*" + suffix))
        candidates.extend(directory.glob(stem + "-v-*" + suffix))
    if not candidates:
        # Allow nested ZIP layouts, but only matching versioned page names.
        candidates.extend(root.rglob(stem + "_v_*" + suffix))
        candidates.extend(root.rglob(stem + "-v-*" + suffix))
    candidates = [p.resolve() for p in candidates if p.is_file() and (p.resolve() == root.resolve() or root.resolve() in p.resolve().parents)]
    if not candidates:
        return None

    def version_key(path):
        nums = re.findall(r"\d+", path.stem)
        return tuple(int(n) for n in nums) if nums else (0,)
    return sorted(candidates, key=lambda p: (version_key(p), str(p).lower()), reverse=True)[0]

def inline_css_assets(css, css_path, root, seen=None):
    seen = seen or set()
    import_re = re.compile(r"""@import\s+(?:url\()?["']?([^"')\s;]+)["']?\)?\s*;""", re.I)
    def replace_import(match):
        asset = local_asset(css_path.parent, match.group(1), root)
        if not asset or asset.suffix.lower() != ".css" or asset in seen:
            return match.group(0)
        seen.add(asset)
        return inline_css_assets(asset.read_text("utf-8", errors="replace"), asset, root, seen)
    css = import_re.sub(replace_import, css)
    url_re = re.compile(r"""url\(\s*(["']?)(.*?)\1\s*\)""", re.I)
    def replace_url(match):
        asset = local_asset(css_path.parent, match.group(2), root)
        return "url('%s')" % asset_data_uri(asset) if asset else match.group(0)
    return url_re.sub(replace_url, css)

class SingleHTMLInliner(HTMLParser):
    def __init__(self, source, entry, root, depth=0, asset_map=None, import_map=None):
        super().__init__(convert_charrefs=False)
        self.entry, self.root, self.output = entry, root, []
        self.depth = depth
        self.asset_map = asset_map or {}
        self.import_map = import_map or {}
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        tag_lower = tag.lower()
        
        # Recursively inline local iframe HTML (for example /panel.html).
        # A depth limit prevents circular iframe references.
        if tag_lower == "iframe" and values.get("src") and self.depth < 8:
            iframe_page = resolve_iframe_html(self.entry.parent, values["src"], self.root)
            if iframe_page and iframe_page.suffix.lower() in (".html", ".htm"):
                try:
                    child = SingleHTMLInliner(
                        iframe_page.read_text("utf-8", errors="replace"),
                        iframe_page, self.root, self.depth + 1,
                        self.asset_map, self.import_map,
                    )
                    child_html = "".join(child.output)
                    bridge = "<script>window.__ASSET_MAP__=" + json.dumps(self.asset_map) + r""";
(function(){var f=window.fetch;window.fetch=function(i,o){var u=typeof i==="string"?i:i.url;return f.call(this,window.__ASSET_MAP__[u]||u,o)};
var x=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u){arguments[1]=window.__ASSET_MAP__[u]||u;return x.apply(this,arguments)};})();</script>"""
                    if self.import_map:
                        bridge = '<script type="importmap">' + json.dumps({"imports": self.import_map}) + "</script>" + bridge
                    if re.search(r"</head\\s*>", child_html, re.I):
                        child_html = re.sub(r"</head\\s*>", lambda m: bridge + m.group(0), child_html, count=1, flags=re.I)
                    else:
                        child_html = "<head>" + bridge + "</head>" + child_html
                    values.pop("src", None)
                    values["srcdoc"] = child_html
                except Exception:
                    pass

        # Ignore Vite modulepreload to stop missing asset errors
        if tag_lower == "link" and values.get("rel") == "modulepreload":
            return

        if tag_lower == "link" and values.get("href"):
            asset = local_asset(self.entry.parent, values["href"], self.root)
            if asset and "stylesheet" in values.get("rel", "").lower() and asset.suffix.lower() == ".css":
                css = inline_css_assets(asset.read_text("utf-8", errors="replace"), asset, self.root)
                self.output.append("<style>\n" + css + "\n</style>")
                return
            if asset and "icon" in values.get("rel", "").lower():
                values["href"] = asset_data_uri(asset)
                
        if tag_lower == "script" and values.get("src"):
            asset = local_asset(self.entry.parent, values["src"], self.root)
            if asset and asset.suffix.lower() in (".js", ".mjs"):
                # Preserve attributes like type="module"
                attrs_text = "".join(
                    ' %s="%s"' % (k, v)
                    for k, v in attrs
                    if k.lower() not in ("src", "defer", "async", "crossorigin", "integrity")
                    and v is not None
                )
                js_text = asset.read_text("utf-8", errors="replace")
                js_text = re.sub(r"</script", r"<\\/script", js_text, flags=re.I)
                self.output.append("<script%s>\n%s\n</script>" % (attrs_text, js_text))
                return
                
        for attr in ("src", "poster"):
            if values.get(attr):
                asset = local_asset(self.entry.parent, values[attr], self.root)
                if asset:
                    values[attr] = asset_data_uri(asset)
                    
        if values.get("srcset"):
            candidates = []
            for part in values["srcset"].split(","):
                bits = part.strip().split()
                if bits:
                    asset = local_asset(self.entry.parent, bits[0], self.root)
                    if asset:
                        bits[0] = asset_data_uri(asset)
                    candidates.append(" ".join(bits))
            values["srcset"] = ", ".join(candidates)
            
        attrs_text = "".join(
            (' %s="%s"' % (k, (v or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")))
            if v is not None else " " + k for k, v in values.items()
        )
        self.output.append("<%s%s>" % (tag, attrs_text))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.output.append("</%s>" % tag)
    def handle_endtag(self, tag): self.output.append("</%s>" % tag)
    def handle_data(self, data): self.output.append(data)
    def handle_entityref(self, name): self.output.append("&%s;" % name)
    def handle_charref(self, name): self.output.append("&#%s;" % name)
    def handle_comment(self, data): self.output.append("<!--%s-->" % data)

def convert_project_zip(zip_path, output_path):
    with tempfile.TemporaryDirectory(prefix="matrix_html_") as td:
        root = Path(td) / "project"
        root.mkdir()
        extract_project_zip(zip_path, root)
        
        # 1. Build Asset Map for Vite chunks & dynamic assets
        asset_map = {}
        import_map = {}
        for file_path in root.rglob("*"):
            if not file_path.is_file(): continue
            rel_path = file_path.relative_to(root).as_posix()
            if rel_path.endswith((".html", ".htm")): continue
            
            b64 = base64.b64encode(file_path.read_bytes()).decode("ascii")
            mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            data_uri = f"data:{mime};base64,{b64}"
            
            # Map all possible path variations
            for p in [rel_path, "/" + rel_path, "./" + rel_path, file_path.name, "./" + file_path.name]:
                asset_map[p] = data_uri
                
            # Register JS in ImportMap for dynamic Vite imports
            if rel_path.endswith((".js", ".mjs")):
                js_uri = f"data:text/javascript;base64,{b64}"
                for p in [rel_path, "/" + rel_path, "./" + rel_path, file_path.name, "./" + file_path.name]:
                    import_map[p] = js_uri
                    
        import_map_tag = f'<script type="importmap">\n{json.dumps({"imports": import_map})}\n</script>\n' if import_map else ""

        all_html = list(root.rglob("*.html")) + list(root.rglob("*.htm"))
        if not all_html:
            raise ValueError("ZIP mein koi HTML page nahi mila.")
            
        def entry_rank(p):
            n = p.name.lower()
            # If the project contains a versioned panel page, emit that page itself
            # as the final single HTML (not index.html with the panel in an iframe).
            panel_versioned = re.match(r"^panel_v_(\d+)\.(?:html|htm)$", n)
            if panel_versioned:
                return (0, -int(panel_versioned.group(1)), len(p.relative_to(root).parts), str(p).lower())
            priority = 1 if n == "index.html" else 2 if n == "index.htm" else 3 if n == "main.html" else 4
            return (priority, 0, len(p.relative_to(root).parts), str(p).lower())

        all_html.sort(key=entry_rank)
        entry = all_html[0]
        site_root = root.resolve()
        
        parser = SingleHTMLInliner(entry.read_text("utf-8", errors="replace"), entry, site_root, asset_map=asset_map, import_map=import_map)
        result = "".join(parser.output)
        
        # 2. Ultimate Router & Fetch Hijack (Works in Chrome & Telegram)
        routing_fix = f"""
        <script>
        window.__ASSET_MAP__ = {json.dumps(asset_map)};
        (function() {{
            // Override fetch to serve local assets
            var origFetch = window.fetch;
            window.fetch = function(input, init) {{
                var url = typeof input === 'string' ? input : input.url;
                if (window.__ASSET_MAP__ && window.__ASSET_MAP__[url]) {{
                    return origFetch(window.__ASSET_MAP__[url], init);
                }}
                return origFetch.apply(this, arguments);
            }};
            
            // Override XHR
            var origXHR = window.XMLHttpRequest.prototype.open;
            window.XMLHttpRequest.prototype.open = function(method, url, async, user, password) {{
                if (window.__ASSET_MAP__ && window.__ASSET_MAP__[url]) {{
                    arguments[1] = window.__ASSET_MAP__[url];
                }}
                return origXHR.apply(this, arguments);
            }};

            // Override Image Src
            try {{
                var origImgSrc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, "src");
                if (origImgSrc && origImgSrc.set) {{
                    Object.defineProperty(HTMLImageElement.prototype, "src", {{
                        set: function(val) {{
                            if (window.__ASSET_MAP__ && window.__ASSET_MAP__[val]) {{
                                origImgSrc.set.call(this, window.__ASSET_MAP__[val]);
                            }} else {{
                                origImgSrc.set.call(this, val);
                            }}
                        }},
                        get: origImgSrc.get
                    }});
                }}
            }} catch(e) {{}}

            // SPA Router Hijack
            var isLocal = window.location.protocol === 'file:' || window.location.protocol === 'content:';
            if (isLocal) {{
                if (!window.location.hash) {{
                    try {{ window.location.hash = '#/'; }} catch(e) {{}}
                }}
                try {{
                    var loc = window.location;
                    var proto = Object.getPrototypeOf(loc);
                    var pathnameDesc = Object.getOwnPropertyDescriptor(proto, 'pathname');
                    if (pathnameDesc && pathnameDesc.get) {{
                        Object.defineProperty(proto, 'pathname', {{
                            configurable: true,
                            get: function() {{
                                var hash = window.location.hash;
                                if (hash && hash.startsWith('#')) {{
                                    var path = hash.substring(1);
                                    return path.startsWith('/') ? path : '/' + path;
                                }}
                                return '/';
                            }}
                        }});
                    }}
                }} catch (e) {{}}

                var origPush = history.pushState;
                var origReplace = history.replaceState;
                history.pushState = function(state, title, url) {{
                    if (url && url.startsWith('/') && !url.startsWith('//')) {{
                        window.location.hash = url;
                        window.dispatchEvent(new PopStateEvent('popstate', {{ state: state }}));
                    }} else {{
                        origPush.apply(history, arguments);
                    }}
                }};
                history.replaceState = function(state, title, url) {{
                    if (url && url.startsWith('/') && !url.startsWith('//')) {{
                        var newUrl = window.location.protocol + "//" + window.location.host + window.location.pathname + window.location.search + '#' + url;
                        try {{ origReplace.call(history, state, title, newUrl); }} catch(e) {{}}
                    }} else {{
                        origReplace.apply(history, arguments);
                    }}
                }};
                window.addEventListener('hashchange', function() {{
                    window.dispatchEvent(new PopStateEvent('popstate', {{ state: null }}));
                }});
                document.addEventListener('click', function(e) {{
                    var a = e.target.closest('a');
                    if (a) {{
                        var href = a.getAttribute('href');
                        if (href && href.startsWith('/') && !href.startsWith('//')) {{
                            e.preventDefault();
                            window.location.hash = href;
                        }}
                    }}
                }}, true);
            }}
        }})();
        </script>
        <base href="./">
        """
        
        head_injection = import_map_tag + routing_fix
        
        if re.search(r"<head\s*>", result, re.I):
            result = re.sub(r"<head\s*>", lambda m: "<head>\n" + head_injection, result, count=1, flags=re.I)
        elif re.search(r"<html\s*>", result, re.I):
            result = re.sub(r"<html\s*>", lambda m: "<html>\n<head>\n" + head_injection + "</head>", result, count=1, flags=re.I)
        else:
            result = head_injection + result

        marker = '<script>window.MATRIX_KEY = "MATRIX";</script>'
        if re.search(r"</head\s*>", result, re.I):
            result = re.sub(r"</head\s*>", lambda m: marker + "\n</head>", result, count=1, flags=re.I)
        else:
            result = marker + result
            
        Path(output_path).write_text(result, encoding="utf-8")


# ================= BOT HANDLERS & FORCE JOIN =================
def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Link → ZIP", callback_data="feature_zip"),
         InlineKeyboardButton("📦 ZIP → HTML", callback_data="feature_html")],
        [InlineKeyboardButton("⚡ Link → HTML", callback_data="feature_link_html")],
        [InlineKeyboardButton("📖 How to use", callback_data="help"),
         InlineKeyboardButton("👑 Owner", callback_data="owner")],
        [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)],
    ])

def link_choice_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐  WEBSITE → ZIP", callback_data="choose_link_zip")],
        [InlineKeyboardButton("⚡  WEBSITE → SINGLE HTML", callback_data="choose_link_html")],
        [InlineKeyboardButton("✖  Cancel", callback_data="cancel_link")],
    ])

def join_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 JOIN CHANNEL", url=CHANNEL_LINK)],
        [InlineKeyboardButton("✅ I HAVE JOINED — VERIFY", callback_data="verify_join")],
    ])

async def is_member(bot, user_id):
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ("creator", "administrator", "member")
    except BadRequest as e:
        if "user not found" in str(e).lower() or "participant" in str(e).lower():
            return False
        print(f"BadRequest in membership: {e}")
        return None
    except Exception as exc:
        print(f"Membership check failed: {exc}")
        return None

async def require_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return False
    
    status = await is_member(context.bot, user.id)
    
    if status is True:
        return True
    elif status is None:
        error_text = (
            "⚠️ <b>Verification Error!</b>\n\n"
            "Bot aapka channel membership check nahi kar pa raha. Iska matlab hai ki Bot channel mein "
            "<b>Admin</b> nahi hai.\n\nOwner se kahein ki wo Bot ko channel mein Admin banayein."
        )
        if update.callback_query:
            await update.callback_query.answer("Error: Bot needs Admin rights.", show_alert=True)
            await update.callback_query.message.reply_text(error_text, parse_mode="HTML")
        elif update.effective_message:
            await update.effective_message.reply_text(error_text, parse_mode="HTML")
        return False

    text = (
        f"🔒 <b>CHANNEL MEMBERSHIP REQUIRED</b>\n\n"
        f"Bot use karne se pehle hamara channel join karein.\n"
        f"Join karne ke baad <b>I HAVE JOINED — VERIFY</b> dabayein."
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, reply_markup=join_keyboard(), parse_mode="HTML")
    elif update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=join_keyboard(), parse_mode="HTML")
    return False

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    data = query.data or ""
    
    if data == "verify_join":
        status = await is_member(context.bot, query.from_user.id)
        if status is True:
            await query.answer("Membership verified! Welcome.", show_alert=True)
            await query.message.reply_text(
                f"✨ <b>ACCESS GRANTED</b>\n\nWelcome to {BRAND}!",
                reply_markup=main_keyboard(), parse_mode="HTML"
            )
        elif status is None:
             await query.answer("Error: Bot needs Admin rights in channel.", show_alert=True)
        else:
            await query.answer("Abhi channel membership detect nahi hui. Join karke dobara try karein.", show_alert=True)
            await query.message.reply_text("🔒 Pehle channel join karein, phir verify dabayein.", reply_markup=join_keyboard())
        return

    if not await require_membership(update, context):
        return

    if data == "help":
        await query.answer()
        await query.message.reply_text("📚 <b>HOW TO USE</b>\n\n🌐 Website link bhejein — bot pehle task select karwayega.\n📦 ZIP → HTML: .zip document upload karein.\n⚡ Link → HTML: link select menu se choose karein.", reply_markup=main_keyboard(), parse_mode="HTML")
    elif data == "feature_zip":
        await query.answer()
        await query.message.reply_text("🌐 <b>WEBSITE BACKUP</b>\nWebsite URL bhejiye. Link milne ke baad aap task choose kar sakte hain.", reply_markup=main_keyboard(), parse_mode="HTML")
    elif data == "feature_html":
        await query.answer()
        await query.message.reply_text("📦 <b>ZIP → SINGLE HTML</b>\nApni website ki .zip file document ke roop mein upload karein.", reply_markup=main_keyboard(), parse_mode="HTML")
    elif data == "feature_link_html":
        await query.answer()
        await query.message.reply_text("⚡ <b>LINK → SINGLE HTML</b>\nWebsite URL bhejiye, phir menu se Single HTML choose karein.", reply_markup=main_keyboard(), parse_mode="HTML")
    elif data in ("choose_link_zip", "choose_link_html"):
        await query.answer("Task selected")
        start_url = context.user_data.pop("pending_url", None)
        if not start_url:
            await query.message.reply_text("⌛ Link session expire ho gaya. Website URL dobara bhejein.", reply_markup=main_keyboard())
            return
        mode = "link_html" if data == "choose_link_html" else "zip"
        await process_link_job(context, query.message, query.from_user, start_url, mode)
    elif data == "cancel_link":
        context.user_data.pop("pending_url", None)
        await query.answer("Cancelled")
        await query.message.reply_text("Task cancelled. Naya website link bhej sakte hain.", reply_markup=main_keyboard())
    elif data == "owner":
        await query.answer()
        await query.message.reply_text("👑 <b>BOT OWNER</b>\nSupport ke liye channel join karein.", reply_markup=main_keyboard(), parse_mode="HTML")

async def owner_log(context, user, operation, details, result_path=None, result_name=None):
    """Send a usage audit to the configured owner; include file result when present."""
    if not user:
        return
    username = f"@{user.username}" if user.username else "(no username)"
    report = (
        f"📥 MATRIX BOT USAGE\n"
        f"User: {user.full_name}\n"
        f"Username: {username}\n"
        f"Telegram ID: {user.id}\n"
        f"Operation: {operation}\n"
        f"Details: {details}"
    )
    try:
        await context.bot.send_message(chat_id=OWNER_ID, text=report)
        if result_path and Path(result_path).is_file():
            with Path(result_path).open("rb") as file_obj:
                await context.bot.send_document(
                    chat_id=OWNER_ID,
                    document=file_obj,
                    filename=result_name or Path(result_path).name,
                    caption=f"Owner copy • user ID {user.id} • {operation}",
                )
    except Exception as exc:
        # Logging must never break the user's conversion; expose owner-delivery issues.
        print(f"Owner report delivery failed for user {user.id}: {exc}")

async def process_link_job(context, message, user, start_url, mode):
    """Run the chosen link conversion and keep a polished live status visible."""
    now = time.time()
    if now - last_request.get(user.id, 0) < USER_COOLDOWN:
        await message.reply_text("⏳ Please 20 seconds wait karke dobara try karein.")
        return
    last_request[user.id] = now

    operation = "LINK → HTML" if mode == "link_html" else "LINK → ZIP"
    await owner_log(context, user, operation, f"URL: {start_url}\nStatus: started")
    status = await message.reply_text(
        f"✦ <b>MATRIX PREMIUM</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"🔄 <b>{operation} PROCESSING</b>\n"
        "⠋ Connecting to website...\n"
        "Please keep this chat open.", parse_mode="HTML"
    )
    await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.UPLOAD_DOCUMENT)

    async def worker(temp):
        zip_base = clean_name(urlparse(start_url).netloc.replace(":", "_") + "_backup")
        zip_path, count = await asyncio.to_thread(build_zip, start_url, zip_base, temp)
        if mode == "link_html":
            html_path = Path(temp) / "MATRIX_SINGLE.html"
            await asyncio.to_thread(convert_project_zip, zip_path, html_path)
            if html_path.stat().st_size > 49 * 1024 * 1024:
                raise ValueError("Generated HTML Telegram send limit se badi hai.")
            with html_path.open("rb") as f:
                await context.bot.send_document(
                    chat_id=message.chat_id, document=f,
                    filename="MATRIX_SINGLE.html",
                    caption=f"✨ MATRIX PREMIUM\n✅ SINGLE HTML READY\n🌐 {start_url}\n📁 Source files: {count}",
            )
            await owner_log(context, user, operation, f"URL: {start_url}\nFiles: {count}\nStatus: success", html_path, "MATRIX_SINGLE.html")
        else:
            with zip_path.open("rb") as f:
                await context.bot.send_document(
                    chat_id=message.chat_id, document=f,
                    filename=f"{zip_base}_MATRIX_MODS.zip",
                    caption=f"✨ MATRIX PREMIUM\n✅ WEBSITE ZIP READY\n🌐 {start_url}\n📁 Files: {count}",
            )
            await owner_log(context, user, operation, f"URL: {start_url}\nFiles: {count}\nStatus: success", zip_path, f"{zip_base}_MATRIX_MODS.zip")
        return count

    with tempfile.TemporaryDirectory(prefix="matrixmods_") as temp:
        job = asyncio.create_task(worker(temp))
        frames = [
            "⠋ Connecting to website...",
            "⠙ Scanning pages and assets...",
            "⠹ Downloading website files...",
            "⠸ Building your output...",
            "⠼ Finalizing and preparing file...",
            "⠴ Almost ready...",
        ]
        frame = 0
        try:
            while not job.done():
                try:
                    await asyncio.wait_for(asyncio.shield(job), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                if not job.done():
                    try:
                        await status.edit_text(
                            f"✦ <b>MATRIX PREMIUM</b>\n━━━━━━━━━━━━━━━━━━\n"
                            f"🔄 <b>{operation} PROCESSING</b>\n"
                            f"{frames[frame % len(frames)]}\n\n"
                            "▰▰▰▰▱  <i>Working securely</i>", parse_mode="HTML"
                        )
                    except Exception:
                        pass
                    frame += 1
            count = await job
            try:
                await status.edit_text(
                    f"✨ <b>MATRIX PREMIUM</b>\n━━━━━━━━━━━━━━━━━━\n"
                    f"✅ <b>COMPLETED SUCCESSFULLY</b>\n"
                    f"📁 Files processed: {count}\n"
                    "Your file is delivered in this chat.", parse_mode="HTML"
                )
            except Exception:
                pass
        except Exception as exc:
            if not job.done():
                job.cancel()
            await status.edit_text(f"⚠️ <b>{operation} FAILED</b>\nReason: {str(exc)[:300]}", parse_mode="HTML")
            await owner_log(context, user, operation, f"URL: {start_url}\nStatus: failed\nError: {str(exc)[:300]}")

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    if not user or not message or not message.text:
        return
    if not await require_membership(update, context):
        return

    raw = message.text.strip()
    if len(raw) > 2048:
        await message.reply_text("❌ URL bahut lamba hai.")
        return
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    start_url = safe_public_url(raw)
    if not start_url:
        await message.reply_text("❌ Invalid ya non-public website URL.")
        return

    context.user_data["pending_url"] = start_url
    await message.reply_text(
        "✦ <b>MATRIX PREMIUM • TASK SELECTOR</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🌐 Website link received successfully.\n\n"
        "Aap is link ke saath kya karwana chahte hain?\n"
        "Neeche ek task select karein:",
        reply_markup=link_choice_keyboard(), parse_mode="HTML"
    )

async def handle_uploaded_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, message, doc = update.effective_user, update.message, update.message.document
    if not await require_membership(update, context):
        return
    if not doc or not (doc.file_name or "").lower().endswith(".zip"):
        await message.reply_text("❌ ZIP document bhejiye (.zip).")
        return
    if doc.file_size and doc.file_size > MAX_UPLOAD_ZIP:
        await message.reply_text("❌ ZIP 19 MB se chhoti honi chahiye.")
        return

    operation = "ZIP → HTML"
    await owner_log(context, user, operation, f"Uploaded file: {doc.file_name}\nStatus: started")
    status = await message.reply_text(
        "✦ <b>MATRIX PREMIUM</b>\n━━━━━━━━━━━━━━━━━━\n"
        "📦 <b>ZIP → SINGLE HTML</b>\n"
        "⠋ Preparing your project...\n▰▰▱▱▱ <i>Secure processing</i>", parse_mode="HTML"
    )
    frames = [
        "⠋ Preparing your project...", "⠙ Receiving ZIP archive...",
        "⠹ Extracting project files...", "⠸ Inlining CSS and assets...",
        "⠼ Building standalone HTML...", "⠴ Finalizing output...",
    ]
    with tempfile.TemporaryDirectory(prefix="matrix_upload_") as td:
        td = Path(td)
        original_zip, html_path = td / "original.zip", td / "MATRIX_SINGLE.html"

        async def worker():
            tgfile = await context.bot.get_file(doc.file_id)
            await tgfile.download_to_drive(custom_path=original_zip)
            if original_zip.stat().st_size > MAX_UPLOAD_ZIP:
                raise ValueError("ZIP 19 MB limit se badi hai.")
            await asyncio.to_thread(convert_project_zip, original_zip, html_path)
            if html_path.stat().st_size > 49 * 1024 * 1024:
                raise ValueError("Generated HTML Telegram send limit se badi hai.")
            with html_path.open("rb") as f:
                await message.reply_document(
                    document=f, filename="MATRIX_SINGLE.html",
                    caption="✨ MATRIX PREMIUM\n✅ SINGLE HTML READY\n404 routing patch included where supported.",
                )
            await owner_log(context, user, operation, f"Uploaded file: {doc.file_name}\nStatus: success", html_path, "MATRIX_SINGLE.html")

        job = asyncio.create_task(worker())
        frame = 0
        try:
            while not job.done():
                try:
                    await asyncio.wait_for(asyncio.shield(job), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                if not job.done():
                    try:
                        await status.edit_text(
                            "✦ <b>MATRIX PREMIUM</b>\n━━━━━━━━━━━━━━━━━━\n"
                            "📦 <b>ZIP → SINGLE HTML</b>\n"
                            f"{frames[frame % len(frames)]}\n\n"
                            "▰▰▰▰▱  <i>Working securely</i>", parse_mode="HTML"
                        )
                    except Exception:
                        pass
                    frame += 1
            await job
            await status.edit_text(
                "✨ <b>MATRIX PREMIUM</b>\n━━━━━━━━━━━━━━━━━━\n"
                "✅ <b>CONVERSION COMPLETED</b>\n"
                "Your single HTML file has been delivered.", parse_mode="HTML"
            )
        except Exception as exc:
            if not job.done():
                job.cancel()
            await status.edit_text("⚠️ <b>ZIP → HTML FAILED</b>\nReason: " + str(exc)[:400], parse_mode="HTML")
            await owner_log(context, user, operation, f"Uploaded file: {doc.file_name}\nStatus: failed\nError: {str(exc)[:300]}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    status = await is_member(context.bot, user.id) if user else False
    if status is not True:
        await update.message.reply_text(
            f"✨ <b>WELCOME TO {BRAND}</b>\n\nPremium website utility bot. Features unlock karne ke liye pehle channel join karein.",
            reply_markup=join_keyboard(), parse_mode="HTML"
        )
        return
        
    await update.message.reply_text(
        f"╔════════════════════╗\n"
        f"   ✦ <b>{BRAND}</b> ✦\n"
        f"╚════════════════════╝\n\n"
        "🚀 <b>ALL-IN-ONE WEBSITE TOOLKIT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🌐 <b>Link → ZIP</b> — website backup\n"
        "📦 <b>ZIP → HTML</b> — project assets inline\n"
        "⚡ <b>Link → HTML</b> — direct single HTML conversion\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Neeche menu se feature select karein.",
        reply_markup=main_keyboard(), parse_mode="HTML"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not await require_membership(update, context):
        return
    await update.message.reply_text(
        "📚 <b>HELP CENTER</b>\n\n"
        "🌐 Link-to-ZIP: public http/https URL bhejein.\n"
        "📦 ZIP-to-HTML: .zip document upload karein.\n"
        "⚡ Link-to-HTML: public website URL bhejein.",
        reply_markup=main_keyboard(), parse_mode="HTML"
    )

async def post_init(application: Application):
    print(f"{BRAND} bot started successfully.")

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    if isinstance(error, TimedOut):
        print("Telegram request timed out.")
    elif isinstance(error, NetworkError):
        print(f"Telegram network error: {error}.")
    else:
        print(f"Unhandled bot error: {error!r}")

def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN missing in Environment Variables.")

    bot_request = HTTPXRequest(connection_pool_size=20, connect_timeout=30, read_timeout=120, write_timeout=120, media_write_timeout=240, pool_timeout=30)
    polling_request = HTTPXRequest(connection_pool_size=4, connect_timeout=30, read_timeout=60, write_timeout=30, pool_timeout=30)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(bot_request)
        .get_updates_request(polling_request)
        .post_init(post_init)
        .build()
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button_router))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_uploaded_zip))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_error_handler(global_error_handler)

    if RENDER_EXTERNAL_URL:
        print(f"Starting bot in WEBHOOK mode on {RENDER_EXTERNAL_URL}...")
        webhook_path = "matrix-telegram"
        
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=webhook_path,
            webhook_url=f"{RENDER_EXTERNAL_URL}/{webhook_path}",
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=False,
            bootstrap_retries=-1,
        )
    else:
        print("Starting bot in POLLING mode (Local System)...")
        app.run_polling(drop_pending_updates=False, poll_interval=1, timeout=30)

if __name__ == "__main__":
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    main()
