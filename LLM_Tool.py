"""
Tools for LLM — web search / fetch / research / extractive-answer tools for LLMs.

No model code. No Ollama. Just retrieval + ranking.
Default: English-only results. Override with allow_domains.

Quick start:
    from tools_for_llm import webtool
    wt = webtool()

    wt.search("python 3.13")                              # -> list[dict]
    wt.fetch("https://docs.python.org/3/whatsnew/3.13.html")
    wt.research("what changed in python 3.13")
    wt.answer("what changed in python 3.13")              # -> str, no model
    wt.answer("...", top_k=20, max_len=3000)
    wt.save("./")

    # Allow non-English sources explicitly:
    wt = webtool(allow_domains=[".jp", ".cn", ".kr", ".ru"])
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse, parse_qs, unquote

try:
    import httpx
except ImportError as e:
    raise ImportError("pip install httpx") from e

try:
    import trafilatura
except ImportError:
    trafilatura = None

__all__ = ["webtool"]
__version__ = "1.1.0"


# ─────────────────────────────────────────────────────────────
# English-default rules
# ─────────────────────────────────────────────────────────────
NON_ENGLISH_TLDS = (
    ".jp", ".cn", ".kr", ".ru", ".tw", ".hk", ".vn", ".th",
    ".sa", ".ae", ".il", ".ir", ".tr", ".pl", ".cz", ".hu",
)

# A chunk is "English enough" if >= this ratio of chars are ASCII.
ASCII_ENGLISH_THRESHOLD = 0.85


# ─────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────
def _sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()


def _hash(*parts) -> str:
    return hashlib.sha256("||".join(map(str, parts)).encode()).hexdigest()[:32]


def _strip_html(html: str) -> str:
    html = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    html = html.replace("&nbsp;", " ").replace("&amp;", "&")
    html = html.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    html = re.sub(r"&#\d+;", " ", html)
    return re.sub(r"\s+", " ", html).strip()


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _normalize_url(url: str) -> str:
    try:
        p = urlparse(url)
        return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "", "", ""))
    except Exception:
        return url


def _ddg_unwrap(href: str) -> str:
    if "uddg=" in href:
        try:
            q = parse_qs(urlparse(href).query)
            if "uddg" in q:
                return unquote(q["uddg"][0])
        except Exception:
            pass
    return href


def _split_sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", text)


def _split_paragraphs(text: str) -> list[str]:
    return re.split(r"\n{2,}", text)


def _ascii_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for c in text if ord(c) < 128) / len(text)


# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────
@dataclass
class Config:
    provider: str = "duckduckgo"

    searx_url: str = "http://localhost:8080"
    brave_key: str = ""
    tavily_key: str = ""
    serper_key: str = ""

    results_per_query: int = 12
    max_pages: int = 10
    chunks_per_source: int = 6
    context_budget_chars: int = 40000

    search_timeout: float = 12.0
    fetch_timeout: float = 15.0

    cache_ttl: int = 3600
    cache_path: str = "cache/.webtool_cache.db"

    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0 Safari/537.36"
    )
    allow_domains: list[str] = field(default_factory=list)
    block_domains: list[str] = field(default_factory=list)

    # language controls
    english_only: bool = True
    ascii_threshold: float = ASCII_ENGLISH_THRESHOLD


# ─────────────────────────────────────────────────────────────
# cache
# ─────────────────────────────────────────────────────────────
class _Cache:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, exp REAL)")
        self.conn.commit()

    def get(self, key: str) -> Optional[Any]:
        row = self.conn.execute("SELECT v, exp FROM kv WHERE k=?", (key,)).fetchone()
        if not row:
            return None
        v, exp = row
        if exp and exp < time.time():
            self.conn.execute("DELETE FROM kv WHERE k=?", (key,))
            self.conn.commit()
            return None
        try:
            return json.loads(v)
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl: int) -> None:
        self.conn.execute(
            "REPLACE INTO kv (k, v, exp) VALUES (?, ?, ?)",
            (key, json.dumps(value), time.time() + ttl),
        )
        self.conn.commit()


# ─────────────────────────────────────────────────────────────
# search result
# ─────────────────────────────────────────────────────────────
@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    published: str = ""
    source: str = ""


# ─────────────────────────────────────────────────────────────
# providers
# ─────────────────────────────────────────────────────────────
FRESH_DDG = {"day": "d", "week": "w", "month": "m", "year": "y"}
FRESH_BRAVE = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}
FRESH_SERPER = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}


async def _search_duckduckgo(client, q, cfg, n, fresh):
    data = {"q": q, "kl": "us-en"}
    if fresh:
        data["df"] = FRESH_DDG.get(fresh, "")
    r = await client.post(
        "https://html.duckduckgo.com/html/",
        data=data,
        headers={"User-Agent": cfg.user_agent, "Accept-Language": "en-US,en;q=0.9"},
        timeout=cfg.search_timeout,
    )
    r.raise_for_status()
    html = r.text
    links = re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL)
    snippets = re.findall(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)

    out = []
    for i, (href, title_html) in enumerate(links[:n]):
        url = _ddg_unwrap(href)
        if not url.startswith("http"):
            continue
        out.append(SearchResult(
            title=_strip_html(title_html),
            url=url,
            snippet=_strip_html(snippets[i]) if i < len(snippets) else "",
            source="duckduckgo",
        ))
    return out


async def _search_searx(client, q, cfg, n, fresh):
    params = {"q": q, "format": "json"}
    if fresh:
        params["time_range"] = fresh
    r = await client.get(f"{cfg.searx_url.rstrip('/')}/search", params=params, timeout=cfg.search_timeout)
    r.raise_for_status()
    return [
        SearchResult(
            title=it.get("title", ""),
            url=it.get("url", ""),
            snippet=it.get("content", ""),
            published=it.get("publishedDate", "") or "",
            source=it.get("engine", "searx"),
        )
        for it in r.json().get("results", [])[:n]
    ]


async def _search_brave(client, q, cfg, n, fresh):
    if not cfg.brave_key:
        raise RuntimeError("BRAVE_API_KEY missing")
    headers = {"X-Subscription-Token": cfg.brave_key, "Accept": "application/json"}
    params: dict[str, Any] = {"q": q, "count": n}
    if fresh:
        params["freshness"] = FRESH_BRAVE.get(fresh, "")
    r = await client.get("https://api.search.brave.com/res/v1/web/search",
                         headers=headers, params=params, timeout=cfg.search_timeout)
    r.raise_for_status()
    return [
        SearchResult(
            title=it.get("title", ""),
            url=it.get("url", ""),
            snippet=it.get("description", ""),
            published=it.get("age", "") or "",
            source="brave",
        )
        for it in r.json().get("web", {}).get("results", [])[:n]
    ]


async def _search_tavily(client, q, cfg, n, fresh):
    if not cfg.tavily_key:
        raise RuntimeError("TAVILY_API_KEY missing")
    payload: dict[str, Any] = {"api_key": cfg.tavily_key, "query": q,
                               "max_results": n, "search_depth": "advanced"}
    if fresh:
        payload["topic"] = "news"
        payload["days"] = {"day": 1, "week": 7, "month": 30, "year": 365}.get(fresh, 7)
    r = await client.post("https://api.tavily.com/search", json=payload, timeout=cfg.search_timeout)
    r.raise_for_status()
    return [
        SearchResult(
            title=it.get("title", ""),
            url=it.get("url", ""),
            snippet=it.get("content", ""),
            published=it.get("published_date", "") or "",
            source="tavily",
        )
        for it in r.json().get("results", [])[:n]
    ]


async def _search_serper(client, q, cfg, n, fresh):
    if not cfg.serper_key:
        raise RuntimeError("SERPER_API_KEY missing")
    headers = {"X-API-KEY": cfg.serper_key, "Content-Type": "application/json"}
    payload: dict[str, Any] = {"q": q, "num": n}
    if fresh:
        payload["tbs"] = FRESH_SERPER.get(fresh, "")
    r = await client.post("https://google.serper.dev/search",
                          headers=headers, json=payload, timeout=cfg.search_timeout)
    r.raise_for_status()
    return [
        SearchResult(
            title=it.get("title", ""),
            url=it.get("link", ""),
            snippet=it.get("snippet", ""),
            published=it.get("date", "") or "",
            source="serper",
        )
        for it in r.json().get("organic", [])[:n]
    ]


PROVIDERS = {
    "duckduckgo": _search_duckduckgo,
    "searx": _search_searx,
    "brave": _search_brave,
    "tavily": _search_tavily,
    "serper": _search_serper,
}


# ─────────────────────────────────────────────────────────────
# prompt guide
# ─────────────────────────────────────────────────────────────
PROMPT_GUIDE = """# Web tools

You have access to three tools. Use them whenever the user asks about
current events, statistics, products, prices, people, or anything not
obviously in your training data. Do NOT guess — call a tool.

## 1. web_search(query, n=12, freshness=None)
Returns: [{title, url, snippet, published, source}, ...]
- query: short, keyword-rich. Not a full sentence.
- freshness: "day" | "week" | "month" | "year" | null.

## 2. web_fetch(url)
Returns the cleaned main text of a page (article body, no nav/ads).

## 3. web_research(question, hops=1, freshness=None)
Search → fetch → chunk → rank. Returns:
  {queries, sources: [{n,title,url,published}], context: "[1] ..."}
Prefer this over chaining search+fetch.

## Citation rules
- Cite every factual claim with [n] matching a source number.
- Never invent URLs.
- If sources disagree, say so.
- If sources are insufficient, say what's missing.

## Multi-hop
If a fact is still missing, call web_search again with a NEW, narrower
query. Stop after 2-3 hops."""


# ─────────────────────────────────────────────────────────────
# main class
# ─────────────────────────────────────────────────────────────
class webtool:
    """
    Web search / fetch / research / extractive-answer toolkit.

        wt = webtool()                                    # English-only default
        wt = webtool(allow_domains=[".jp", ".cn"])        # allow those languages
        wt = webtool(english_only=False)                  # disable filter entirely

        wt.search("q", n=20, freshness="week")
        wt.research("q", max_pages=20, context_budget_chars=80000)
        wt.answer("q", top_k=15, max_len=1500, unit="paragraph")
    """

    providers = list(PROVIDERS.keys())

    def __init__(self, provider: str = "duckduckgo", **overrides):
        cfg = Config(provider=provider)
        cfg.brave_key = overrides.pop("brave_key", os.getenv("BRAVE_API_KEY", ""))
        cfg.tavily_key = overrides.pop("tavily_key", os.getenv("TAVILY_API_KEY", ""))
        cfg.serper_key = overrides.pop("serper_key", os.getenv("SERPER_API_KEY", ""))
        cfg.searx_url = overrides.pop("searx_url", os.getenv("SEARX_URL", cfg.searx_url))
        for k, v in overrides.items():
            if not hasattr(cfg, k):
                raise TypeError(f"unknown config field: {k}")
            setattr(cfg, k, v)
        if cfg.provider not in PROVIDERS:
            raise ValueError(f"unknown provider {cfg.provider!r}; try {list(PROVIDERS)}")
        self.cfg = cfg
        self._cache = _Cache(cfg.cache_path)

    # ── config helpers ──
    def _opt(self, name: str, override):
        return self.cfg.__dict__[name] if override is None else override

    # ── language rules ──
    def _explicitly_allowed(self, url: str) -> bool:
        """True if this URL is on the user's allow list.
        Allow-listed domains bypass the English-only filter."""
        d = _domain(url)
        if not d:
            return False
        return bool(self.cfg.allow_domains) and any(
            d.endswith(a) for a in self.cfg.allow_domains
        )

    def _is_english_chunk(self, text: str) -> bool:
        return _ascii_ratio(text) >= self.cfg.ascii_threshold

    # ── domain filter ──
    def _allowed(self, url: str) -> bool:
        d = _domain(url)
        if not d:
            return False

        # 1. hard block always wins
        if self.cfg.block_domains and any(d.endswith(b) for b in self.cfg.block_domains):
            return False

        # 2. explicit allow list
        if self.cfg.allow_domains:
            # user picked these domains — respect them fully,
            # including non-English TLDs
            return any(d.endswith(a) for a in self.cfg.allow_domains)

        # 3. default: English-only → drop obvious non-English TLDs
        if self.cfg.english_only and any(d.endswith(t) for t in NON_ENGLISH_TLDS):
            return False

        return True

    def _dedup(self, results: list[SearchResult]) -> list[SearchResult]:
        seen_u, seen_t, out = set(), set(), []
        for r in results:
            if not r.url or not self._allowed(r.url):
                continue
            u = _normalize_url(r.url)
            t = r.title.strip().lower()[:80]
            if u in seen_u or (t and t in seen_t):
                continue
            seen_u.add(u)
            if t:
                seen_t.add(t)
            out.append(r)
        return out

    # ── search ──
    async def asearch(self, query: str, n: Optional[int] = None,
                      freshness: Optional[str] = None) -> list[dict]:
        n = self._opt("results_per_query", n)
        key = _hash("search", self.cfg.provider, query, n, freshness,
                    self.cfg.english_only, tuple(self.cfg.allow_domains))
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        fn = PROVIDERS[self.cfg.provider]
        async with httpx.AsyncClient() as client:
            for attempt in range(3):
                try:
                    results = self._dedup(await fn(client, query, self.cfg, n, freshness))
                    out = [asdict(r) for r in results]
                    self._cache.set(key, out, self.cfg.cache_ttl)
                    return out
                except Exception as e:
                    wait = 2 ** attempt
                    print(f"[search:{self.cfg.provider}] {e} (retry {wait}s)", file=sys.stderr)
                    await asyncio.sleep(wait)
        return []

    def search(self, query: str, n: Optional[int] = None,
               freshness: Optional[str] = None) -> list[dict]:
        return _sync(self.asearch(query, n, freshness))

    # ── fetch ──
    async def afetch(self, url: str) -> str:
        key = _hash("page", url)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        text = ""
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    url, timeout=self.cfg.fetch_timeout, follow_redirects=True,
                    headers={"User-Agent": self.cfg.user_agent, "Accept-Language": "en"},
                )
                if r.status_code == 403 and "wikipedia.org" in url:
                    text = await self._fetch_wikipedia(client, url)
                else:
                    r.raise_for_status()
                    ct = r.headers.get("content-type", "").lower()
                    text = self._extract_pdf(r.content) if "pdf" in ct else self._extract_html(r.text)
        except Exception as e:
            print(f"[fetch] {url}: {e}", file=sys.stderr)

        self._cache.set(key, text, self.cfg.cache_ttl)
        return text

    def fetch(self, url: str) -> str:
        return _sync(self.afetch(url))

    async def _fetch_wikipedia(self, client, url: str) -> str:
        try:
            title = unquote(urlparse(url).path.rsplit("/", 1)[-1])
            r = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "query", "prop": "extracts", "explaintext": 1,
                        "format": "json", "redirects": 1, "titles": title},
                headers={"User-Agent": "ToolsForLLM/1.1 (https://example.com)"},
                timeout=self.cfg.fetch_timeout,
            )
            r.raise_for_status()
            pages = r.json().get("query", {}).get("pages", {})
            return next(iter(pages.values())).get("extract", "") or ""
        except Exception as e:
            print(f"[wikipedia] {e}", file=sys.stderr)
            return ""

    @staticmethod
    def _extract_pdf(data: bytes) -> str:
        try:
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            return "\n".join((p.extract_text() or "") for p in reader.pages)
        except Exception as e:
            print(f"[pdf] {e}", file=sys.stderr)
            return ""

    @staticmethod
    def _extract_html(html: str) -> str:
        if trafilatura is not None:
            try:
                t = trafilatura.extract(html, include_comments=False, include_tables=True,
                                        favor_precision=True, no_fallback=False)
                if t and len(t) > 200:
                    return t
            except Exception:
                pass
        return _strip_html(html)

    # ── chunk + rank ──
    @staticmethod
    def _chunk(text: str, size: int = 900, overlap: int = 150) -> list[str]:
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return []
        if len(text) <= size:
            return [text]
        out, i = [], 0
        while i < len(text):
            out.append(text[i:i + size])
            i += size - overlap
        return out

    def _rank(self, query: str, chunks: list[dict]) -> list[dict]:
        q_terms = {w.lower() for w in re.findall(r"\w+", query) if len(w) > 2}
        trusted = ("wikipedia.org", ".gov", ".edu", "arxiv.org",
                   "nature.com", "reuters.com", "bbc.", "apnews.com")

        # language filter first — drop non-English chunks
        # unless the domain was explicitly allow-listed
        filtered: list[dict] = []
        for c in chunks:
            if self.cfg.english_only and not self._explicitly_allowed(c["url"]):
                if not self._is_english_chunk(c["text"]):
                    continue
            filtered.append(c)

        for c in filtered:
            words = set(re.findall(r"\w+", c["text"].lower()))
            overlap = len(q_terms & words) / (len(q_terms) + 1e-9)
            bonus = 0.15 if any(t in c["url"] for t in trusted) else 0.0
            recency = 0.10 if c.get("published") else 0.0
            c["score"] = 0.75 * overlap + bonus + recency

        filtered.sort(key=lambda c: c["score"], reverse=True)
        return filtered

    # ── research ──
    async def aresearch(
        self,
        question: str,
        hops: int = 1,
        results_per_query: Optional[int] = None,
        max_pages: Optional[int] = None,
        chunks_per_source: Optional[int] = None,
        context_budget_chars: Optional[int] = None,
        freshness: Optional[str] = None,
    ) -> dict:
        results_per_query = self._opt("results_per_query", results_per_query)
        max_pages = self._opt("max_pages", max_pages)
        chunks_per_source = self._opt("chunks_per_source", chunks_per_source)
        budget = self._opt("context_budget_chars", context_budget_chars)

        queries = [question]
        all_chunks: list[dict] = []
        sources_by_url: dict[str, dict] = {}

        for hop in range(hops):
            groups = await asyncio.gather(*[
                self.asearch(q, n=results_per_query, freshness=freshness if hop == 0 else None)
                for q in queries
            ])
            flat = [r for g in groups for r in g]

            seen: set[str] = set()
            fresh_hits: list[dict] = []
            for r in flat:
                u = _normalize_url(r["url"])
                if u in seen or u in sources_by_url:
                    continue
                seen.add(u)
                fresh_hits.append(r)
            fresh_hits = fresh_hits[:max_pages]
            if not fresh_hits:
                break

            for r in fresh_hits:
                sources_by_url[_normalize_url(r["url"])] = r

            pages = await asyncio.gather(*[self.afetch(r["url"]) for r in fresh_hits])
            for r, text in zip(fresh_hits, pages):
                for ch in self._chunk(text):
                    all_chunks.append({
                        "text": ch,
                        "url": r["url"],
                        "title": r["title"],
                        "published": r.get("published", ""),
                    })

        top = self._rank(question, all_chunks)[: max_pages * chunks_per_source]

        seen_n: dict[str, int] = {}
        sources: list[dict] = []
        lines: list[str] = []
        used = 0
        for c in top:
            key = _normalize_url(c["url"])
            if key not in seen_n:
                seen_n[key] = len(sources) + 1
                sources.append({
                    "n": seen_n[key],
                    "title": c["title"],
                    "url": c["url"],
                    "published": c.get("published", ""),
                    "snippet": sources_by_url.get(key, {}).get("snippet", ""),
                })
            n = seen_n[key]
            block = f"[{n}] {c['title']} — {c['url']}\n{c['text'].strip()}\n\n"
            if used + len(block) > budget:
                break
            lines.append(block)
            used += len(block)

        return {
            "question": question,
            "queries": queries,
            "sources": sources,
            "context": "".join(lines).strip(),
            "chunks": top,
        }

    def research(self, question: str, hops: int = 1, **kwargs) -> dict:
        return _sync(self.aresearch(question, hops=hops, **kwargs))

    # ── extractive answer ──
    async def aanswer(
        self,
        question: str,
        top_k: int = 10,
        unit: str = "paragraph",
        min_len: int = 60,
        max_len: int = 2000,
        context_budget_chars: Optional[int] = None,
        max_pages: Optional[int] = None,
        results_per_query: Optional[int] = None,
        chunks_per_source: Optional[int] = None,
        freshness: Optional[str] = None,
        sources_footer: bool = True,
    ) -> str:
        b = await self.aresearch(
            question,
            results_per_query=results_per_query,
            max_pages=max_pages,
            chunks_per_source=chunks_per_source,
            context_budget_chars=context_budget_chars,
            freshness=freshness,
        )
        return self._extractive(question, b, top_k, unit, min_len, max_len, sources_footer)

    def answer(self, question: str, **kwargs) -> str:
        return _sync(self.aanswer(question, **kwargs))

    @staticmethod
    def _extractive(question: str, bundle: dict, top_k: int, unit: str,
                    min_len: int, max_len: int, sources_footer: bool) -> str:
        context = bundle.get("context", "")
        if not context:
            return "No usable sources."

        splitter = _split_paragraphs if unit == "paragraph" else _split_sentences
        pieces = [p.strip() for p in splitter(context)]
        terms = {w.lower() for w in re.findall(r"\w+", question) if len(w) > 3}

        scored: list[tuple[float, str]] = []
        for p in pieces:
            if not (min_len < len(p) < max_len):
                continue
            words = set(re.findall(r"\w+", p.lower()))
            score = len(terms & words) / (len(terms) + 1e-9)
            if score > 0:
                scored.append((score, p))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = [p for _, p in scored[:top_k]]

        out = "Answer:\n\n" + "\n\n".join(top)
        if sources_footer and bundle.get("sources"):
            out += "\n\nSources:"
            for s in bundle["sources"]:
                out += f"\n  [{s['n']}] {s['url']}"
        return out

    # ── introspection ──
    def tools_json(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": ("Search the web. Returns "
                                    "{title, url, snippet, published, source}. "
                                    "Call multiple times with different phrasings."),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "n": {"type": "integer", "default": 12, "minimum": 1, "maximum": 30},
                            "freshness": {"type": "string",
                                          "enum": ["day", "week", "month", "year"]},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_fetch",
                    "description": "Fetch the cleaned main text of a URL.",
                    "parameters": {
                        "type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_research",
                    "description": ("Search + fetch + rank. Returns "
                                    "{queries, sources, context} where context has [n] markers."),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "hops": {"type": "integer", "default": 1,
                                     "minimum": 1, "maximum": 3},
                            "freshness": {"type": "string",
                                          "enum": ["day", "week", "month", "year"]},
                        },
                        "required": ["question"],
                    },
                },
            },
        ]

    def prompt_guide(self) -> str:
        return PROMPT_GUIDE

    def save(self, dir_: str = ".") -> tuple[str, str]:
        os.makedirs(dir_, exist_ok=True)
        jp = os.path.join(dir_, "webtool.tools.json")
        mp = os.path.join(dir_, "webtool.prompt.md")
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(self.tools_json(), f, indent=2)
        with open(mp, "w", encoding="utf-8") as f:
            f.write(self.prompt_guide())
        return jp, mp

    def __repr__(self) -> str:
        c = self.cfg
        lang = "en-only" if c.english_only else "any-lang"
        return (f"webtool(provider={c.provider!r}, lang={lang}, "
                f"allow={c.allow_domains or '—'}, "
                f"max_pages={c.max_pages}, "
                f"context_budget_chars={c.context_budget_chars})")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("--provider", default="duckduckgo")
    ap.add_argument("--freshness", default=None,
                    choices=["day", "week", "month", "year"])
    ap.add_argument("--research", action="store_true")
    ap.add_argument("--answer", action="store_true")
    ap.add_argument("--unit", default="paragraph", choices=["paragraph", "sentence"])
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--max-len", type=int, default=2000)
    ap.add_argument("--context-budget", type=int, default=None)
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--allow", nargs="*", default=[],
                    help="domains/TLDs to allow (e.g. .jp .cn arxiv.org)")
    ap.add_argument("--any-lang", action="store_true",
                    help="disable English-only filter")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    wt = webtool(
        provider=args.provider,
        allow_domains=args.allow,
        english_only=not args.any_lang,
    )
    q = " ".join(args.query)

    if args.save:
        jp, mp = wt.save(".")
        print(f"wrote {jp} and {mp}")

    if args.answer:
        print(wt.answer(
            q, top_k=args.top_k, unit=args.unit, max_len=args.max_len,
            context_budget_chars=args.context_budget,
            max_pages=args.max_pages, freshness=args.freshness,
        ))
    elif args.research:
        out = wt.research(q, freshness=args.freshness)
        print(out["context"])
        print("\nSOURCES")
        for s in out["sources"]:
            print(f"  [{s['n']}] {s['title']} — {s['url']}")
    else:
        for r in wt.search(q, freshness=args.freshness):
            print(f"- {r['title']}\n  {r['url']}\n  {r['snippet'][:160]}")
