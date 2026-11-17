#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
監視ボード — sources.json に並べた X / YouTube / note / ニュースのフィードを
まとめて取得し、スマホ向けの1枚ページ (index.html) と data.json を作る。

使い方:
    python3 update.py                     # 取得 → index.html / data.json を更新
    python3 update.py --dry-run -v        # 取得だけして結果を表示（ファイルは書かない）
    python3 update.py --only x_tkzwgrs    # 一部のソースだけ更新（カンマ区切り）
    python3 update.py --add x tkzwgrs "滝沢ガレソ"   # add_source.py に委譲

外部ライブラリ不要（Python 3.9+ の標準ライブラリだけ）。
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_PATH = os.path.join(BASE_DIR, "sources.json")
DATA_PATH = os.path.join(BASE_DIR, "data.json")
INDEX_PATH = os.path.join(BASE_DIR, "index.html")

JST = timezone(timedelta(hours=9), "JST")
WEEKDAY_JP = ["月", "火", "水", "木", "金", "土", "日"]
CJK_RE = re.compile(r"[ぁ-んァ-ヶー一-龥々〆ヵヶ]")
ILLEGAL_XML_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
BARE_AMP_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")

BADGES = {
    "x": ("X", "badge-x"),
    "youtube": ("YT", "badge-yt"),
    "note": ("note", "badge-note"),
    "news": ("JP", "badge-news"),
    "google_news": ("JP", "badge-news"),
}

TYPE_LABEL = {
    "x": "X",
    "youtube": "YouTube",
    "note": "note",
    "news": "ニュース",
    "google_news": "ニュース",
}


# ---------------------------------------------------------------- utilities
def local(tag: str) -> str:
    """名前空間を落としたタグ名。"""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def text_of(el) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def strip_html(value: str) -> str:
    """HTML 断片を素のテキストに（<br> は改行として残す）。"""
    if not value:
        return ""
    s = re.sub(r"(?is)<\s*br\s*/?\s*>", "\n", value)
    s = re.sub(r"(?is)</\s*(p|div|li|h[1-6])\s*>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", "", s)
    s = html_lib.unescape(s)
    lines = [re.sub(r"[ \t\u3000\u00a0]+", " ", ln).strip() for ln in s.split("\n")]
    return "\n".join(ln for ln in lines if ln).strip()


# ---------------------------------------------------------------- images
IMG_TAG_RE = re.compile(r"<\s*img\b[^>]*?\bsrc\s*=\s*(['\"])(.*?)\1", re.I | re.S)
IMG_LAZY_RE = re.compile(r"<\s*img\b[^>]*?\bdata-src\s*=\s*(['\"])(.*?)\1", re.I | re.S)
NITTER_PIC_RE = re.compile(r"^https?://[^/]+/pic/(?:media|amplify_video_thumb|card_img|profile_images|ext_tw_video_thumb)/(.+)$", re.I)
TWIMG_MEDIA_RE = re.compile(r"^https?://pbs\.twimg\.com/(?:media|amplify_video_thumb|ext_tw_video_thumb|tweet_video_thumb)/[^?]+$", re.I)
IMG_EXT_RE = re.compile(r"\.(?:jpe?g|png|gif|webp|avif)(?:$|[?#])", re.I)
IMAGE_HOSTS = ("pbs.twimg.com", "i.ytimg.com", "yt3.ggpht.com", "assets.st-note.com", "d2l930y2yx77uc.cloudfront.net")


def normalize_img_url(url: str) -> str:
    """画像URLを整える（nitter の /pic/ を pbs.twimg.com に、http→https、サイズ軽量化）。"""
    u = html_lib.unescape((url or "").strip())
    if not u:
        return ""
    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("http://"):
        u = "https://" + u[len("http://"):]
    if not u.startswith("https://"):
        return ""
    m = NITTER_PIC_RE.match(u)
    if m:
        rest = urllib.parse.unquote(m.group(1))
        u = "https://pbs.twimg.com/" + rest.lstrip("/")
    u = u.replace("&amp;", "&")
    # サムネイル用途なので、元画像がそのまま返る pbs.twimg.com/media は小さめを要求
    if TWIMG_MEDIA_RE.match(u):
        u += "?name=small"
    return u


def extract_images(fragment: str) -> list:
    """HTML 断片の <img src> を順番どおりに集める。"""
    if not fragment:
        return []
    found = []
    for m in IMG_TAG_RE.finditer(fragment):
        found.append(m.group(2))
    for m in IMG_LAZY_RE.finditer(fragment):
        found.append(m.group(2))
    out = []
    for raw in found:
        u = normalize_img_url(raw)
        if u and u not in out:
            out.append(u)
    return out


def looks_like_image(url: str, mime: str = "") -> bool:
    mime = (mime or "").lower()
    if mime and not mime.startswith("image"):
        return False
    host = domain_of(url)
    if host in IMAGE_HOSTS:
        return True
    if mime.startswith("image"):
        return True
    return bool(IMG_EXT_RE.search(url or ""))


def parse_date(value):
    """RFC822 / ISO8601 のどちらでも datetime(aware) にする。"""
    if not value:
        return None
    s = value.strip()
    try:
        dt = parsedate_to_datetime(s)
    except Exception:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JST)


def jst_label(dt, now=None) -> str:
    if dt is None:
        return ""
    now = now or datetime.now(JST)
    if dt.year != now.year:
        return f"{dt.year}/{dt.month}/{dt.day}({WEEKDAY_JP[dt.weekday()]}) {dt:%H:%M}"
    return f"{dt.month}/{dt.day}({WEEKDAY_JP[dt.weekday()]}) {dt:%H:%M}"


def domain_of(url: str) -> str:
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except Exception:
        host = ""
    return host[4:] if host.startswith("www.") else host


def sanitize_xml(raw: bytes) -> str:
    s = raw.decode("utf-8", "replace")
    s = s.lstrip("\ufeff")
    s = ILLEGAL_XML_RE.sub("", s)
    return BARE_AMP_RE.sub("&amp;", s)


def http_get(url: str, timeout: int, ua: str, retries: int = 1) -> tuple:
    """(bytes, status, final_url) を返す。失敗時は最後の例外を投げる。"""
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": ua,
                    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
                    "Accept-Language": "ja,en;q=0.8",
                    "Accept-Encoding": "identity",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.read(), getattr(res, "status", 200), res.geturl()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (400, 401, 403, 404, 410, 451):
                break  # リトライしても変わらない
        except Exception as exc:  # noqa: BLE001 - ネットワークは何でも起こる
            last = exc
        if attempt < retries:
            time.sleep(1.0 + attempt)
    raise last if last else RuntimeError("fetch failed")


# ------------------------------------------------------------------ parsing
def child_map(node) -> dict:
    m = {}
    for ch in node:
        m.setdefault(local(ch.tag), ch)
    return m


def parse_feed(raw: bytes):
    """RSS 2.0 / RSS 1.0 (RDF) / Atom を同じ形の dict の配列に。"""
    root = ET.fromstring(sanitize_xml(raw))
    entries = []
    for node in root.iter():
        name = local(node.tag)
        if name not in ("item", "entry"):
            continue
        m = child_map(node)
        links = [ch for ch in node if local(ch.tag) == "link"]

        link = ""
        for el in links:
            rel = (el.get("rel") or "alternate").lower()
            href = el.get("href")
            if href and rel in ("alternate", ""):
                link = href
                break
        if not link:
            for el in links:
                if el.get("href"):
                    link = el.get("href")
                    break
        if not link:
            link = text_of(m.get("link"))

        summary = ""
        for key in ("description", "summary", "content", "encoded"):
            if key in m:
                summary = text_of(m[key])
                if summary:
                    break

        # 画像: 本文HTMLの <img> と media:content / media:thumbnail / enclosure
        images = extract_images(summary)
        for el in node.iter():
            ln = local(el.tag)
            if ln not in ("content", "thumbnail", "enclosure"):
                continue
            url = el.get("url") or el.get("href") or ""
            mime = el.get("type") or ""
            if not url or not looks_like_image(url, mime):
                continue
            u = normalize_img_url(url)
            if u and u not in images:
                images.append(u)
        for el in links:
            if (el.get("rel") or "").lower() == "enclosure" and el.get("href"):
                if looks_like_image(el.get("href", ""), el.get("type") or ""):
                    u = normalize_img_url(el.get("href"))
                    if u and u not in images:
                        images.append(u)

        guid = text_of(m.get("guid")) or text_of(m.get("id")) or link
        published = parse_date(
            text_of(m.get("pubDate"))
            or text_of(m.get("published"))
            or text_of(m.get("updated"))
            or text_of(m.get("date"))
        )

        src_el = m.get("source")
        src_url = src_el.get("url") if src_el is not None else ""
        src_name = text_of(src_el)

        entries.append(
            {
                "title": text_of(m.get("title")),
                "link": link.strip(),
                "summary": summary,
                "guid": guid,
                "published": published,
                "source_url": src_url or "",
                "source_name": src_name or "",
                "author": text_of(m.get("creator")) or text_of(m.get("author")),
                "images": images,
            }
        )
    return entries


# ------------------------------------------------------------------ sources
def feed_urls(src: dict, settings: dict) -> list:
    """1つのソースが持つフィードURL（X はミラーも候補に入れる）。"""
    kind = src.get("type", "news")
    urls = []
    if src.get("feed"):
        urls.append(src["feed"])
    urls.extend(src.get("feeds") or [])
    handle = src.get("handle")

    if kind == "x" and handle:
        base = (src.get("nitter") or settings.get("nitter_base") or "https://nitter.cf").rstrip("/")
        if base not in urls:
            urls.append(f"{base}/{handle}/rss")
        for mirror in settings.get("nitter_mirrors") or []:
            mirror = (mirror or "").rstrip("/")
            if mirror and mirror != base:
                urls.append(f"{mirror}/{handle}/rss")
    if kind == "youtube" and src.get("channel_id") and not urls:
        urls.append(f"https://www.youtube.com/feeds/videos.xml?channel_id={src['channel_id']}")
    if kind == "note" and src.get("username") and not urls:
        urls.append(f"https://note.com/{src['username']}/rss")
    if kind == "google_news" and not urls:
        q = urllib.parse.quote(src.get("query", ""))
        hl = src.get("hl", "ja")
        gl = src.get("gl", "JP")
        ceid = src.get("ceid", "JP:ja")
        urls.append(f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}")

    seen, out = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def rewrite_x_link(link: str) -> str:
    """nitter のリンクを x.com の元ツイートに直す。"""
    m = re.match(r"https?://[^/]+/([A-Za-z0-9_]+)/status/(\d+)", link or "")
    if m:
        return f"https://x.com/{m.group(1)}/status/{m.group(2)}"
    return link


def detect_badge(kind: str, text: str) -> tuple:
    label, cls = BADGES.get(kind, ("JP", "badge-news"))
    if kind in ("news", "google_news", "note") and text and not CJK_RE.search(text):
        label = "EN"
    return label, cls


MEDIA_NOISE_RE = re.compile(r"^(?:Video|Pictures|GIF|Play)$", re.I)
TCO_RE = re.compile(r"https?://[^/\s]+/t\.co/\S+")
STATUS_IN_TEXT_RE = re.compile(r"https?://[^/\s]+/([A-Za-z0-9_]{1,20}/status/\d+)")


def clean_x_text(value: str) -> str:
    """nitter の本文から t.co 迷子リンクや "Video" などの飾りを落とす。"""
    s = TCO_RE.sub("", value or "")
    s = STATUS_IN_TEXT_RE.sub(r"https://x.com/\1", s)
    lines = [ln.strip() for ln in s.split("\n")]
    lines = [ln for ln in lines if ln]
    while len(lines) > 1 and MEDIA_NOISE_RE.match(lines[-1]):
        lines.pop()
    return "\n".join(lines).strip()


def clean_news_title(value: str, source_name: str = "") -> str:
    """Googleニュース由来の「 - サイト名」「(2026年11月17日掲載)」を落とす。"""
    s = (value or "").strip()
    for _ in range(2):
        s = re.sub(r"\s*\(\d{4}年\d{1,2}月\d{1,2}日掲載\)\s*$", "", s).strip()
        if source_name:
            s = re.sub(r"\s*[-|–—]\s*" + re.escape(source_name) + r"\s*$", "", s).strip()
    return s.strip()


def build_items(src: dict, entries: list) -> list:
    """フィードの生データを、ボード表示用の item dict に変換する。"""
    kind = src.get("type", "news")
    items, seen, seen_titles = [], set(), set()

    for e in entries:
        title = strip_html(e.get("title", ""))
        link = (e.get("link") or "").strip()
        body = ""
        pinned = False

        images = list(e.get("images") or [])

        if kind == "x":
            link = rewrite_x_link(link)
            body = strip_html(e.get("summary", "")) or title
            if body.startswith("Pinned:"):
                pinned = True
                body = re.sub(r"^Pinned:\s*", "", body).strip()
            elif title.startswith("Pinned:"):
                pinned = True
            body = clean_x_text(body)
            # 本文が空でも画像だけのポストは載せる
            if not body and not images:
                continue
            text = body
        elif kind == "youtube":
            text = title
            body = strip_html(e.get("summary", ""))
            images = images[:1]
            body = ""
        elif kind == "note":
            text = title
            body = strip_html(e.get("summary", ""))
            if body == text:
                body = ""
            images = images[:4]
        elif kind == "google_news":
            text = clean_news_title(title, e.get("source_name", ""))
            images = []
        else:  # news
            text = title
            images = images[:1]

        if (not text and not images) or not link:
            continue

        key = link or e.get("guid")
        if key in seen:
            continue
        seen.add(key)
        if kind != "x":
            # 同じ見出しが複数フィードに載っている場合を1本にまとめる
            tkey = re.sub(r"\s+", " ", text).strip()
            if tkey in seen_titles:
                continue
            seen_titles.add(tkey)

        if kind == "x":
            domain = "x.com"
        elif kind == "youtube":
            domain = "youtube.com"
        elif kind == "note":
            domain = domain_of(link) or "note.com"
        elif kind == "google_news":
            domain = domain_of(e.get("source_url") or "") or domain_of(link)
        else:
            domain = domain_of(link)

        badge, badge_cls = detect_badge(kind, text)
        published = e.get("published")

        items.append(
            {
                "title": text,
                "summary": body if body and body != text else "",
                "images": images[:4],
                "link": link,
                "domain": domain,
                "published": published.isoformat() if published else None,
                "published_label": jst_label(published),
                "badge": badge,
                "badge_class": badge_cls,
                "pinned": pinned,
                "type": kind,
            }
        )

    return items


def fetch_source(src: dict, settings: dict, verbose: bool = False) -> dict:
    started = time.time()
    urls = feed_urls(src, settings)
    timeout = int(settings.get("timeout_seconds", 20))
    retries = int(settings.get("retries", 1))
    ua = settings.get("user_agent") or "Mozilla/5.0 (compatible; MonitorBoard/1.0)"

    result = {
        "id": src.get("id") or src.get("name") or "source",
        "type": src.get("type", "news"),
        "name": src.get("name") or src.get("id") or "source",
        "handle": src.get("handle", ""),
        "status": "ok",
        "error": None,
        "via": "",
        "count": 0,
        "items": [],
        "elapsed": 0.0,
    }

    if not urls:
        result.update(status="error", error="フィードURLを決められません（設定を確認）", elapsed=round(time.time() - started, 1))
        return result

    entries, errors, used = [], [], []

    if src.get("type") == "x":
        # X はミラーを順に試し、最初に成功したものだけを使う
        candidates = urls
    else:
        candidates = urls  # ニュース等は複数フィードを合成する

    for url in candidates:
        try:
            raw, status, final = http_get(url, timeout, ua, retries)
            got = parse_feed(raw)
            if not got:
                raise ValueError("記事が0件（フィードが空か形式が違います）")
            entries.extend(got)
            used.append(url)
            if verbose:
                print(f"    ok   {url}  ({len(got)} items)")
            if src.get("type") == "x":
                break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url} -> {exc}")
            if verbose:
                print(f"    fail {url}  ({exc})")

    if not entries:
        result.update(
            status="error",
            error=errors[0] if errors else "取得できませんでした",
            elapsed=round(time.time() - started, 1),
        )
        return result

    items = build_items(src, entries)
    limit = int(src.get("limit") or settings.get("items_per_source", 15))
    if settings.get("pinned_first", True):
        pinned = [i for i in items if i["pinned"]]
        rest = [i for i in items if not i["pinned"]]
        items = pinned + rest
    items.sort(key=lambda i: (0 if i["pinned"] else 1, -(datetime.fromisoformat(i["published"]).timestamp() if i["published"] else 0)))
    items = items[:limit]

    result.update(
        status="ok" if not errors else "partial",
        error="; ".join(errors) if errors else None,
        via=used[0] if used else "",
        count=len(items),
        items=items,
        elapsed=round(time.time() - started, 1),
    )
    return result


# -------------------------------------------------------------------- board
def load_sources(path: str = SOURCES_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        data = {"settings": {}, "sources": data}
    data.setdefault("settings", {})
    data.setdefault("sources", [])
    return data


def previous_links() -> set:
    try:
        with open(DATA_PATH, encoding="utf-8") as fh:
            old = json.load(fh)
    except Exception:
        return set()
    links = set()
    for src in old.get("sources", []):
        for item in src.get("items", []):
            if item.get("link"):
                links.add(item["link"])
    return links


def esc(text: str) -> str:
    return html_lib.escape(text or "", quote=True)


def render_images(images: list) -> str:
    """カード内のサムネイル（最大4枚・遅延読み込み・読み込み失敗は非表示）。"""
    images = [u for u in (images or []) if u][:4]
    if not images:
        return ""
    n = len(images)
    imgs = "".join(
        f'<img src="{esc(u)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer"'
        f' onerror="this.style.display=&#39;none&#39;">'
        for u in images
    )
    return f'<span class="imgs n{n}">{imgs}</span>'


def render_item(item: dict) -> str:
    badge = esc(item["badge"])
    cls = item["badge_class"]
    title = esc(item["title"]).replace("\n", "<br>")
    if not title:
        title = ''  # 画像だけのポスト
    pin = '<span class="pin">固定</span>' if item["pinned"] else ""
    new_cls = " is-new" if item.get("is_new") else ""
    when = esc(item["published_label"]) or "—"
    title_html = f'<span class="title">{pin}{title}</span>\n' if (title or pin) else ""
    desc = ""
    if item.get("type") == "note" and item.get("summary"):
        desc = f'    <span class="desc">{esc(item["summary"]).replace(chr(10), "<br>")}</span>\n'
    imgs = render_images(item.get("images"))
    imgs_html = f"    {imgs}\n" if imgs else ""
    return (
        f'<a class="item t-{item["type"]}{new_cls}" href="{esc(item["link"])}" target="_blank" rel="noopener noreferrer">\n'
        f'  <span class="badge {cls}">{badge}</span>\n'
        f'  <span class="body">\n'
        f'    {title_html}'
        f'{desc}'
        f'{imgs_html}'
        f'    <span class="meta"><span class="dom">{esc(item["domain"])}</span>'
        f'<span class="time">{when}</span></span>\n'
        f'  </span>\n'
        f"</a>"
    )


def render_card(src: dict) -> str:
    count = f'{src["count"]}件' if src["status"] != "error" else "取得エラー"
    if src["status"] == "error":
        body = (
            '<p class="err">このソースは取得できませんでした。'
            f'<br><span>{esc(src.get("error") or "")}</span></p>'
        )
    elif not src["items"]:
        body = '<p class="err">新しい記事はありません。</p>'
    else:
        body = "\n".join(render_item(i) for i in src["items"])
        if src.get("status") == "partial" and src.get("error"):
            body += f'\n<p class="err note">一部のフィードを取得できませんでした: <span>{esc(src["error"][:160])}</span></p>'
    return (
        f'<section class="card" data-source="{esc(src["id"])}">\n'
        f'  <header><h2>{esc(src["name"])}</h2><span class="count">{esc(count)}</span></header>\n'
        f'  <div class="items">\n{body}\n  </div>\n'
        f"</section>"
    )


PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light">
<meta name="theme-color" content="#f2f3f5">
<title>{title}</title>
<style>
  :root {{
    --bg: #f2f3f5;
    --card: #ffffff;
    --text: #16181c;
    --text-2: #3f434a;
    --muted: #8a8f98;
    --line: #eef0f2;
    --new-bg: #ecf8f0;
    --dot: #e5484d;
  }}
  * {{ box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
  html {{ -webkit-text-size-adjust: 100%; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Hiragino Kaku Gothic ProN",
      "Hiragino Sans", "Noto Sans JP", "Yu Gothic", Meiryo, sans-serif;
    font-size: 14px;
    line-height: 1.6;
    padding-bottom: 40px;
  }}
  .wrap {{ max-width: 720px; margin: 0 auto; padding: 0 12px; }}
  .topbar {{
    position: sticky; top: 0; z-index: 10;
    background: rgba(242,243,245,.92);
    backdrop-filter: saturate(180%) blur(12px);
    -webkit-backdrop-filter: saturate(180%) blur(12px);
    padding: 10px 0 8px;
    border-bottom: 1px solid rgba(0,0,0,.04);
  }}
  .topbar .row {{ display: flex; align-items: baseline; gap: 8px; }}
  .topbar h1 {{ font-size: 16px; margin: 0; letter-spacing: .02em; }}
  .topbar .updated {{ font-size: 11px; color: var(--muted); }}
  .search {{
    margin-top: 8px; position: relative;
  }}
  .search input {{
    width: 100%; border: 0; border-radius: 10px; background: #fff;
    padding: 9px 12px 9px 34px; font-size: 16px; color: var(--text);
    box-shadow: 0 1px 2px rgba(16,24,40,.05);
    outline: none;
  }}
  .search::before {{
    content: ""; position: absolute; left: 12px; top: 12px; width: 12px; height: 12px;
    border: 2px solid #b6bbc4; border-radius: 50%;
  }}
  .search::after {{
    content: ""; position: absolute; left: 23px; top: 23px; width: 6px; height: 2px;
    background: #b6bbc4; transform: rotate(45deg); border-radius: 1px;
  }}
  .cards {{ margin-top: 12px; display: flex; flex-direction: column; gap: 12px; }}
  .card {{
    background: var(--card);
    border-radius: 14px;
    overflow: hidden;
    box-shadow: 0 1px 2px rgba(16,24,40,.05), 0 1px 3px rgba(16,24,40,.03);
  }}
  .card header {{
    display: flex; align-items: center; justify-content: space-between;
    gap: 10px; padding: 13px 15px 11px;
    border-bottom: 1px solid var(--line);
  }}
  .card h2 {{ font-size: 14.5px; margin: 0; font-weight: 700; letter-spacing: .01em; }}
  .count {{ font-size: 11px; color: var(--muted); white-space: nowrap; }}
  .items {{ display: flex; flex-direction: column; }}
  .item {{
    display: flex; gap: 9px; align-items: flex-start;
    padding: 10px 15px 9px;
    border-bottom: 1px solid var(--line);
    text-decoration: none; color: inherit;
  }}
  .item:last-child {{ border-bottom: 0; }}
  .item:active {{ background: #f7f8fa; }}
  .item.is-new {{ background: var(--new-bg); }}
  .badge {{
    flex: 0 0 auto; margin-top: 2px;
    font-size: 9.5px; font-weight: 700; letter-spacing: .02em;
    padding: 1px 5px; border-radius: 4px; line-height: 1.5;
    background: #fdf3d7; color: #9a6700;
  }}
  .badge-x {{ background: #e9f0fe; color: #1a56c4; }}
  .badge-yt {{ background: #fdeaea; color: #c62828; }}
  .badge-note {{ background: #e6f7ee; color: #0f7a4a; }}
  .body {{ min-width: 0; flex: 1 1 auto; display: block; }}
  .title {{
    display: block; font-size: 13.5px; line-height: 1.62; color: var(--text-2);
    word-break: break-word;
  }}
  .item.is-new .title::before {{
    content: ""; display: inline-block; width: 6px; height: 6px; border-radius: 50%;
    background: var(--dot); margin-right: 6px; vertical-align: 1px;
  }}
  .desc {{
    display: block; margin-top: 4px; font-size: 12.5px; line-height: 1.6;
    color: var(--muted); word-break: break-word;
  }}
  .imgs {{ display: grid; gap: 4px; margin-top: 8px; }}
  .imgs.n2, .imgs.n4 {{ grid-template-columns: 1fr 1fr; }}
  .imgs.n3 {{ grid-template-columns: 1fr 1fr 1fr; }}
  .imgs img {{
    display: block; width: 100%; border-radius: 9px; background: var(--bg);
    object-fit: cover; object-position: top; aspect-ratio: 4 / 3;
  }}
  .imgs.n3 img {{ aspect-ratio: 1 / 1; }}
  .imgs.n1 {{ justify-items: start; }}
  .imgs.n1 img {{
    width: auto; height: auto; max-width: 100%; max-height: 340px;
    aspect-ratio: auto; object-fit: contain; border-radius: 10px;
  }}
  .pin {{
    display: inline-block; font-size: 9.5px; font-weight: 700; color: #9a6700;
    background: #fdf3d7; border-radius: 4px; padding: 1px 5px; margin-right: 6px;
    vertical-align: 1px;
  }}
  .meta {{
    display: flex; justify-content: space-between; gap: 10px;
    margin-top: 4px; font-size: 10.5px; color: var(--muted);
  }}
  .dom {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .time {{ white-space: nowrap; }}
  .err {{ margin: 0; padding: 14px 15px; font-size: 12.5px; color: #b42318; }}
  .err span {{ color: var(--muted); font-size: 11px; word-break: break-all; }}
  .err.note {{ color: var(--muted); padding-top: 6px; padding-bottom: 10px; border-top: 1px solid var(--line); }}
  footer {{
    margin: 18px 2px 0; font-size: 11px; color: var(--muted);
    display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap;
  }}
  .hidden {{ display: none !important; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="row"><h1>{title}</h1><span class="updated">{updated}</span></div>
    <div class="search"><input id="q" type="search" placeholder="キーワードで絞り込み" autocomplete="off"></div>
  </div>
  <main class="cards" id="cards">
{cards}
  </main>
  <footer><span>{summary}</span><span>{footer_right}</span></footer>
</div>
<script id="board-data" type="application/json">{data_json}</script>
<script>
(function () {{
  var q = document.getElementById('q');
  var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
  cards.forEach(function (c) {{ c.dataset.total = c.querySelectorAll('.item').length; }});
  function apply() {{
    var v = q.value.trim().toLowerCase();
    cards.forEach(function (c) {{
      var shown = 0;
      c.querySelectorAll('.item').forEach(function (it) {{
        var hit = !v || it.textContent.toLowerCase().indexOf(v) !== -1;
        it.classList.toggle('hidden', !hit);
        if (hit) shown++;
      }});
      var count = c.querySelector('.count');
      if (count) {{
        if (c.dataset.error === '1') {{ /* エラー表示はそのまま */ }}
        else if (!v) {{ count.textContent = c.dataset.total + '件'; }}
        else {{ count.textContent = shown + '/' + c.dataset.total + '件'; }}
      }}
      c.classList.toggle('hidden', shown === 0);
    }});
  }}
  q.addEventListener('input', apply);
}})();
</script>
</body>
</html>
"""


PAGE = PAGE.replace("{{", "{").replace("}}", "}")


def render_page(data: dict, settings: dict) -> str:
    now = datetime.now(JST)
    cards = "\n".join(render_card(s) for s in data["sources"])
    kinds = {}
    for s in data["sources"]:
        kinds[s["type"]] = kinds.get(s["type"], 0) + 1
    kind_summary = " / ".join(f"{TYPE_LABEL.get(k, k)} {v}" for k, v in kinds.items())
    summary = f"全{data['total']}件 ・ {kind_summary}"
    footer_right = "1時間ごとに自動更新 (GitHub Actions)"
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return (
        PAGE.replace("{title}", esc(settings.get("title") or "監視ボード"))
        .replace("{updated}", f'最終更新 {now:%Y/%m/%d %H:%M} JST')
        .replace("{cards}", cards)
        .replace("{summary}", esc(summary))
        .replace("{footer_right}", esc(footer_right))
        .replace("{data_json}", payload)
    )


# --------------------------------------------------------------------- main
def run(args) -> int:
    cfg = load_sources()
    settings = cfg["settings"]
    sources = [s for s in cfg["sources"] if s.get("enabled", True)]

    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        sources = [s for s in sources if s.get("id") in wanted or s.get("handle") in wanted]
        if not sources:
            print(f"該当するソースがありません: {args.only}", file=sys.stderr)
            return 2

    old_links = previous_links()

    print(f"取得開始: {len(sources)} ソース")
    fetched = []
    for src in sources:
        if args.verbose:
            print(f"  - {src.get('name')} [{src.get('type')}]")
        res = fetch_source(src, settings, verbose=args.verbose)
        for item in res["items"]:
            item["is_new"] = bool(old_links) and item["link"] not in old_links
        fetched.append(res)
    results = list(fetched)

    if args.only:
        # --only のときは、選ばなかったソースの前回結果をそのまま残す
        try:
            with open(DATA_PATH, encoding="utf-8") as fh:
                old = json.load(fh)
            old_by_id = {s.get("id"): s for s in old.get("sources", [])}
        except Exception:
            old_by_id = {}
        fresh = {r["id"]: r for r in fetched}
        merged = []
        for src in [s for s in cfg["sources"] if s.get("enabled", True)]:
            sid = src.get("id")
            kept = fresh.get(sid) or old_by_id.get(sid)
            if kept:
                merged.append(kept)
        results = merged

    total = sum(r["count"] for r in results)
    ok = [r for r in results if r["status"] == "ok"]
    partial = [r for r in results if r["status"] == "partial"]
    failed = [r for r in results if r["status"] == "error"]

    print("")
    print(f"{'状態':<6}{'件数':>4}  {'ソース':<26} 取得元/エラー")
    for r in fetched:
        mark = {"ok": "OK", "partial": "一部", "error": "NG"}[r["status"]]
        via = domain_of(r["via"]) or "-"
        note = "" if r["status"] == "ok" else f"  ({r['error'][:110]})"
        print(f"{mark:<6}{r['count']:>4}  {r['name'][:26]:<26} {via}{note}")
    print("")
    if args.only and len(results) != len(fetched):
        print(f"今回の取得: {sum(r['count'] for r in fetched)}件 / {len(fetched)}ソース")
        print(f"ボード全体（前回の結果を含む）: {total}件 / {len(results)}ソース")
    else:
        print(f"合計 {total}件 / {len(results)}ソース (OK {len(ok)} / 一部 {len(partial)} / NG {len(failed)})")

    now = datetime.now(JST)
    data = {
        "generated_at": now.isoformat(timespec="seconds"),
        "generated_at_label": f"{now:%Y/%m/%d %H:%M} JST",
        "title": settings.get("title") or "監視ボード",
        "total": total,
        "sources": results,
    }

    if args.dry_run:
        print("--dry-run のためファイルは書き換えていません。")
        return 0 if not any(r["status"] == "error" for r in fetched) else 1

    with open(DATA_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(INDEX_PATH, "w", encoding="utf-8") as fh:
        fh.write(render_page(data, settings))

    print(f"書き出し: {os.path.relpath(INDEX_PATH, BASE_DIR)} / {os.path.relpath(DATA_PATH, BASE_DIR)}")
    return 0 if not any(r["status"] == "error" for r in fetched) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="監視ボードの取得と生成")
    parser.add_argument("--only", help="特定のソースIDだけ更新（カンマ区切り）")
    parser.add_argument("--dry-run", action="store_true", help="取得だけしてファイルを書かない")
    parser.add_argument("-v", "--verbose", action="store_true", help="各フィードの結果を表示")
    parser.add_argument("--add", nargs=argparse.REMAINDER, help="add_source.py に委譲してソースを追加")
    args = parser.parse_args(argv)

    if args.add is not None:
        if not args.add:
            print("例: python3 update.py --add x tkzwgrs \"滝沢ガレソ\"", file=sys.stderr)
            return 2
        script = os.path.join(BASE_DIR, "add_source.py")
        return subprocess.call([sys.executable, script] + args.add)

    return run(args)


if __name__ == "__main__":
    sys.exit(main())
