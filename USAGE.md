# Usage Guide

Full reference for `tools_for_llm.webtool`: every method, every parameter, the CLI, and how to wire it into an LLM as a tool.
<br>

## Table of contents

- [Creating a `webtool`](#creating-a-webtool)
- [Methods](#methods)
  - [`search` / `asearch`](#search--asearch)
  - [`fetch` / `afetch`](#fetch--afetch)
  - [`research` / `aresearch`](#research--aresearch)
  - [`answer` / `aanswer`](#answer--aanswer)
  - [`tools_json`](#tools_json)
  - [`prompt_guide`](#prompt_guide)
  - [`save`](#save)
- [Config reference](#config-reference)
- [Providers in detail](#providers-in-detail)
- [Language filtering](#language-filtering)
- [Caching](#caching)
- [CLI reference](#cli-reference)
- [Using with an LLM (function calling)](#using-with-an-llm-function-calling)
- [Troubleshooting](#troubleshooting)
- [Changing the search engine](#changing-the-search-engine)

---

## Creating a `webtool`

```python
from tools_for_llm import webtool

wt = webtool()
```
<br>

| Arg        | Type  | Default        | Description                                                        |
|------------|-------|----------------|----------------------------------------------------------------------|
| `provider` | `str` | `"duckduckgo"` | One of `duckduckgo`, `searx`, `brave`, `tavily`, `serper`           |
| `**overrides` | — | —              | Any [`Config`](#config-reference) field, plus API keys              |
<br>

Full constructor signature:

```python
wt = webtool(provider="duckduckgo", **overrides)
```

---

## Methods

Every method below has a synchronous version and an `a`-prefixed async version (e.g. `search()` / `asearch()`). Use the async versions if you're already inside an event loop.
<br>

### `search` / `asearch`

```python
wt.search(query: str, n: int | None = None, freshness: str | None = None) -> list[dict]
```
<br>

**Example**

```python
wt.search("claude 5 release", n=10, freshness="week")
```

```output
  {"title": "Claude 5 release notes", "url": "https://example.com/claude-5",
   "snippet": "Anthropic details reasoning and coding upgrades...",
   "published": "2026-09-10", "source": "duckduckgo"},
  {"title": "Claude 5: what's new", "url": "https://example.com/whats-new",
   "snippet": "A rundown of the latest model's changes...",
   "published": "2026-09-11", "source": "duckduckgo"}
```
<br>

**Parameters**

- `query` (`str`) — the search query. Keep it short and keyword-rich, not a full sentence.
- `n` (`int | None`, default `None`) — number of results to return. Falls back to `results_per_query` (12) when omitted.
- `freshness` (`str | None`, default `None`) — restrict to recent results: `"day"`, `"week"`, `"month"`, or `"year"`.
<br>

**Returns** — `list[dict]`, each item shaped `{title, url, snippet, published, source}`.

---

### `fetch` / `afetch`

```python
wt.fetch(url: str) -> str
```
<br>

**Example**

```python
wt.fetch("https://docs.python.org/3/whatsnew/3.13.html")
```

```output
"Python 3.13 introduces a new interactive interpreter, an experimental
free-threaded build, and a basic JIT compiler..."
```
<br>

**Parameters**

- `url` (`str`) — the page URL to fetch. HTML is cleaned into article text; PDFs are detected via `content-type` and text-extracted automatically. `wikipedia.org` pages fall back to the Wikipedia API if the direct fetch is blocked (403).
<br>

**Returns** — `str`, the cleaned main text of the page (empty string on failure).

---

### `research` / `aresearch`

```python
wt.research(
    question: str,
    hops: int = 1,
    results_per_query: int | None = None,
    max_pages: int | None = None,
    chunks_per_source: int | None = None,
    context_budget_chars: int | None = None,
    freshness: str | None = None,
) -> dict
```
<br>

**Example**

```python
bundle = wt.research("what changed in python 3.13", hops=1, max_pages=5)
```

```output
{
  "question": "what changed in python 3.13",
  "queries": ["what changed in python 3.13"],
  "sources": [
    {"n": 1, "title": "What's New In Python 3.13",
     "url": "https://docs.python.org/3/whatsnew/3.13.html", "published": "", "snippet": "..."},
    {"n": 2, "title": "Python 3.13 Release Notes",
     "url": "https://www.python.org/downloads/release/python-3130/", "published": "", "snippet": "..."}
  ],
  "context": "[1] What's New In Python 3.13 — https://docs.python.org/...\nPython 3.13 adds a new REPL...\n\n[2] ...",
  "chunks": [ ... ]
}
```
<br>

**Parameters**

- `question` (`str`) — the research question to answer.
- `hops` (`int`, default `1`) — number of search → fetch rounds to run (1–3 recommended). More hops can pull in follow-up queries but cost more requests.
- `results_per_query` (`int | None`, default `None`) — overrides `Config.results_per_query` for this call only.
- `max_pages` (`int | None`, default `None`) — overrides `Config.max_pages` for this call only; caps how many distinct pages get fetched.
- `chunks_per_source` (`int | None`, default `None`) — overrides `Config.chunks_per_source` for this call only; caps ranked chunks kept per source.
- `context_budget_chars` (`int | None`, default `None`) — overrides `Config.context_budget_chars` for this call only; caps total size of the assembled `context` string.
- `freshness` (`str | None`, default `None`) — `"day"`, `"week"`, `"month"`, or `"year"`; applied on the first hop only.
<br>

**Returns** — `dict` shaped `{question, queries, sources, context, chunks}`. `context` is a ready-to-cite string with `[n]` markers matching entries in `sources`. Ranking weighs query-term overlap (0.75), a trust bonus (+0.15) for domains like `wikipedia.org`, `.gov`, `.edu`, `arxiv.org`, `nature.com`, `reuters.com`, `bbc.`, `apnews.com`, and a recency bonus (+0.10) when a published date is present.

---

### `answer` / `aanswer`

```python
wt.answer(
    question: str,
    top_k: int = 10,
    unit: str = "paragraph",
    min_len: int = 60,
    max_len: int = 2000,
    context_budget_chars: int | None = None,
    max_pages: int | None = None,
    results_per_query: int | None = None,
    chunks_per_source: int | None = None,
    freshness: str | None = None,
    sources_footer: bool = True,
) -> str
```
<br>

**Example**

```python
wt.answer("what changed in python 3.13", top_k=5, max_len=1500)
```

```output
OUTPUT:

Python 3.13 introduces a new interactive interpreter based on PyPy's,
an experimental free-threaded build option, and a basic JIT compiler...

Sources:
  [1] https://docs.python.org/3/whatsnew/3.13.html
  [2] https://www.python.org/downloads/release/python-3130/
```
<br>

**Parameters**

- `question` (`str`) — the question to answer.
- `top_k` (`int`, default `10`) — max number of extracted text pieces to include in the answer.
- `unit` (`str`, default `"paragraph"`) — extraction granularity: `"paragraph"` or `"sentence"`.
- `min_len` (`int`, default `60`) — minimum character length for a piece to be considered.
- `max_len` (`int`, default `2000`) — maximum character length for a piece to be considered.
- `context_budget_chars` (`int | None`, default `None`) — passed through to `research()`.
- `max_pages` (`int | None`, default `None`) — passed through to `research()`.
- `results_per_query` (`int | None`, default `None`) — passed through to `research()`.
- `chunks_per_source` (`int | None`, default `None`) — passed through to `research()`.
- `freshness` (`str | None`, default `None`) — passed through to `research()`.
- `sources_footer` (`bool`, default `True`) — append a `Sources:` list of `[n] url` at the end of the answer.
<br>

**Returns** — `str`, an extractive answer built purely from retrieved text — no model call involved.

---

### `tools_json`

```python
wt.tools_json() -> list[dict]
```
<br>

**Example**

```python
wt.tools_json()
```

```output
[
  {"type": "function", "function": {"name": "web_search", "description": "Search the web...", "parameters": { ... }}},
  {"type": "function", "function": {"name": "web_fetch", "description": "Fetch the cleaned main text of a URL.", "parameters": { ... }}},
  {"type": "function", "function": {"name": "web_research", "description": "Search + fetch + rank...", "parameters": { ... }}}
]
```
<br>

**Parameters** — none.
<br>

**Returns** — `list[dict]`, an OpenAI-style function-calling schema for `web_search`, `web_fetch`, and `web_research`, ready to pass to a chat completion API's `tools` parameter.

---

### `prompt_guide`

```python
wt.prompt_guide() -> str
```
<br>

**Example**

```python
wt.prompt_guide()
```

```output
"# Web tools

You have access to three tools. Use them whenever the user asks about
current events, statistics, products, prices, people, or anything not
obviously in your training data. Do NOT guess — call a tool.
..."
```
<br>

**Parameters** — none.
<br>

**Returns** — `str`, built-in Markdown system-prompt text describing how an LLM should use the three tools (citation rules, multi-hop guidance, etc.). Paste it into your system prompt alongside `tools_json()`.

---

### `save`

```python
wt.save(dir_: str = ".") -> tuple[str, str]
```
<br>

**Example**

```python
wt.save("./llm_config")
```

```output
('./llm_config/webtool.tools.json', './llm_config/webtool.prompt.md')
```
<br>

**Parameters**

- `dir_` (`str`, default `"."`) — directory to write the files into. Created if it doesn't exist.
<br>

**Returns** — `tuple[str, str]`, the paths of the written `webtool.tools.json` and `webtool.prompt.md` files.

---

## Config reference

All fields of the internal `Config` dataclass — pass any of these as keyword overrides to `webtool(...)`.
<br>

| Field                   | Type        | Default                         | Description                                                                 |
|-------------------------|-------------|----------------------------------|-------------------------------------------------------------------------------|
| `provider`              | `str`       | `"duckduckgo"`                  | Search backend to use                                                       |
| `searx_url`             | `str`       | `"http://localhost:8080"`       | Base URL of a self-hosted SearX instance                                    |
| `brave_key`             | `str`       | `""` (env `BRAVE_API_KEY`)      | Brave Search API key                                                        |
| `tavily_key`            | `str`       | `""` (env `TAVILY_API_KEY`)     | Tavily API key                                                               |
| `serper_key`            | `str`       | `""` (env `SERPER_API_KEY`)     | Serper (Google) API key                                                     |
| `results_per_query`     | `int`       | `12`                             | Default number of results to request per search                            |
| `max_pages`              | `int`       | `10`                             | Max distinct pages fetched per research call                                |
| `chunks_per_source`     | `int`       | `6`                              | Max ranked chunks kept per source in research output                        |
| `context_budget_chars`  | `int`       | `40000`                          | Max total characters assembled into the research `context` string           |
| `search_timeout`        | `float`     | `12.0`                           | Timeout (seconds) for search requests                                       |
| `fetch_timeout`         | `float`     | `15.0`                           | Timeout (seconds) for page fetch requests                                   |
| `cache_ttl`             | `int`       | `3600`                           | Cache lifetime in seconds                                                   |
| `cache_path`            | `str`       | `"cache/.webtool_cache.db"`     | SQLite cache file path                                                      |
| `user_agent`            | `str`       | Chrome UA string                 | User-Agent header sent with requests                                        |
| `allow_domains`         | `list[str]` | `[]`                             | Domains/TLDs to always allow, bypassing the English-only filter             |
| `block_domains`         | `list[str]` | `[]`                             | Domains/TLDs to always exclude (checked before anything else)               |
| `english_only`          | `bool`      | `True`                           | Enable/disable the default English-only filtering                           |
| `ascii_threshold`       | `float`     | `0.85`                           | Min ratio of ASCII characters for a text chunk to count as "English enough" |

---

## Providers in detail

| Provider     | Setup                                             | Freshness values                     |
|--------------|-----------------------------------------------------|----------------------------------------|
| `duckduckgo` | No key needed. Scrapes `html.duckduckgo.com`.       | `day` `week` `month` `year`           |
| `searx`      | Requires a running SearX instance (`searx_url`).    | Passed through as `time_range`         |
| `brave`      | Requires `BRAVE_API_KEY`.                           | `day` `week` `month` `year`           |
| `tavily`     | Requires `TAVILY_API_KEY`. Uses `search_depth="advanced"`. | `day` `week` `month` `year` (as `days`) |
| `serper`     | Requires `SERPER_API_KEY`. Google results via Serper.| `day` `week` `month` `year` (as `tbs`) |
<br>

All providers retry up to 3 times with exponential backoff on failure.

---

## Language filtering

By default (`english_only=True`), Tools for LLM filters at two levels:
<br>

1. **Domain level** — search results from a fixed list of non-English TLDs are dropped (`.jp`, `.cn`, `.kr`, `.ru`, `.tw`, `.hk`, `.vn`, `.th`, `.sa`, `.ae`, `.il`, `.ir`, `.tr`, `.pl`, `.cz`, `.hu`), unless the domain also appears in `allow_domains`.
2. **Chunk level** — during `research()`, individual text chunks are kept only if their ASCII-character ratio meets `ascii_threshold` (default `0.85`), unless their domain is in `allow_domains`.
<br>

Ways to change this behavior:

```python
webtool(allow_domains=[".jp", ".cn"])   # whitelist specific domains/TLDs, filter stays on for everything else
webtool(english_only=False)             # turn the filter off entirely
webtool(ascii_threshold=0.6)            # loosen the chunk-level ASCII requirement
```
<br>

`block_domains` is independent of language filtering and always wins — any domain listed there is excluded regardless of other settings.

---

## Caching

Search and fetch results are cached in a local SQLite database (`cache_path`, default `cache/.webtool_cache.db`) for `cache_ttl` seconds (default 1 hour). The cache key includes the query/URL plus relevant config, so changing `provider`, `english_only`, or `allow_domains` produces distinct cache entries.
<br>

```python
wt = webtool(cache_path="./my_cache.db", cache_ttl=86400)  # cache for 24h
```

---

## CLI reference

```bash
python tools_for_llm.py QUERY [options]
```
<br>

| Flag                | Values                                      | Default        | Description                                   |
|----------------------|------------------------------------------------|-----------------|-------------------------------------------------|
| `QUERY`              | free text (positional, can be multiple words)| —               | The search query / question                    |
| `--provider`         | `duckduckgo` `searx` `brave` `tavily` `serper` | `duckduckgo`   | Search backend                                  |
| `--freshness`        | `day` `week` `month` `year`                   | none            | Time filter                                     |
| `--research`         | flag                                          | off             | Run the full research pipeline instead of search|
| `--answer`           | flag                                          | off             | Run extractive answer instead of search         |
| `--unit`             | `paragraph` `sentence`                        | `paragraph`     | Extraction granularity for `--answer`           |
| `--top-k`            | int                                           | `10`            | Max extracted pieces for `--answer`             |
| `--max-len`          | int                                           | `2000`          | Max piece length for `--answer`                 |
| `--context-budget`   | int                                           | unset (config)  | Max context characters                          |
| `--max-pages`        | int                                           | unset (config)  | Max pages fetched                               |
| `--allow`            | list of domains/TLDs                          | `[]`            | Whitelist non-English domains                   |
| `--any-lang`         | flag                                          | off             | Disable English-only filtering                  |
| `--save`             | flag                                          | off             | Write `webtool.tools.json` and `webtool.prompt.md` |
<br>

**Examples**

```bash
# Plain search
python tools_for_llm.py "latest iPhone" --freshness month

# Full research pipeline
python tools_for_llm.py "impact of ECB rate decision" --research --provider tavily

# Extractive answer, no LLM
python tools_for_llm.py "what is a SAFE note" --answer --top-k 5

# Allow Japanese sources
python tools_for_llm.py "東京 ラーメン" --any-lang
python tools_for_llm.py "東京 ラーメン" --allow .jp

# Export the LLM tool schema and prompt guide
python tools_for_llm.py "unused" --save
```

---

## Using with an LLM (function calling)

```python
from tools_for_llm import webtool

wt = webtool()

# 1. Build your system prompt
system_prompt = wt.prompt_guide()

# 2. Pass the tool schema to your chat completion call
tools = wt.tools_json()

# 3. When the model calls a tool, dispatch to the matching method
def dispatch(name: str, args: dict):
    if name == "web_search":
        return wt.search(args["query"], n=args.get("n"), freshness=args.get("freshness"))
    if name == "web_fetch":
        return wt.fetch(args["url"])
    if name == "web_research":
        return wt.research(args["question"], hops=args.get("hops", 1), freshness=args.get("freshness"))
    raise ValueError(f"unknown tool: {name}")
```
<br>

`tools_json()` returns schemas for `web_search`, `web_fetch`, and `web_research`, matching the signatures documented above — pass it straight to any OpenAI-compatible `tools` parameter.

---

## Troubleshooting

| Symptom                                   | Fix                                                                 |
|--------------------------------------------|------------------------------------------------------------------------|
| `ImportError: pip install httpx`           | `pip install httpx`                                                  |
| PDF pages return empty text                | `pip install pypdf`                                                  |
| HTML extraction looks rough / includes nav | `pip install trafilatura`                                            |
| `RuntimeError: BRAVE_API_KEY missing` (or Tavily/Serper equivalent) | Set the corresponding environment variable or pass the key directly |
| SearX requests fail                        | Confirm `SEARX_URL` points to a running instance                     |
| Non-English results missing                | Pass `allow_domains=[...]` or set `english_only=False`               |
| Stale results                              | Lower `cache_ttl` or delete the cache file at `cache_path`           |

---

## Changing the search engine

Pass a different `provider` to switch engines. API keys are read automatically from the matching environment variable, or can be passed explicitly:

```python
wt = webtool(provider="duckduckgo")                      # default, no key needed
wt = webtool(provider="brave")                            # reads BRAVE_API_KEY
wt = webtool(provider="brave", brave_key="sk-...")        # or pass the key directly
wt = webtool(provider="tavily", tavily_key="tvly-...")
wt = webtool(provider="serper", serper_key="...")
wt = webtool(provider="searx", searx_url="http://localhost:8080")
```
<br>

**Other constructor examples**

```python
wt = webtool()                                    # English-only, DuckDuckGo
wt = webtool(allow_domains=[".jp", ".cn"])        # allow specific non-English TLDs
wt = webtool(english_only=False)                  # disable the language filter entirely
wt = webtool(provider="tavily", results_per_query=20, max_pages=15)
```
