"""Добор полного текста, автора и даты страниц из выдачи Яндекса прямым GET.

Площадки: Дзен (/a/, /b/), Пикабу, vc.ru, Хабр — robots.txt разрешает. Отзовик и IRecommend НЕ обходим (блокируют).
Вежливо: пауза ≥ 1,5 с между запросами (Пикабу — 3 с: Crawl-Delay для ботов), после отказа площадки (не 200, капча)
к ней в этом прогоне больше не обращаемся. Капчу и защиту не обходим.
Разбор — контракт формата (встроенный JSON, JSON-LD, разметка), не смысл: «по делу ли» решает модель-разметчик.
enrich(url) -> {"text", "author_key", "date", "metrics", "comments": [{"text","author_key","date","likes"}]} | None
"""
import html as _html, http.cookiejar, json, re, time, urllib.parse, urllib.request
from html.parser import HTMLParser

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
PAUSE = {"pikabu.ru": 3.0}
DEFAULT_PAUSE = 1.5
_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))
_last = {}
BLOCKED = set()  # площадки, отказавшие в этом прогоне (не 200 или «не та страница»: капча, заглушка, вход)
WRONG = "wrong_page"  # 200, но адрес ушёл с площадки или нет ожидаемой разметки — считать отказом
_dzen_ok = [False]  # куки SSO Дзена уже получены: дальше одна попытка на адрес


def host_of(url):
    return urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.").removeprefix("m.")


def supported(url):
    h, path = host_of(url), urllib.parse.urlsplit(url).path
    if h == "dzen.ru":
        return path.startswith(("/a/", "/b/"))
    if h == "pikabu.ru":
        return path.startswith("/story/")
    if h == "habr.com":
        return "/articles/" in path or "/posts/" in path or "/news/" in path
    return h == "vc.ru" and bool(re.search(r"/\d{5,}-", path))


def _get(url, enc=None):
    h = host_of(url)
    wait = PAUSE.get(h, DEFAULT_PAUSE) - (time.time() - _last.get(h, 0))
    if wait > 0:
        time.sleep(wait)
    try:
        r = _opener.open(urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"}), timeout=30)
        data, final, st = r.read(), r.geturl(), r.status
        cs = enc or r.headers.get_content_charset() or "utf-8"
    except Exception as e:
        _last[h] = time.time()
        return getattr(e, "code", None), "", url
    _last[h] = time.time()
    return st, data.decode(cs, "replace"), final


def _ld(page):
    out = []
    for m in re.finditer(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page, re.S):
        try:
            j = json.loads(m.group(1))
        except ValueError:
            continue
        for x in (j if isinstance(j, list) else j.get("@graph", [j])):
            if isinstance(x, dict) and x.get("@type") in ("Article", "NewsArticle", "BlogPosting", "SocialMediaPosting"):
                out.append(x)
    return out[0] if out else {}


def _strip(h):
    t = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>|</blockquote>", "\n", h or "", flags=re.I)
    t = _html.unescape(re.sub(r"<[^>]+>", "", t))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", t)).strip()


class _TextIn(HTMLParser):
    """Текст внутри элементов, у которых id == eid или в class есть cls (с учётом вложенности)."""

    def __init__(self, eid=None, cls=None):
        super().__init__()
        self.eid, self.cls, self.depth, self.buf = eid, cls, 0, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.depth:
            if tag not in ("br", "img", "hr", "input", "meta", "link", "source", "wbr"):
                self.depth += 1
            if tag in ("p", "br", "li", "h2", "h3", "blockquote"):
                self.buf.append("\n")
        elif (self.eid and a.get("id") == self.eid) or (self.cls and self.cls in (a.get("class") or "").split()):
            self.depth = 1
            self.buf.append("\n")

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1

    def handle_data(self, d):
        if self.depth:
            self.buf.append(d)

    def text(self):
        return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", "".join(self.buf))).strip()


def _text_in(page, eid=None, cls=None):
    p = _TextIn(eid, cls)
    try:
        p.feed(page)
    except Exception:
        pass
    return p.text()


def _json_at(page, key):
    """Значение JSON сразу после "key": (объект, массив или строка) — встроенные данные страницы."""
    i = page.find(f'"{key}":')
    if i < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(page, i + len(key) + 3)[0]
    except ValueError:
        return None


def _iso_ms(ms):
    import datetime as dt
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ms else None


def _iso_any(s):
    import datetime as dt
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _dzen(url):
    st, page, final = 0, "", url
    for _ in range(1 if _dzen_ok[0] else 3):  # первый раз 1-й и 2-й GET уходят на SSO и ставят куки; дальше страница
        st, page, final = _get(url)
        if st == 200 and host_of(final) == "dzen.ru" and '"publisher"' in page:
            _dzen_ok[0] = True
            break
    if st != 200:
        return None, st
    if host_of(final) != "dzen.ru" or '"publisher"' not in page:
        return None, WRONG
    pub = _json_at(page, "publisher") or {}
    text = ""
    cs = _json_at(page, "contentState")
    if isinstance(cs, str):
        try:
            cj = json.loads(cs)
            blocks = (cj.get("draftJsState") or {}).get("blocks") or []  # статья /a/
            text = "\n".join(b.get("text", "").strip() for b in blocks if isinstance(b, dict) and b.get("text", "").strip())
            if not text:  # пост /b/ (brief): rich_text.json — дерево text / tag br
                def walk(nodes):
                    for n in nodes or []:
                        if isinstance(n, dict):
                            if n.get("type") == "text":
                                yield n.get("data") or ""
                            elif n.get("name") in ("br", "p"):
                                yield "\n"
                            yield from walk(n.get("children"))
                text = re.sub(r"\n{3,}", "\n\n", "".join(walk((cj.get("rich_text") or {}).get("json")))).strip()
        except (ValueError, AttributeError):
            text = ""
    if not text:  # пост /b/ или иной формат: описание страницы
        m = re.search(r'<meta[^>]+(?:property|name)="(?:og:)?description"[^>]+content="([^"]*)"', page)
        text = _html.unescape(m.group(1)) if m else ""
    title = _json_at(page, "title")
    pt = _json_at(page, "publishTime")
    return {"text": "\n".join(x for x in (title if isinstance(title, str) else "", text) if x).strip(),
            "author_key": f"dzen:{pub.get('id')}" if pub.get("id") else "",
            "date": _iso_ms(pt) if isinstance(pt, (int, float)) else None,
            "metrics": {k: v for k, v in {"comments": _json_at(page, "commentsCount")}.items() if isinstance(v, int)},
            "comments": []}, st


def _pikabu(url):
    st, page, final = _get(url, enc="windows-1251")  # Пикабу отдаёт windows-1251
    if st != 200:
        return None, st
    ld = _ld(page)
    if host_of(final) != "pikabu.ru" or "story__content-inner" not in page or not ld:
        return None, WRONG
    i = page.find("story__content-inner")
    j = page.find('class="story__footer', i)
    body = _text_in(page[i - 200 if i > 200 else 0: j if j > 0 else None], cls="story-block_type_text") if i >= 0 else ""
    a = ld.get("author") or {}
    com = []
    for c in ld.get("comment") or []:
        if isinstance(c, dict) and c.get("text"):
            ca = c.get("author") or {}
            com.append({"text": _strip(c["text"]), "author_key": "pikabu:" + ca["url"].rsplit("@", 1)[-1].lower() if "@" in (ca.get("url") or "") else "",
                        "date": _iso_any(c.get("dateCreated")), "likes": None})
    title = ld.get("headline") or ld.get("name") or ""
    return {"text": "\n".join(x for x in (title, body) if x).strip(),
            "author_key": "pikabu:" + a["url"].rsplit("@", 1)[-1].lower() if "@" in (a.get("url") or "") else "",
            "date": _iso_any(ld.get("datePublished")),
            "metrics": {"comments": int(ld["commentCount"])} if str(ld.get("commentCount", "")).isdigit() else {},
            "comments": com}, st


def _vc(url):
    st, page, final = _get(url)
    if st != 200:
        return None, st
    if host_of(final) != "vc.ru" or "application/ld+json" not in page:
        return None, WRONG
    ld = _ld(page)
    a = ld.get("author") or {}
    m = re.search(r"vc\.ru/(id\d+|[A-Za-z0-9_.-]+)/?$", a.get("url") or "")  # /id123 или ник автора /neyrobit
    title = re.sub(r"\s+—\s+[^—]+ на vc\.ru$", "", ld.get("headline") or "")
    return {"text": "\n".join(x for x in (title, ld.get("text") or "") if x).strip(),
            "author_key": f"vc:{m.group(1).lower()}" if m else "",
            "date": _iso_any(ld.get("datePublished")), "metrics": {}, "comments": []}, st


def _habr(url):
    st, page, final = _get(url)
    if st != 200:
        return None, st
    if host_of(final) != "habr.com" or "post-content-body" not in page:
        return None, WRONG
    ld = _ld(page)
    m = re.search(r'href="/ru/users/([^/"]+)/"[^>]*class="tm-user-info__username', page)
    return {"text": "\n".join(x for x in (ld.get("headline") or "", _text_in(page, eid="post-content-body")) if x).strip(),
            "author_key": f"habr:{m.group(1).lower()}" if m else "",
            "date": _iso_any(ld.get("datePublished")), "metrics": {}, "comments": []}, st


def enrich(url):
    """Полная страница. Возвращает (dict | None, http_status)."""
    h = host_of(url)
    if h in BLOCKED or not supported(url):
        return None, None
    fn = {"dzen.ru": _dzen, "pikabu.ru": _pikabu, "vc.ru": _vc, "habr.com": _habr}[h]
    try:
        res, st = fn(url)
    except Exception as e:  # сбой разбора одной страницы не роняет добор
        return None, f"{type(e).__name__}: {e}"
    if st == WRONG or (isinstance(st, int) and st not in (200, 404, 410)):
        BLOCKED.add(h)  # отказ (капча, заглушка, вход, 403, 429, 5xx) — к площадке больше не идём
    if res and len(res.get("text") or "") < 80:
        return None, st
    return res, st
