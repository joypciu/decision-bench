from __future__ import annotations

import ipaddress
import re
import socket
from html import unescape
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from decision_bench.domain import ProviderConfig

Get = Callable[..., Any]
TAG = re.compile(r"<[^>]+>")
SPACE = re.compile(r"\s+")


def web_search(query: str, configs: list[ProviderConfig], get: Get | None = None) -> dict:
    cleaned = " ".join(query.split())[:200]
    if not cleaned:
        return {"error": "A search query is required.", "results": []}
    fetch = get or _http_get
    results: list[dict[str, str]] = []
    results.extend(_configured_search(cleaned, configs, fetch))
    try:
        results.extend(_wikipedia(cleaned, fetch))
    except Exception:
        pass
    try:
        results.extend(_duckduckgo(cleaned, fetch))
    except Exception:
        pass
    if get is None:
        try:
            results.extend(_duckduckgo_html(cleaned))
        except Exception:
            pass
    unique = []
    seen = set()
    for item in results:
        key = (item.get("url") or item.get("title") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) == 6:
            break
    if not unique:
        return {
            "query": cleaned,
            "results": [],
            "note": "No results. Do not search again. Finish from the case.",
        }
    return {"query": cleaned, "results": unique}


def fetch_url(url: str, get: Get | None = None) -> dict:
    if not is_public_http_url(url):
        return {"error": "Only public http and https URLs can be fetched."}
    fetch = get or _http_get_text
    try:
        body = fetch(url)
    except Exception as exc:
        return {"error": f"Fetch failed: {exc}"[:240], "url": url}
    if isinstance(body, dict):
        text = str(body)[:4000]
    else:
        text = plain_text(str(body))[:4000]
    return {"url": url, "text": text}


def is_public_http_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "0.0.0.0"} or host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return False
    if not addresses:
        return False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def plain_text(value: str) -> str:
    text = TAG.sub(" ", unescape(value))
    return SPACE.sub(" ", text).strip()


def _configured_search(query: str, configs: list[ProviderConfig], get: Get) -> list[dict[str, str]]:
    found = []
    for config in configs:
        if config.kind != "search" or not config.enabled or not config.api_key.strip():
            continue
        try:
            if config.name == "tavily":
                found.extend(_tavily(query, config, get))
            elif config.name == "brave":
                found.extend(_brave(query, config, get))
            elif config.name == "exa":
                found.extend(_exa(query, config, get))
        except Exception:
            continue
    return found


def _wikipedia(query: str, get: Get) -> list[dict[str, str]]:
    payload = get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": "3",
            "format": "json",
        },
    )
    rows = ((payload or {}).get("query") or {}).get("search") or []
    results = []
    for row in rows:
        title = str(row.get("title") or "")
        if not title:
            continue
        slug = title.replace(" ", "_")
        results.append(
            {
                "title": title,
                "url": f"https://en.wikipedia.org/wiki/{slug}",
                "snippet": plain_text(str(row.get("snippet") or "")),
                "source": "wikipedia",
            }
        )
    return results


def _duckduckgo_html(query: str) -> list[dict[str, str]]:
    headers = {"User-Agent": "DecisionBench/0.1 (https://github.com/joypciu/decision-bench)"}
    with httpx.Client(timeout=12, follow_redirects=True) as client:
        response = client.post("https://html.duckduckgo.com/html/", data={"q": query}, headers=headers)
        response.raise_for_status()
    return parse_duckduckgo_html(response.text)


def parse_duckduckgo_html(html: str) -> list[dict[str, str]]:
    links = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.I | re.S)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</(?:a|td|span)>', html, flags=re.I | re.S)
    results = []
    for index, (href, title) in enumerate(links[:5]):
        url = _unwrap_duckduckgo_url(href)
        if not url.startswith("http"):
            continue
        snippet = plain_text(snippets[index]) if index < len(snippets) else ""
        results.append({"title": plain_text(title)[:160], "url": url, "snippet": snippet[:500], "source": "duckduckgo"})
    return results


def _unwrap_duckduckgo_url(href: str) -> str:
    from urllib.parse import parse_qs, unquote, urlparse

    if "uddg=" not in href:
        return unescape(href)
    query = parse_qs(urlparse(href).query)
    return unquote(query.get("uddg", [href])[0])


def _duckduckgo(query: str, get: Get) -> list[dict[str, str]]:
    payload = get(
        "https://api.duckduckgo.com/",
        params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
    )
    if not isinstance(payload, dict):
        return []
    results = []
    abstract = str(payload.get("AbstractText") or "").strip()
    abstract_url = str(payload.get("AbstractURL") or "").strip()
    if abstract and abstract_url:
        results.append(
            {
                "title": str(payload.get("Heading") or query),
                "url": abstract_url,
                "snippet": abstract[:500],
                "source": "duckduckgo",
            }
        )
    for topic in payload.get("RelatedTopics") or []:
        if not isinstance(topic, dict) or not topic.get("FirstURL"):
            continue
        results.append(
            {
                "title": plain_text(str(topic.get("Text") or ""))[:120],
                "url": str(topic["FirstURL"]),
                "snippet": plain_text(str(topic.get("Text") or ""))[:500],
                "source": "duckduckgo",
            }
        )
        if len(results) == 4:
            break
    return results


def _tavily(query: str, config: ProviderConfig, get: Get) -> list[dict[str, str]]:
    payload = get(
        f"{config.base_url.rstrip('/')}/search",
        json={"api_key": config.api_key, "query": query, "max_results": 4},
    )
    return [
        {
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "snippet": str(item.get("content") or "")[:500],
            "source": "tavily",
        }
        for item in (payload or {}).get("results") or []
        if item.get("url")
    ]


def _brave(query: str, config: ProviderConfig, get: Get) -> list[dict[str, str]]:
    payload = get(
        f"{config.base_url.rstrip('/')}/web/search",
        params={"q": query, "count": "4"},
        headers={"X-Subscription-Token": config.api_key, "Accept": "application/json"},
    )
    web = ((payload or {}).get("web") or {}).get("results") or []
    return [
        {
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "snippet": str(item.get("description") or "")[:500],
            "source": "brave",
        }
        for item in web
        if item.get("url")
    ]


def _exa(query: str, config: ProviderConfig, get: Get) -> list[dict[str, str]]:
    payload = get(
        f"{config.base_url.rstrip('/')}/search",
        json={"query": query, "numResults": 4},
        headers={"x-api-key": config.api_key},
    )
    return [
        {
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "snippet": str(item.get("text") or "")[:500],
            "source": "exa",
        }
        for item in (payload or {}).get("results") or []
        if item.get("url")
    ]


def _http_get(url: str, params=None, json=None, headers=None):
    merged = {"User-Agent": "DecisionBench/0.1 (https://github.com/joypciu/decision-bench)"}
    if headers:
        merged.update(headers)
    with httpx.Client(timeout=12, follow_redirects=True) as client:
        if json is not None:
            response = client.post(url, json=json, headers=merged)
        else:
            response = client.get(url, params=params, headers=merged)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "json" in content_type or (response.text[:1] in "{["):
            return response.json()
        return response.text[:20000]


def _http_get_text(url: str):
    with httpx.Client(timeout=12, follow_redirects=False) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text[:50000]
