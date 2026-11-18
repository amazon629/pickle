#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
監視ボード — sources.json に並べた X / YouTube / note / ニュースのフィードを
まとめて取得し、スマホ向けの1枚ページ (index.html) と data.json を作る。

使い方:
    python3 update.py                     # 全部のボードを更新（sources.json + sources-*.json）
    python3 update.py --dry-run -v        # 取得だけして結果を表示（ファイルは書かない）
    python3 update.py --only x_tkzwgrs    # 一部のソースだけ更新（カンマ区切り）
    python3 update.py --add x tkzwgrs "滝沢ガレソ"   # add_source.py に委譲

ボードは sources*.json ごとに1枚。出力先は自動で決まる:
    sources.json      -> index.html / data.json      （メインボード）
    sources-ipo.json  -> ipo.html   / data-ipo.json  （IPO・スタートアップボード）
1枚だけ更新したいとき:
    python3 update.py --board ipo
    python3 update.py --config sources-ipo.json
    python3 update.py --config sources-ipo.json --out ipo.html --data data-ipo.json

外部ライブラリ不要（Python 3.9+ の標準ライブラリだけ）。
"""

from __future__ import annotations

import argparse
import glob
import html as html_lib
import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_PATH = os.path.join(BASE_DIR, "sources.json")
DATA_PATH = os.path.join(BASE_DIR, "data.json")
INDEX_PATH = os.path.join(BASE_DIR, "index.html")


# ------------------------------------------------------------------- boards
def board_paths(config_path: str, out: str = "", data: str = "") -> dict:
    """sources*.json から、そのボードの html / data.json のパスを決める。

    sources.json -> index.html / data.json
    sources-ipo.json -> ipo.html / data-ipo.json
    """
    config_path = os.path.abspath(config_path)
    folder = os.path.dirname(config_path)
    base = os.path.basename(config_path)
    if base == "sources.json":
        name = "main"
    elif base.startswith("sources-") and base.endswith(".json"):
        name = base[len("sources-"):-len(".json")]
    else:
        name = os.path.splitext(base)[0]
    if not name:
        name = "main"
    if name == "main":
        default_index, default_data = "index.html", "data.json"
    else:
        default_index, default_data = f"{name}.html", f"data-{name}.json"
    return {
        "name": name,
        "config": config_path,
        "index": os.path.join(folder, out) if out else os.path.join(folder, default_index),
        "data": os.path.join(folder, data) if data else os.path.join(folder, default_data),
    }


def discover_boards() -> list:
    """sources.json（メイン）と sources-*.json（サブボード）を全部返す。"""
    boards = []
    if os.path.exists(SOURCES_PATH):
        boards.append(board_paths(SOURCES_PATH))
    for path in sorted(glob.glob(os.path.join(BASE_DIR, "sources-*.json"))):
        boards.append(board_paths(path))
    return boards

JST = timezone(timedelta(hours=9), "JST")
# 全ボードをまとめて取得するときの同時接続数。大きくしすぎると Google ニュース /
# nitter に 429 を返されるので、この程度で止める（--workers で上書き可）。
GLOBAL_FETCH_WORKERS = 24
# nitter へ同時に投げる本数の上限（レート制限で空フィードが返るのを避ける）
X_FETCH_GATE = threading.Semaphore(6)
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


def match_any_term(text: str, terms: list) -> bool:
    """タイトルが must_match のどれかを含むか。

    全角/半角・大文字小文字の揺れは NFKC + 小文字化で吸収する。
    ラテン文字だけの語は単語境界で判定するので、"NE" が "ZO-NE" の
    ように別の語の一部に紛れている場合は一致にしない。
    """
    t = unicodedata.normalize("NFKC", text or "").lower()
    for term in terms or []:
        n = unicodedata.normalize("NFKC", term or "").lower().strip()
        if not n:
            continue
        if re.fullmatch(r"[\x00-\x7f]+", n):
            if re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", t):
                return True
        elif n in t:
            return True
    return False


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

        # 社名ごとの監視では「その社名を含む見出し」だけを残す（同名他社・一般語のノイズ対策）
        if src.get("must_match") and not match_any_term(text, src.get("must_match")):
            continue
        # 一般語の社名では、さらに業界語（EC・通販・楽天…）のどれかを含む見出しに絞る
        if src.get("context_match") and not match_any_term(text, src.get("context_match")):
            continue
        # 地名・別の意味の語（Town Hall / Old Town など）を含む見出しは落とす
        if src.get("exclude_match") and match_any_term(text, src.get("exclude_match")):
            continue

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


# -------------------------------------------------------------- translation
KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
LATIN_LETTER_RE = re.compile(r"[A-Za-z]")
GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
TRANSLATE_MAX_PER_RUN = 60  # 1回の更新で新たに和訳する上限（実行時間の暴走を防ぐ）


def is_foreign_text(text: str) -> bool:
    """ひらがな・カタカナを含まず、ラテン文字が十分にある（＝外国語の）文章か。"""
    t = (text or "").strip()
    if not t:
        return False
    if KANA_RE.search(t):
        return False
    return len(LATIN_LETTER_RE.findall(t)) >= 10


def _http_json(url: str, timeout: int, ua: str, data: bytes = None, extra_headers: dict = None):
    headers = {"User-Agent": ua or "Mozilla/5.0", "Accept": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _join_segments(payload) -> str:
    """Google の訳文（文字列 / [訳, 言語] / その配列）を1本の文字列に。"""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, list):
        if payload and isinstance(payload[0], (list, tuple)):
            return "".join(str(seg[0]) for seg in payload if seg and seg[0]).strip()
        return "".join(str(seg) for seg in payload if isinstance(seg, str)).strip()
    return ""


def _translate_google_chrome(text: str, timeout: int, ua: str) -> str:
    """Google 翻訳（chrome 拡張クライアント）。gtx が 429 でも通ることが多い。"""
    q = urllib.parse.quote(text)
    last = None
    for host in ("https://clients5.google.com", "https://translate.googleapis.com"):
        url = f"{host}/translate_a/t?client=dict-chrome-ex&sl=auto&tl=ja&q={q}"
        try:
            return _join_segments(_http_json(url, timeout, ua))
        except Exception as exc:
            last = exc
    if last:
        raise last
    return ""


def _translate_google(text: str, timeout: int, ua: str) -> str:
    """Google 翻訳（公開 gtx エンドポイント）。"""
    url = GOOGLE_TRANSLATE_URL + "?client=gtx&sl=auto&tl=ja&dt=t&q=" + urllib.parse.quote(text)
    payload = _http_json(url, timeout, ua)
    out = []
    for chunk in (payload[0] if payload else []) or []:
        if chunk and chunk[0]:
            out.append(chunk[0])
    return "".join(out).strip()


def chunk_text(text: str, size: int = 480) -> list:
    """長文を size 文字以下の塊に分ける（MyMemory の 500 文字制限のため）。"""
    pieces, cur = [], ""
    for word in (text or "").split(" "):
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= size:
            cur += " " + word
        else:
            pieces.append(cur)
            cur = word
    if cur:
        pieces.append(cur)
    out = []
    for piece in pieces:  # 1語だけで長すぎる場合の保険
        while len(piece) > size:
            out.append(piece[:size])
            piece = piece[size:]
        if piece:
            out.append(piece)
    return out or [text or ""]


def _translate_mymemory(text: str, timeout: int, ua: str) -> str:
    """MyMemory（無料・APIキー不要）。500 文字ずつに分けて訳す。"""
    lang = "en"
    if re.search(r"[\u0400-\u04ff]", text):
        lang = "ru"
    elif re.search(r"[\uac00-\ud7af]", text):
        lang = "ko"
    out = []
    for piece in chunk_text(text, 480):
        url = (
            "https://api.mymemory.translated.net/get?langpair="
            + urllib.parse.quote(f"{lang}|ja") + "&q=" + urllib.parse.quote(piece)
        )
        payload = _http_json(url, timeout, ua)
        got = ((payload.get("responseData") or {}).get("translatedText") or "").strip()
        if "MYMEMORY WARNING" in got.upper() or "QUERY LENGTH LIMIT" in got.upper():
            return ""
        if not got:
            return ""
        out.append(html_lib.unescape(got).strip())
        time.sleep(0.15)
    return "\n".join(p for p in out if p).strip()


# 上から順に試し、最初に成功したものを使う
TRANSLATORS = (_translate_google_chrome, _translate_google, _translate_mymemory)


def translate_text(text: str, timeout: int = 10, ua: str = "") -> str:
    """外国語テキストを日本語に訳す。無料の翻訳サービスを順に試す。"""
    src = (text or "").strip()
    if not src:
        return ""
    errors = []
    for fn in TRANSLATORS:
        try:
            ja = fn(src[:1500], timeout, ua)
        except Exception as exc:
            errors.append(f"{fn.__name__}: {exc}")
            continue
        if ja and ja != src:
            return ja
    if errors:
        raise RuntimeError(" / ".join(errors[-2:]))
    return ""


def attach_translations(results: list, previous: dict, settings: dict, verbose: bool = False) -> dict:
    """外国語の投稿に和訳を付ける（並列）。

    前回の data*.json に和訳があれば通信せずそのまま使うので、
    時間ごとの更新で訳すのは「新しく増えた投稿」だけ。
    """
    stats = {"made": 0, "reused": 0, "failed": 0}
    if settings.get("translate") is False:
        return stats
    ua = settings.get("user_agent") or "Mozilla/5.0"
    timeout = int(settings.get("timeout_seconds", 20))
    budget = int(settings.get("translate_max_per_run", TRANSLATE_MAX_PER_RUN))

    pending = []
    for src in results:
        for item in src.get("items", []):
            text = item.get("title") or ""
            if not is_foreign_text(text):
                continue
            cached = ((previous.get(item.get("link")) or {}).get("title_ja") or "").strip()
            if cached:
                item["title_ja"] = cached
                stats["reused"] += 1
            else:
                pending.append(item)

    if not pending or budget <= 0:
        pass
    else:
        todo = pending[:budget]
        stats["failed"] += max(0, len(pending) - len(todo))
        workers = max(1, int(settings.get("translate_workers", 6)))

        def work(item):
            try:
                return item, translate_text(item["title"], timeout=min(timeout, 15), ua=ua)
            except Exception as exc:
                return item, exc

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for item, out in pool.map(work, todo):
                if isinstance(out, Exception):
                    stats["failed"] += 1
                    if verbose:
                        print(f"    和訳できませんでした: {item.get('link')} ({out})")
                    continue
                if not out or out == item.get("title"):
                    stats["failed"] += 1
                    continue
                item["title_ja"] = out
                stats["made"] += 1

    if verbose and any(stats.values()):
        line = f"  和訳: 新規 {stats['made']} 件 / 前回から再利用 {stats['reused']} 件"
        if stats["failed"]:
            line += f" / 見送り {stats['failed']} 件"
        print(line)
    return stats


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

    if src.get("type") == "x":
        # X はミラーを順に試し、最初に成功したものだけを使う
        candidates = urls
    else:
        candidates = urls  # ニュース等は複数フィードを合成する

    def try_urls(urls_to_try):
        got_entries, got_errors, got_used = [], [], []
        for url in urls_to_try:
            try:
                raw, status, final = http_get(url, timeout, ua, retries)
                got = parse_feed(raw)
                if not got and src.get("type") != "google_news":
                    # 社名検索（google_news）は該当記事0件が普通なのでエラーにしない
                    raise ValueError("記事が0件（フィードが空か形式が違います）")
                got_entries.extend(got)
                got_used.append(url)
                if verbose:
                    print(f"    ok   {url}  ({len(got)} items)")
                if src.get("type") == "x":
                    break
            except Exception as exc:  # noqa: BLE001
                got_errors.append(f"{url} -> {exc}")
                if verbose:
                    print(f"    fail {url}  ({exc})")
        return got_entries, got_errors, got_used

    # nitter は同時に投げすぎるとレート制限で空のフィードを返すので、
    # X の取得は別枠のセマフォで絞る（全体は 24 並列でも、nitter へは 6 まで）。
    gate = X_FETCH_GATE if src.get("type") == "x" else None
    if gate is not None:
        gate.acquire()
    try:
        entries, errors, used = try_urls(candidates)
        if src.get("type") == "x" and not entries:
            # 一時的なレート制限は少し待つと戻ることが多いので、一度だけやり直す
            time.sleep(2.0)
            more, more_errors, more_used = try_urls(candidates)
            entries += more
            errors += more_errors
            used += more_used
    finally:
        if gate is not None:
            gate.release()

    if not entries:
        if used:
            # フィードは取れたが記事が無い（社名検索でヒット0件など）
            result.update(
                status="ok",
                via=used[0],
                count=0,
                items=[],
                elapsed=round(time.time() - started, 1),
            )
            return result
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


def previous_links(data_path: str = DATA_PATH) -> set:
    return set(previous_items_by_link(data_path))


def previous_items_by_link(data_path: str = DATA_PATH) -> dict:
    """前回の data*.json を link -> item の辞書で返す（和訳の再利用などに使う）。"""
    try:
        with open(data_path, encoding="utf-8") as fh:
            old = json.load(fh)
    except Exception:
        return {}
    items = {}
    for src in old.get("sources", []):
        for item in src.get("items", []):
            link = item.get("link")
            if link:
                items[link] = item
    return items


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
    title = esc(item["title"]).replace("\n", "<br>")  # 画像だけのポストは空
    pin = '<span class="pin">固定</span>' if item["pinned"] else ""
    new_cls = " is-new" if item.get("is_new") else ""
    when = esc(item["published_label"]) or "—"
    title_html = f'<span class="title">{pin}{title}</span>\n' if (title or pin) else ""
    desc = ""
    if item.get("type") == "note" and item.get("summary"):
        desc = f'    <span class="desc">{esc(item["summary"]).replace(chr(10), "<br>")}</span>\n'
    imgs = render_images(item.get("images"))
    imgs_html = f"    {imgs}\n" if imgs else ""
    trans_html = ""
    if item.get("title_ja"):
        trans_html = (
            f'    <span class="trans"><span class="trans-label">【和訳】</span>'
            f'{esc(item["title_ja"]).replace(chr(10), "<br>")}</span>\n'
        )
    return (
        f'<a class="item t-{item["type"]}{new_cls}" href="{esc(item["link"])}" target="_blank" rel="noopener noreferrer">\n'
        f'  <span class="badge {cls}">{badge}</span>\n'
        f'  <span class="body">\n'
        f'    {title_html}'
        f'{trans_html}'
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
  .nav {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }}
  .nav a {{
    font-size: 11.5px; line-height: 1.5; padding: 2px 10px; border-radius: 999px;
    color: var(--text-2); background: #fff; border: 1px solid #e4e6ea;
    text-decoration: none;
  }}
  .nav a.is-active {{ background: var(--text); border-color: var(--text); color: #fff; }}
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
  .trans {{
    display: block; margin-top: 6px; padding: 6px 9px 6px 9px;
    background: #eefaf3; border-left: 3px solid #16a34a; border-radius: 4px;
    font-size: 13px; line-height: 1.62; color: #14532d; word-break: break-word;
  }}
  .trans-label {{ font-weight: 700; color: #15803d; margin-right: 1px; }}
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
{nav_block}    <div class="search"><input id="q" type="search" placeholder="キーワードで絞り込み" autocomplete="off"></div>
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


def render_nav(current: str, boards: list, labels: dict) -> str:
    """1枚目のボードから他のボードへ飛べる小さなピル。"""
    if len(boards) < 2:
        return ""
    pills = []
    for b in boards:
        cls = "is-active" if b["name"] == current else ""
        href = os.path.basename(b["index"])
        label = labels.get(b["name"]) or b["name"]
        pills.append(f'<a class="{cls}" href="{esc(href)}">{esc(label)}</a>')
    return '    <nav class="nav">' + "".join(pills) + "</nav>\n"


def render_page(data: dict, settings: dict, nav_block: str = "") -> str:
    now = datetime.now(JST)
    cards = "\n".join(render_card(s) for s in data["sources"])
    kinds = {}
    for s in data["sources"]:
        kinds[s["type"]] = kinds.get(s["type"], 0) + 1
    kind_summary = " / ".join(f"{TYPE_LABEL.get(k, k)} {v}" for k, v in kinds.items())
    summary = f"全{data['total']}件 ・ {kind_summary}"
    translated = sum(1 for s in data["sources"] for i in s.get("items", []) if i.get("title_ja"))
    footer_right = "1時間ごとに自動更新 (GitHub Actions)"
    if translated:
        footer_right += " ・ 外国語の投稿は自動で和訳"
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return (
        PAGE.replace("{title}", esc(settings.get("title") or "監視ボード"))
        .replace("{updated}", f'最終更新 {now:%Y/%m/%d %H:%M} JST')
        .replace("{nav_block}", nav_block)
        .replace("{cards}", cards)
        .replace("{summary}", esc(summary))
        .replace("{footer_right}", esc(footer_right))
        .replace("{data_json}", payload)
    )


# --------------------------------------------------------------------- main
def board_label(board: dict, settings: dict) -> str:
    """ナビに出す短い名前（settings.nav_label で上書き可）。"""
    return (
        settings.get("nav_label")
        or ("メイン" if board["name"] == "main" else board["name"].upper())
    )


def run_board(board: dict, args, nav_block: str = "", prefetched: list = None) -> dict:
    """1枚のボードを取得して書き出す。結果のサマリーを返す。"""
    cfg = load_sources(board["config"])
    settings = cfg["settings"]
    sources = [s for s in cfg["sources"] if s.get("enabled", True)]
    title = settings.get("title") or "監視ボード"

    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        sources = [s for s in sources if s.get("id") in wanted or s.get("handle") in wanted]
        if not sources:
            return {"title": title, "skipped": True, "errors": 0}

    previous = previous_items_by_link(board["data"])
    old_links = set(previous)

    if prefetched is not None:
        # run() が全ボードぶんまとめて取得済み（ボードをまたいで並列化）
        fetched = list(prefetched)
        print(f"取得済み: {len(fetched)} ソース  — {title}")
    else:
        workers = max(1, int(settings.get("fetch_workers", 8)))
        print(f"取得開始: {len(sources)} ソース（並列 {min(workers, len(sources))}）  — {title}")
        if args.verbose:
            for src in sources:
                print(f"  - {src.get('name')} [{src.get('type')}]")
        with ThreadPoolExecutor(max_workers=min(workers, max(1, len(sources)))) as pool:
            fetched = list(pool.map(lambda s: fetch_source(s, settings, verbose=False), sources))
    for res in fetched:
        for item in res["items"]:
            item["is_new"] = bool(old_links) and item["link"] not in old_links

    attach_translations(fetched, previous, settings, verbose=args.verbose)

    results = list(fetched)
    if args.only:
        # --only のときは、選ばなかったソースの前回結果をそのまま残す
        try:
            with open(board["data"], encoding="utf-8") as fh:
                old = json.load(fh)
            old_by_id = {s.get("id"): s for s in old.get("sources", [])}
        except Exception:
            old_by_id = {}
        fresh = {r["id"]: r for r in fetched}
        merged = []
        for src in [s for s in cfg["sources"] if s.get("enabled", True)]:
            kept = fresh.get(src.get("id")) or old_by_id.get(src.get("id"))
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
        "title": title,
        "total": total,
        "sources": results,
    }

    errors = len(failed)
    if args.dry_run:
        print("--dry-run のためファイルは書き換えていません。")
        return {"title": title, "skipped": False, "errors": errors, "tolerate": bool(settings.get("tolerate_errors"))}

    with open(board["data"], "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(board["index"], "w", encoding="utf-8") as fh:
        fh.write(render_page(data, settings, nav_block=nav_block))

    print(
        "書き出し: "
        f"{os.path.relpath(board['index'], BASE_DIR)} / {os.path.relpath(board['data'], BASE_DIR)}"
    )
    return {
        "title": title,
        "skipped": False,
        "errors": errors,
        "tolerate": bool(settings.get("tolerate_errors")),
    }


def prefetch_all(boards: list, workers: int = None) -> dict:
    """全ボードのソースを1つのプールでまとめて取得する（ボードをまたいで並列化）。

    これまではボードごとに順番に取りに行っていたので、ソースの少ないボードが
    続くと待ち時間が積み上がった。ここでは全ボードぶんの取得を先に一括で流し、
    同時接続数を workers で抑えることで、Google ニュース / nitter への
    過剰なバーストを避けつつ全体の時間を短くする。

    戻り値: {ボードのindex: [fetch_source の結果, ...]}（ソースの並び順）
    """
    workers = max(1, int(workers or GLOBAL_FETCH_WORKERS))
    plans = []
    for board in boards:
        cfg = load_sources(board["config"])
        settings = cfg["settings"]
        srcs = [s for s in cfg["sources"] if s.get("enabled", True)]
        plans.append((settings, srcs))

    results = {bi: [None] * len(srcs) for bi, (_, srcs) in enumerate(plans)}
    total = sum(len(srcs) for _, srcs in plans)
    started = time.time()
    print(f"一括取得: {total} ソース / {len(plans)} ボード（並列 {workers}）")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = []
        for bi, (settings, srcs) in enumerate(plans):
            for si, src in enumerate(srcs):
                futures.append((bi, si, pool.submit(fetch_source, src, settings, False)))
        for bi, si, fut in futures:
            try:
                results[bi][si] = fut.result()
            except Exception as exc:  # noqa: BLE001
                src = plans[bi][1][si]
                results[bi][si] = {
                    "id": src.get("id") or src.get("name") or "source",
                    "type": src.get("type", "news"),
                    "name": src.get("name") or src.get("id") or "source",
                    "handle": src.get("handle", ""),
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "via": "",
                    "count": 0,
                    "items": [],
                    "elapsed": 0.0,
                }
    print(f"一括取得 完了: {time.time() - started:.1f}秒")
    return results


def run(args) -> int:
    """コマンドライン引数から対象ボードを決めて更新する。"""
    all_boards = discover_boards()
    boards = list(all_boards)

    if args.config:
        path = args.config
        if not os.path.isabs(path):
            path = os.path.join(BASE_DIR, path)
        if not os.path.exists(path):
            print(f"設定ファイルがありません: {args.config}", file=sys.stderr)
            return 2
        boards = [board_paths(path, out=args.out or "", data=args.data or "")]
    elif args.board:
        wanted = {b.strip() for b in args.board.split(",") if b.strip()}
        boards = [b for b in all_boards if b["name"] in wanted]
        if not boards:
            print(f"該当するボードがありません: {args.board}", file=sys.stderr)
            return 2
    elif args.out or args.data:
        print("--out / --data は --config と一緒に使ってください。", file=sys.stderr)
        return 2

    if not boards:
        print("sources*.json が見つかりません。", file=sys.stderr)
        return 2

    labels = {}
    for b in all_boards:
        try:
            labels[b["name"]] = board_label(b, load_sources(b["config"]).get("settings", {}))
        except Exception:
            labels[b["name"]] = b["name"]

    summaries = []
    prefetched_all = None
    if len(boards) > 1 and not args.only:
        prefetched_all = prefetch_all(boards, getattr(args, "workers", None))
    for bi, board in enumerate(boards):
        nav = render_nav(board["name"], all_boards, labels)
        summary = run_board(
            board, args, nav_block=nav,
            prefetched=(prefetched_all.get(bi) if prefetched_all is not None else None),
        )
        summary["name"] = board["name"]
        summaries.append(summary)
        if len(boards) > 1 and not args.dry_run:
            print("")

    ran = [s for s in summaries if not s["skipped"]]
    if not ran:
        print(f"該当するソースがありません: {args.only}", file=sys.stderr)
        return 2
    if len(boards) > 1:
        print("更新したボード: " + " / ".join(f"{s['title']}" for s in ran))
    # settings.tolerate_errors のボードは、一部ソースの失敗で終了コードを落とさない
    # （社名検索のように一時的に取れないことがあるボードで、他のボードの更新を止めないため）
    fatal = [s for s in ran if s["errors"] and not s.get("tolerate")]
    return 0 if not fatal else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="監視ボードの取得と生成")
    parser.add_argument("--only", help="特定のソースIDだけ更新（カンマ区切り）")
    parser.add_argument("--dry-run", action="store_true", help="取得だけしてファイルを書かない")
    parser.add_argument("-v", "--verbose", action="store_true", help="各フィードの結果を表示")
    parser.add_argument("--board", help="更新するボード名（例: main, ipo）")
    parser.add_argument("--config", help="この sources*.json だけ更新（例: sources-ipo.json）")
    parser.add_argument("--out", help="--config と一緒に使う: 出力するHTML")
    parser.add_argument("--data", help="--config と一緒に使う: 出力するJSON")
    parser.add_argument(
        "--workers",
        type=int,
        help=f"全ボードまとめて取得するときの並列数（既定 {GLOBAL_FETCH_WORKERS}）",
    )
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
