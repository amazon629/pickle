#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公式サイトのニュース/お知らせページから日付＋タイトル＋リンクを抜く（標準ライブラリのみ）。

update.py に組み込む `html_news` の試作。ここでは探索・検証用に使う。
"""
from __future__ import annotations

import html as html_lib
import http.cookiejar
import re
import time
import unicodedata
import urllib.parse
import urllib.request

_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
_OPENER.addheaders = [
    ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
    ("Accept-Language", "ja,en;q=0.8"),
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

DATE_RE = re.compile(
    r"(?:(\d{4})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})\s*日?"
    r"|令和\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日)"
)

NEWS_HINT_RE = re.compile(
    r"(news|topics|information|info|press|release|whatsnew|what-s-new|"
    r"お知らせ|おしらせ|ニュース|新着|トピックス|プレスリリース|告知)",
    re.I,
)

JUNK_HOSTS = (
    "prtimes.jp", "news.google.com", "google.", "wikipedia.org", "facebook.com",
    "instagram.com", "twitter.com", "x.com", "youtube.com", "tiktok.com",
    "rakuten.co.jp", "amazon.co.jp", "amazon.com", "yahoo.co.jp", "shopping.yahoo",
    "indeed.com", "recruit.", "oricon.co.jp", "tabelog.com", "note.com",
    "linkedin.com", "apps.apple.com", "play.google.com", "ecNav".lower(),
)

BLOCK_HOSTS = (
    "prtimes.jp", "google.", "wikipedia.org", "facebook.com", "instagram.com",
    "twitter.com", "x.com", "youtube.com", "tiktok.com", "rakuten.co.jp",
    "amazon.", "yahoo.co.jp", "indeed.com", "oricon.co.jp", "tabelog.com",
    "note.com", "linkedin.com", "apps.apple.com", "play.google.com",
    "kakaku.com", "monotaRO".lower(), "shopify.com", "stores.jp", "base.ec",
    "ameblo.jp", "seesaa.net", "fc2.com", "blog.jp", "hatena", "nikkei.com",
    "prtimes", "value-press", "dreamnews", "atpress", "cnn.co.jp",
)


def http_get(url: str, timeout: int = 20, retries: int = 1):
    last = ""
    for i in range(retries + 1):
        try:
            with _OPENER.open(url, timeout=timeout) as r:
                raw = r.read()
                charset = r.headers.get_content_charset()
                try:
                    text = raw.decode(charset or "utf-8", "ignore")
                except LookupError:
                    text = raw.decode("utf-8", "ignore")
                if "charset=" in text[:2000].lower() and charset is None:
                    m = re.search(r"charset=[\"']?([\w\-]+)", text[:2000], re.I)
                    if m:
                        try:
                            text = raw.decode(m.group(1), "ignore")
                        except LookupError:
                            pass
                return r.status, r.geturl(), text
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
            if i < retries:
                time.sleep(1.5)
    return "ERR", url, last


def strip_tags(fragment: str) -> str:
    s = re.sub(r"(?is)<\s*(script|style)\b.*?</\s*\1\s*>", " ", fragment)
    s = re.sub(r"(?is)<\s*br\s*/?\s*>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html_lib.unescape(s)
    return re.sub(r"[ \t\u3000\xa0]+", " ", s).strip()


def parse_jp_date(text: str):
    m = DATE_RE.search(text or "")
    if not m:
        return None
    if m.group(1):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        y, mo, d = 2018 + int(m.group(4)), int(m.group(5)), int(m.group(6))
    if not (2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


BLOCK_RE = re.compile(r"<(li|tr|article|div|dd|section)\b[^>]*>", re.I)


def _blocks(h: str):
    """<li>/<tr> などのブロックを素朴に切り出す（入れ子は浅い前提）。"""
    out = []
    for m in BLOCK_RE.finditer(h):
        tag = m.group(1).lower()
        end = h.find(f"</{tag}>", m.end())
        if end == -1 or end - m.end() > 6000:
            continue
        out.append(h[m.start(): end + len(tag) + 3])
    return out


def parse_entries(h: str, base_url: str, max_items: int = 60) -> list:
    """HTML から (date, title, link) を拾う。"""
    h = re.sub(r"(?is)<\s*(script|style)\b.*?</\s*\1\s*>", " ", h)
    entries, seen = [], set()

    def add(date, title, href):
        if not date or not title or not href:
            return
        if href.startswith(("javascript:", "#", "mailto:")):
            return
        link = urllib.parse.urljoin(base_url, href)
        title = re.sub(r"\s+", " ", title).strip()
        title = DATE_RE.sub("", title).strip(" 　|,-–—")
        if len(title) < 4:
            return
        key = (link, title)
        if key in seen:
            return
        seen.add(key)
        entries.append({"date": date, "title": title, "link": link})

    # 1) ブロック単位（li/tr/...）に日付＋リンク＋見出しを拾う
    for b in _blocks(h):
        text = strip_tags(b)
        date = parse_jp_date(text)
        if not date:
            continue
        anchors = re.findall(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.S | re.I)
        if not anchors:
            continue
        href, inner = anchors[0]
        title = strip_tags(inner) or ""
        title = DATE_RE.sub("", title).strip()
        if len(title) < 4:
            # タイトルがリンクの外（同じブロック内の見出し）にある型
            rest = strip_tags(re.sub(r'<a\b[^>]*>.*?</a>', " ", b, flags=re.S | re.I))
            rest = DATE_RE.sub("", rest).strip()
            title = rest[:120]
        add(date, title, href)
        if len(entries) >= max_items:
            return entries

    if entries:
        return entries

    # 2) フォールバック: リンクの手前/中に日付がある型
    for m in re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', h, re.S | re.I):
        href, inner = m.group(1), m.group(2)
        inner_text = strip_tags(inner)
        date = parse_jp_date(inner_text)
        if date:
            add(date, inner_text, href)
            continue
        before = strip_tags(h[max(0, m.start() - 400): m.start()])
        date = parse_jp_date(before[-40:]) or parse_jp_date(before)
        if date and len(inner_text) >= 4:
            add(date, inner_text, href)
        if len(entries) >= max_items:
            break
    return entries


def find_news_links(home_html: str, base_url: str, limit: int = 4) -> list:
    """トップページから「お知らせ/ニュース」系のリンクを探す。"""
    out, seen = [], set()
    for m in re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', home_html, re.S | re.I):
        href, inner = m.group(1), strip_tags(m.group(2))
        if href.startswith(("javascript:", "#", "mailto:")):
            continue
        url = urllib.parse.urljoin(base_url, href)
        if urllib.parse.urlparse(url).netloc != urllib.parse.urlparse(base_url).netloc:
            continue
        if not NEWS_HINT_RE.search(href + " " + inner):
            continue
        if re.search(r"(login|cart|mypage|member|entry|contact|company|recruit|privacy)", url, re.I):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= limit:
            break
    return out
