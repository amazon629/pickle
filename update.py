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
# archive-*.json（過去投稿のシード）の読み込みキャッシュ
_ARCHIVE_CACHE = {}
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
    # 年は常に出す（「8/6(木)」だけだと何年の記事か分からないという指摘から）
    if dt.year != now.year:
        return f"{dt.year}/{dt.month}/{dt.day}({WEEKDAY_JP[dt.weekday()]}) {dt:%H:%M}"
    return f"{dt.year}/{dt.month}/{dt.day}({WEEKDAY_JP[dt.weekday()]}) {dt:%H:%M}"


def jst_date_label(dt) -> str:
    """日付だけのソース（公式サイトのお知らせ等）用。時刻は出さない。"""
    if dt is None:
        return ""
    return f"{dt.year}/{dt.month}/{dt.day}({WEEKDAY_JP[dt.weekday()]})"


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


# ------------------------------------------------------- html news (公式サイト)
HTML_DATE_RE = re.compile(
    r"(?:(\d{4})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})\s*日?"
    r"|令和\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日)"
)
HTML_BLOCK_RE = re.compile(r"<(li|tr|article|dd)\b[^>]*>", re.I)
JUNK_TITLE_RE = re.compile(r"^(?:お知らせ一覧|ニュース一覧|新着情報|商品一覧|一覧|もっと見る|リリース|トピックス|TOP|HOME)$")
# 公式サイトの「事務連絡」系（配送遅延・休業・採用・ブログ更新など）は、
# 発売・キャンペーン・受賞などの本題の後ろに回す（消しはしない）。
LOW_PRIORITY_RE = re.compile(
    r"(配送|発送|遅延|休業|年末年始|お盆|シルバーウィーク|ゴールデンウィーク|メンテナンス|障害|"
    r"停止|価格改定|値上げ|採用|Instagram|YouTube|ブログ|SNS|電話番号|迷惑メール|フィッシング|"
    r"パスワード|ログイン|会員|システム|サーバ|問い合わせ)", re.I)


def html_date(value: str):
    """HTMLの日付表記（2026.10.01 / 2026年10月1日 / 令和8年…）を datetime に。"""
    m = HTML_DATE_RE.search(value or "")
    if not m:
        return None
    if m.group(1):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        y, mo, d = 2018 + int(m.group(4)), int(m.group(5)), int(m.group(6))
    try:
        return datetime(y, mo, d, 9, 0, tzinfo=JST)
    except ValueError:
        return None


def parse_html_news(text: str, base_url: str, max_items: int = 80) -> list:
    """公式サイトのお知らせ/ニュース HTML から (日付, タイトル, リンク) を拾う。

    (1) <li>/<tr>/<article>/<dd> ブロック内の「日付＋リンク＋見出し」
    (2) <a> 自身の中に日付がある型（<time datetime="…"> も可）
    の両方を拾って重複を除く。取れなければ「リンク直前40文字に日付」型も見る。
    """
    h = re.sub(r"(?is)<\s*(script|style)\b.*?</\s*\1\s*>", " ", text or "")
    entries, seen = [], set()

    def clean_title(value: str) -> str:
        t = HTML_DATE_RE.sub("", strip_html(value))
        t = t.replace("-->", " ")
        # 先頭のカテゴリラベル（NEWS / リリース / お知らせ / TOPICS など）を落とす
        for _ in range(2):
            t = re.sub(
                r"^\s*(?:NEWS|news|News|リリース|お知らせ|おしらせ|TOPICS|Topics|トピックス|"
                r"コーポレート|商品情報|新商品情報|メディア|MEDIA|Information|インフォメーション)"
                r"\s*[:：|｜\-–—]?\s+", "", t)
        # 末尾のカテゴリラベル（… MEDIA / … TOPICS など）も落とす
        t = re.sub(
            r"\s+(?:NEWS|news|リリース|お知らせ|TOPICS|Topics|トピックス|メディア|MEDIA|コーポレート)\s*$",
            "", t)
        t = re.sub(r"[（(]\s*[)）]\s*$", "", t)  # 末尾の空括弧
        t = re.sub(r"\s+", " ", t).strip(" 　|,-–—>")
        return t

    def add(dt, title, href):
        if not dt or not href or href.startswith(("javascript:", "#", "mailto:")):
            return
        link = urllib.parse.urljoin(base_url, href)
        title = clean_title(title)
        if len(title) < 6:
            return
        if JUNK_TITLE_RE.match(title):
            return
        for kept in entries:
            other = kept["title"]
            short, long_ = (title, other) if len(title) <= len(other) else (other, title)
            if short == long_ or (len(short) >= 6 and short in long_):
                return
        key = (link, title)
        if key in seen:
            return
        seen.add(key)
        seen.add(title)
        entries.append({
            "title": title, "link": link, "summary": "", "guid": link,
            "published": dt, "source_url": "", "source_name": "", "author": "", "images": [],
            "date_only": True,
            "priority": 1 if LOW_PRIORITY_RE.search(title) else 0,
        })

    # (1) ブロック型
    for m in HTML_BLOCK_RE.finditer(h):
        tag = m.group(1).lower()
        end_pos = h.find(f"</{tag}>", m.end())
        if end_pos == -1 or end_pos - m.end() > 6000:
            continue
        block = h[m.start(): end_pos + len(tag) + 3]
        dt = html_date(strip_html(block))
        if not dt:
            continue
        anchors = re.findall(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S | re.I)
        if not anchors:
            continue
        href, inner = anchors[0]
        title = strip_html(inner)
        if len(HTML_DATE_RE.sub("", title).strip()) < 6:
            rest = strip_html(re.sub(r"<a\b[^>]*>.*?</a>", " ", block, flags=re.S | re.I))
            title = rest[:160]
        add(dt, title, href)
        if len(entries) >= max_items:
            return entries

    # (2) リンク自身に日付がある型（<time datetime> も見る）
    for m in re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', h, re.S | re.I):
        href, inner = m.group(1), m.group(2)
        inner_text = strip_html(inner)
        dt = html_date(inner_text)
        if not dt:
            tm = re.search(r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})', inner, re.I)
            if tm:
                dt = parse_date(tm.group(1))
        if dt:
            add(dt, inner_text, href)
        elif entries:
            continue
        else:
            before = strip_html(h[max(0, m.start() - 400): m.start()])[-60:]
            dt2 = html_date(before)
            if dt2 and len(strip_html(inner)) >= 6:
                add(dt2, inner_text, href)
        if len(entries) >= max_items:
            break

    entries.sort(key=lambda e: e["published"], reverse=True)
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


def source_jobs(src: dict, settings: dict) -> list:
    """1ソース分の取得ジョブ（RSS/Atom と 公式サイトHTML）を並べる。

    extra_feeds … PR TIMES 企業RSS など、社名がタイトルに無い社の別フィード
    html_news   … 公式サイトの「お知らせ／ニュース」ページ（HTML）
    どちらも `trusted` 扱い（社名での絞り込みをしない。取得元自体がその会社なので）。
    """
    jobs, seen = [], set()
    for url in feed_urls(src, settings):
        if url and url not in seen:
            seen.add(url)
            jobs.append({"url": url, "kind": "feed", "origin": "", "trusted": False})
    for item in src.get("extra_feeds") or []:
        if isinstance(item, str):
            item = {"url": item}
        url = (item or {}).get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        jobs.append({"url": url, "kind": "feed", "origin": item.get("origin") or "prtimes", "trusted": True})
    html_urls = src.get("html_news")
    if isinstance(html_urls, str):
        html_urls = [html_urls]
    for item in html_urls or []:
        if isinstance(item, str):
            item = {"url": item}
        url = (item or {}).get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        jobs.append({"url": url, "kind": "html", "origin": item.get("origin") or "site", "trusted": True})
    return jobs


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


def starts_with_term(text: str, terms: list) -> bool:
    """本文の1行目（先頭の絵文字・記号を除く）が terms のどれかとちょうど一致するか。

    東京IPOの「新規承認カード」だけを拾いたいときに使う。
    「🎉新規承認 / 💹FOLIO…」は一致、「新規承認出ないなぁー…」のような話題投稿、
    お詫び・価格決定の投稿は不一致になる。
    """
    t = unicodedata.normalize("NFKC", text or "").strip()
    first = t.split("\n", 1)[0].strip()
    # 先頭・末尾の絵文字や記号（🎉・【】・！！など）を落としてから比べる
    first = re.sub(r"^[^0-9A-Za-z\u3040-\u30ff\u4e00-\u9fff]+", "", first)
    first = re.sub(r"[^0-9A-Za-z\u3040-\u30ff\u4e00-\u9fff]+$", "", first).strip()
    for term in terms or []:
        n = unicodedata.normalize("NFKC", term or "").strip()
        if n and first.lower() == n.lower():
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
        # ただし extra_feeds / html_news（取得元自体がその会社）の記事は社名が無くて当然なので素通しする
        if src.get("must_match") and not e.get("trusted") and not match_any_term(text, src.get("must_match")):
            continue
        # 本文の「冒頭」が決まった見出しの投稿だけを残す（例: x_tokyoipo_approved の「🎉新規承認」）
        if src.get("must_start") and not e.get("trusted") and not starts_with_term(text, src.get("must_start")):
            continue
        # 一般語の社名では、さらに業界語（EC・通販・楽天…）のどれかを含む見出しに絞る
        if src.get("context_match") and not e.get("trusted") and not match_any_term(text, src.get("context_match")):
            continue
        # 地名・別の意味の語（Town Hall / Old Town など）を含む見出しは落とす
        if src.get("exclude_match") and not e.get("trusted") and match_any_term(text, src.get("exclude_match")):
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
        origin = e.get("origin") or ""
        if origin == "prtimes":
            badge, badge_cls = "PRT", "badge-news"
        elif origin == "site":
            badge, badge_cls = "公式", "badge-news"
        published = e.get("published")

        items.append(
            {
                "title": text,
                "summary": body if body and body != text else "",
                "images": images[:4],
                "link": link,
                "domain": domain,
                "published": published.isoformat() if published else None,
                "published_label": (jst_date_label(published) if e.get("date_only") else jst_label(published)),
                "badge": badge,
                "badge_class": badge_cls,
                "pinned": pinned,
                "type": kind,
                "origin": origin,
                "priority": int(e.get("priority") or 0),
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
    jobs = source_jobs(src, settings)
    urls = [j["url"] for j in jobs]
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
        candidates = jobs
    else:
        candidates = jobs  # ニュース等は複数フィード／取得元を合成する

    def try_urls(jobs_to_try):
        got_entries, got_errors, got_used = [], [], []
        for job in jobs_to_try:
            url = job["url"]
            try:
                raw, status, final = http_get(url, timeout, ua, retries)
                if job["kind"] == "html":
                    got = parse_html_news(
                        raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw,
                        final or url,
                    )
                    if not got:
                        raise ValueError("ニュース記事を抽出できませんでした（HTML形式が想定外）")
                else:
                    got = parse_feed(raw)
                    if not got and src.get("type") != "google_news" and not job["trusted"]:
                        # 社名検索（google_news）は該当記事0件が普通なのでエラーにしない
                        raise ValueError("記事が0件（フィードが空か形式が違います）")
                for entry in got:
                    entry["trusted"] = job["trusted"]
                    entry["origin"] = job["origin"]
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
    # 年で絞るボード用（sources-*.json の settings/src に min_year を書いたときだけ効く）。
    # 例: ipo-approved は min_year: 2026 で「2026年の新規承認」だけを残す。
    min_year = int(src.get("min_year") or settings.get("min_year") or 0)
    if min_year:
        def _year_of(it):
            pub = it.get("published")
            if not pub:
                return 0
            try:
                return datetime.fromisoformat(pub).year
            except ValueError:
                return int(pub[:4]) if pub[:4].isdigit() else 0
        items = [it for it in items if _year_of(it) >= min_year]
    limit = int(src.get("limit") or settings.get("items_per_source", 15))
    if settings.get("pinned_first", True):
        pinned = [i for i in items if i["pinned"]]
        rest = [i for i in items if not i["pinned"]]
        items = pinned + rest
    # 日付の新しい順（同じ日時なら本題の記事を事務連絡より先に）
    items.sort(key=lambda i: (
        0 if i["pinned"] else 1,
        -(datetime.fromisoformat(i["published"]).timestamp() if i["published"] else 0),
        int(i.get("priority") or 0),
    ))
    items = dedupe_items(items)
    # 公式サイトの事務連絡（配送遅延・休業など）で枠が埋まらないよう、既定で最大3件まで
    max_routine = int(src.get("max_routine_items") or settings.get("max_routine_items") or 2)
    if max_routine >= 0:
        kept, routine = [], 0
        for it in items:
            if it.get("priority") and routine >= max_routine:
                continue
            if it.get("priority"):
                routine += 1
            kept.append(it)
        items = kept
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


def title_key(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "").lower()
    s = re.sub(r"[\s・,，.。!！?？「」『』【】\[\]（）()\-–—:：/／|｜~〜]", "", s)
    return s


def dedupe_items(items: list) -> list:
    """同じリリースが Googleニュース／PR TIMES／公式サイトに重複して載るのを1本に。"""
    kept = []
    for it in items:
        key = title_key(it.get("title", ""))
        dup = False
        for k in kept:
            other = title_key(k.get("title", ""))
            if not key or not other:
                continue
            if key == other:
                dup = True
                break
            short, long_ = (key, other) if len(key) <= len(other) else (other, key)
            if len(short) >= 12 and short in long_ and len(short) / len(long_) >= 0.7:
                dup = True
                break
        if not dup:
            kept.append(it)
    return kept


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


def load_archive(path: str) -> list:
    """過去ログ（archive-*.json）の items を読む。書き換えない読み取り専用のシード。"""
    if path not in _ARCHIVE_CACHE:
        try:
            with open(path, encoding="utf-8") as fh:
                _ARCHIVE_CACHE[path] = json.load(fh).get("items", [])
        except (FileNotFoundError, ValueError):
            _ARCHIVE_CACHE[path] = []
    return _ARCHIVE_CACHE[path]


def save_archive(path: str, items: list) -> None:
    """archive-*.json を日付降順で書き戻す（毎時実行で新しい投稿を蓄積するため）。"""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, ValueError):
        data = {}
    items = sorted(items, key=lambda it: it.get("published") or "", reverse=True)
    data.setdefault("settings", {})
    data["settings"].update(
        {
            "item_count": len(items),
            "oldest": items[-1].get("published") if items else None,
            "newest": items[0].get("published") if items else None,
            "updated_at": datetime.now(JST).isoformat(timespec="seconds"),
        }
    )
    data["items"] = items
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    _ARCHIVE_CACHE[path] = items


def merge_archive(res: dict, src: dict, settings: dict) -> dict:
    """archive-*.json の過去分を、今回の取得結果に合流させる。

    RSS は最新 18〜20件しか返さないので、それより古い投稿はここで補う。
    毎時実行でも archive ファイルはそのまま残り、新しい投稿は RSS 側から先頭に積まれる。
    同じ link は取得結果を優先（本文・画像が最新）。
    """
    rel = src.get("archive")
    if not rel:
        return res
    path = rel if os.path.isabs(rel) else os.path.join(BASE_DIR, rel)
    archived = load_archive(path)
    if not archived:
        return res
    min_year = int(src.get("min_year") or settings.get("min_year") or 0)
    limit = int(src.get("limit") or settings.get("items_per_source", 15))

    def keeps(it: dict) -> bool:
        pub = it.get("published") or ""
        if min_year:
            try:
                if datetime.fromisoformat(pub).year < min_year:
                    return False
            except ValueError:
                if not (pub[:4].isdigit() and int(pub[:4]) >= min_year):
                    return False
        if src.get("must_start") and not starts_with_term(it.get("title", ""), src.get("must_start")):
            return False
        if src.get("must_match") and not match_any_term(it.get("title", ""), src.get("must_match")):
            return False
        return True

    by_link = {}
    for it in res.get("items", []):
        if it.get("link"):
            by_link[it["link"]] = it
    added = 0
    archived_links = {it.get("link") for it in archived}
    fresh_for_archive = [
        it for it in res.get("items", []) if it.get("link") and it["link"] not in archived_links and keeps(it)
    ]
    for it in archived:
        link = it.get("link")
        if not link or link in by_link:
            continue
        if not keeps(it):
            continue
        item = dict(it)
        item.setdefault("type", "x")
        item.setdefault("badge", "X")
        item.setdefault("badge_class", "badge-x")
        item.setdefault("domain", "x.com")
        item.setdefault("pinned", False)
        item["is_new"] = False
        item["from_archive"] = True
        by_link[link] = item
        added += 1
    # 今回 RSS で取れた新しい投稿は archive にも追記（古い投稿が RSS から流れても残す）
    if fresh_for_archive:
        to_store = []
        for it in fresh_for_archive:
            clean = {k: v for k, v in it.items() if k not in ("is_new", "from_archive", "title_ja")}
            to_store.append(clean)
        save_archive(path, list(archived) + to_store)
    if not added:
        return res
    items = list(by_link.values())
    items.sort(key=lambda i: (
        0 if i.get("pinned") else 1,
        -(datetime.fromisoformat(i["published"]).timestamp() if i.get("published") else 0),
    ))
    items = items[:limit] if limit > 0 else items
    res = dict(res)
    res["items"] = items
    res["count"] = len(items)
    res["archive_added"] = added
    if res.get("status") == "error":
        res["status"] = "partial"
    return res


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
  .chips {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }}
  .chip {{
    font: inherit; font-size: 11.5px; line-height: 1.5; padding: 3px 10px;
    border-radius: 999px; border: 1px solid #e4e6ea; background: #fff;
    color: var(--text-2); cursor: pointer; -webkit-appearance: none; appearance: none;
  }}
  .chip .n {{ font-size: 10px; color: var(--muted); margin-left: 4px; }}
  .chip.is-on {{ background: #1a56c4; border-color: #1a56c4; color: #fff; }}
  .chip.is-on .n {{ color: rgba(255,255,255,.85); }}
  .chip.is-zero {{ color: #b0b5bd; }}
  .noresult {{
    margin: 18px 4px; font-size: 12.5px; color: var(--muted); text-align: center;
  }}
  mark.kw {{ background: #fff08a; color: inherit; border-radius: 2px; padding: 0 1px; }}
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
{chips_block}  </div>
  <main class="cards" id="cards">
{cards}
  </main>
  <p class="noresult hidden" id="noresult"></p>
  <footer><span>{summary}</span><span>{footer_right}</span></footer>
</div>
<script id="board-data" type="application/json">{data_json}</script>
{board_js}
</body>
</html>
"""


PAGE = PAGE.replace("{{", "{").replace("}}", "}")

# ページ内の絞り込みUI。settings.keyword_chips があるボードだけ、キーワードのチップが
# 出る（押すとその語を含む投稿だけを残し、語を黄色でハイライト）。無いボードは
# これまでどおり検索欄だけが効く。
BOARD_JS = """<script>
(function () {
  var q = document.getElementById('q');
  var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
  var chips = Array.prototype.slice.call(document.querySelectorAll('.chip'));
  var active = [];              // 選ばれているチップ [{terms: [...], label: '...'}]
  var allItems = [];
  cards.forEach(function (c) {
    var its = Array.prototype.slice.call(c.querySelectorAll('.item'));
    c.dataset.total = its.length;
    its.forEach(function (it) {
      it.dataset.text = it.textContent.toLowerCase();
      allItems.push(it);
    });
  });
  function activeTerms() {
    var out = [];
    active.forEach(function (a) { out = out.concat(a.terms); });
    return out;
  }
  function hasTerm(text, terms) {
    for (var i = 0; i < terms.length; i++) {
      if (text.indexOf(terms[i]) !== -1) return true;
    }
    return false;
  }
  function apply() {
    var v = q.value.trim().toLowerCase();
    var terms = activeTerms();
    var filtering = !!v || terms.length > 0;
    var shownTotal = 0;
    cards.forEach(function (c) {
      var shown = 0;
      Array.prototype.slice.call(c.querySelectorAll('.item')).forEach(function (it) {
        var hit = (!v || it.dataset.text.indexOf(v) !== -1)
               && (!terms.length || hasTerm(it.dataset.text, terms));
        it.classList.toggle('hidden', !hit);
        if (hit) shown++;
      });
      var count = c.querySelector('.count');
      if (count) {
        if (c.dataset.error === '1') { /* エラー表示はそのまま */ }
        else if (!filtering) { count.textContent = c.dataset.total + '件'; }
        else { count.textContent = shown + '/' + c.dataset.total + '件'; }
      }
      c.classList.toggle('hidden', filtering && shown === 0);
      shownTotal += shown;
    });
    var nr = document.getElementById('noresult');
    if (nr) {
      if (filtering && shownTotal === 0) {
        var names = active.map(function (a) { return a.label; });
        if (v) names.push(q.value.trim());
        nr.textContent = '「' + names.join('・') + '」に該当する投稿はありません';
        nr.classList.remove('hidden');
      } else {
        nr.classList.add('hidden');
      }
    }
  }
  function clearMarks(root) {
    Array.prototype.slice.call(root.querySelectorAll('mark.kw')).forEach(function (m) {
      m.parentNode.replaceChild(document.createTextNode(m.textContent), m);
    });
    root.normalize();
  }
  function markText(root, terms) {
    clearMarks(root);
    if (!terms.length) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function (node) {
      var text = node.nodeValue;
      if (!text) return;
      var lower = text.toLowerCase(), hits = [];
      terms.forEach(function (t) {
        var idx = 0;
        while ((idx = lower.indexOf(t, idx)) !== -1) {
          hits.push([idx, idx + t.length]);
          idx += t.length;
        }
      });
      if (!hits.length) return;
      hits.sort(function (a, b) { return a[0] - b[0]; });
      var merged = [];
      hits.forEach(function (h) {
        var last = merged[merged.length - 1];
        if (last && h[0] <= last[1]) last[1] = Math.max(last[1], h[1]);
        else merged.push(h.slice());
      });
      var frag = document.createDocumentFragment(), pos = 0;
      merged.forEach(function (h) {
        if (h[0] > pos) frag.appendChild(document.createTextNode(text.slice(pos, h[0])));
        var m = document.createElement('mark');
        m.className = 'kw';
        m.textContent = text.slice(h[0], h[1]);
        frag.appendChild(m);
        pos = h[1];
      });
      if (pos < text.length) frag.appendChild(document.createTextNode(text.slice(pos)));
      node.parentNode.replaceChild(frag, node);
    });
  }
  function marks() {
    var terms = activeTerms();
    allItems.forEach(function (it) {
      Array.prototype.slice.call(it.querySelectorAll('.title, .trans, .desc')).forEach(function (el) {
        markText(el, terms);
      });
    });
  }
  chips.forEach(function (ch) {
    var label = ch.dataset.label || ch.dataset.kw || '';
    var terms = (ch.dataset.kw || '').split('|').map(function (s) {
      return s.trim().toLowerCase();
    }).filter(Boolean);
    var n = allItems.filter(function (it) { return hasTerm(it.dataset.text, terms); }).length;
    var num = document.createElement('span');
    num.className = 'n';
    num.textContent = n;
    ch.appendChild(num);
    if (!n) ch.classList.add('is-zero');
    ch.addEventListener('click', function () {
      var idx = -1;
      active.forEach(function (a, i) { if (a.label === label) idx = i; });
      if (idx === -1) active.push({terms: terms, label: label}); else active.splice(idx, 1);
      ch.classList.toggle('is-on', idx === -1);
      apply();
      marks();
    });
  });
  q.addEventListener('input', apply);
})();
</script>"""


def render_chips(settings: dict) -> str:
    """settings.keyword_chips から絞り込みチップを作る（無ければ何も出さない）。

    書き方は2とおり:
      "再開発"                                          → その語だけで絞り込むチップ
      {"label": "新築・供給", "terms": ["新築", "供給"]} → 複数の語をまとめた1つのチップ
    """
    buttons = []
    for entry in settings.get("keyword_chips") or []:
        if isinstance(entry, dict):
            terms = [str(t).strip() for t in (entry.get("terms") or []) if str(t).strip()]
            label = str(entry.get("label") or "・".join(terms)).strip()
        else:
            label = str(entry).strip()
            terms = [label] if label else []
        if not label or not terms:
            continue
        buttons.append(
            f'<button type="button" class="chip" data-kw="{esc("|".join(terms))}"'
            f' data-label="{esc(label)}">{esc(label)}</button>'
        )
    if not buttons:
        return ""
    return f'    <div class="chips" id="chips">{"".join(buttons)}</div>\n'


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
        .replace("{chips_block}", render_chips(settings))
        .replace("{summary}", esc(summary))
        .replace("{footer_right}", esc(footer_right))
        .replace("{data_json}", payload)
        .replace("{board_js}", BOARD_JS)
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
    src_by_id = {s.get("id"): s for s in sources}
    for res in fetched:
        fixed = merge_archive(res, src_by_id.get(res["id"]) or {}, settings)
        if fixed is not res:
            fetched[fetched.index(res)] = fixed
    for res in fetched:
        for item in res["items"]:
            if item.get("from_archive"):
                # archive（過去分）は「新着」にしない
                item["is_new"] = False
                continue
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
    # 新規承認銘柄の自動同期: ipo-approved の archive に増えた社名を
    # sources-ipo-news.json（ipo-news.html）へ自動で足す。
    # archive は ipo-approved ボードの取得時に update.py 自身が追記するので、
    # ここで一度読むだけで「新しい新規承認が出たら次の回から監視に加わる」が成立する。
    # 失敗しても他のボードの更新は止めない。
    if not args.config:
        try:
            import sync_ipo_approved_news

            added = sync_ipo_approved_news.sync(verbose=bool(args.verbose))
            if added:
                print(f"IPO新規承認ニュース: 新規 {len(added)} 社を監視に追加（{', '.join(added)}）")
        except Exception as exc:  # noqa: BLE001
            print(f"IPO新規承認ニュースの同期をスキップ: {exc}", file=sys.stderr)

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
