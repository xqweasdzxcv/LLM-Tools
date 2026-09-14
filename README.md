# Tools for LLM

Lightweight web search, fetch, research, and extractive-answer toolkit for LLMs — no model code, no Ollama, just retrieval and ranking.

Tools for LLM gives an LLM (or any Python app) three primitives: **search** the web, **fetch** a clean copy of a page, and **research** a question end‑to‑end (search → fetch → chunk → rank → cite). An **answer** helper can even extract a plain-text answer without calling any model at all.

## Features

- 🔎 **Multi-provider search** — DuckDuckGo (default, no key), SearX, Brave, Tavily, Serper
- 🧹 **Clean text extraction** — HTML via `trafilatura`, PDFs via `pypdf`, with graceful fallbacks
- 🔗 **Multi-hop research pipeline** — search, fetch, chunk, and rank into a citable `[n]` context block
- 🧠 **Extractive Q&A** — pull a plain-text answer out of the research context, no LLM required
- 🇬🇧 **English-only by default** — filters non-English TLDs and low-ASCII text; fully overridable
- 💾 **Built-in caching** — SQLite-backed cache for search and fetch results
- 🤖 **LLM-ready** — ships a function-calling schema (`tools_json()`) and a ready-to-paste prompt guide (`prompt_guide()`)
- ⚡ **Sync and async** — every method has an `a`-prefixed async twin

## Install

```bash
pip install httpx trafilatura pypdf
```

- `httpx` is required.
- `trafilatura` is optional but strongly recommended for clean text extraction.
- `pypdf` is optional, only needed to extract text from PDF pages.

## Quick Start

```python
from tools_for_llm import webtool

wt = webtool()  # defaults to DuckDuckGo, English-only

# 1. Search
results = wt.search("python 3.13 release notes")

# 2. Fetch a page's clean text
text = wt.fetch("https://docs.python.org/3/whatsnew/3.13.html")

# 3. Full research pipeline (search + fetch + rank + citations)
bundle = wt.research("what changed in python 3.13")
print(bundle["context"])   # citable [1] ... [2] ... text
print(bundle["sources"])   # [{n, title, url, published, snippet}, ...]

# 4. Extractive answer, no model involved
print(wt.answer("what changed in python 3.13"))
```

Allow non-English sources explicitly:

```python
wt = webtool(allow_domains=[".jp", ".cn", ".kr", ".ru"])
```

## Providers

| Provider     | Key required     | Notes                          |
|--------------|-------------------|---------------------------------|
| `duckduckgo` | none              | Default provider                |
| `searx`      | none (self-hosted)| Needs `SEARX_URL`               |
| `brave`      | `BRAVE_API_KEY`   |                                  |
| `tavily`     | `TAVILY_API_KEY`  |                                  |
| `serper`     | `SERPER_API_KEY`  | Google results via Serper       |

## CLI

```bash
python tools_for_llm.py "python 3.13 release notes" --research --freshness month
```

## Documentation

Full parameter reference, all methods, config options, CLI flags, and LLM function-calling examples: **[USAGE.md](./USAGE.md)**

## License

Apache 2.0 license
