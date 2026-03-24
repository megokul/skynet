from __future__ import annotations

import asyncio
import json
import logging
import re
from html import unescape
from typing import Any
from urllib import parse, request

from executor.action_support import require_param


logger = logging.getLogger("chathan.executor")
WEB_SEARCH_TIMEOUT_SECONDS = 15


async def web_search(params: dict[str, Any], *, brave_api_key: str) -> dict[str, Any]:
    """Search the web from the worker using Brave API with DDG fallback."""
    query = require_param(params, "query")
    raw_num = params.get("num_results", 5)
    try:
        num_results = int(raw_num)
    except (TypeError, ValueError):
        num_results = 5
    num_results = min(max(num_results, 1), 10)

    loop = asyncio.get_running_loop()
    if brave_api_key:
        try:
            output = await loop.run_in_executor(None, brave_web_search_sync, query, num_results, brave_api_key)
            return {"returncode": 0, "stdout": output, "stderr": ""}
        except Exception as exc:
            logger.warning("Brave web search failed: %s; falling back to DDG", exc)

    try:
        output = await loop.run_in_executor(None, ddg_web_search_sync, query, num_results)
        return {"returncode": 0, "stdout": output, "stderr": ""}
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": f"Web search failed: {exc}"}


def brave_web_search_sync(query: str, num_results: int, api_key: str) -> str:
    url = f"https://api.search.brave.com/res/v1/web/search?q={parse.quote_plus(query)}&count={num_results}"
    req = request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
            "User-Agent": "SKYNET-Worker/1.0",
        },
    )
    with request.urlopen(req, timeout=WEB_SEARCH_TIMEOUT_SECONDS) as resp:
        payload = resp.read().decode("utf-8", errors="replace")
    data = json.loads(payload)
    results = data.get("web", {}).get("results", []) if isinstance(data, dict) else []
    if not results:
        return "No results found."

    lines: list[str] = []
    for index, item in enumerate(results[:num_results], 1):
        title = str(item.get("title", "No title")).strip()
        link = str(item.get("url", "")).strip()
        desc = str(item.get("description", "No description")).strip()
        lines.append(f"{index}. {title}\n   URL: {link}\n   {desc}\n")
    return "\n".join(lines)


def ddg_web_search_sync(query: str, num_results: int) -> str:
    url = f"https://lite.duckduckgo.com/lite/?q={parse.quote_plus(query)}"
    req = request.Request(url, headers={"User-Agent": "SKYNET-Worker/1.0"})
    with request.urlopen(req, timeout=WEB_SEARCH_TIMEOUT_SECONDS) as resp:
        page = resp.read().decode("utf-8", errors="replace")

    results: list[str] = []
    link_pattern = re.compile(
        r"<a(?=[^>]*class=['\"]result-link['\"])(?=[^>]*href=['\"]([^'\"]+)['\"])[^>]*>(.*?)</a>",
        re.IGNORECASE | re.DOTALL,
    )
    for match in link_pattern.finditer(page):
        raw_link = (match.group(1) or "").strip()
        title_html = match.group(2) or ""
        if not raw_link:
            continue
        link = normalize_ddg_result_url(raw_link)
        if not link:
            continue
        title_text = unescape(re.sub(r"<[^>]+>", "", title_html)).strip() or "No title"
        results.append(f"- {title_text}\n  URL: {link}")
        if len(results) >= num_results:
            break

    if results:
        return "\n".join(results)

    links = re.findall(r"<a[^>]+href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>", page, re.IGNORECASE | re.DOTALL)
    for raw_link, title_html in links:
        link = normalize_ddg_result_url(raw_link)
        if not link or "duckduckgo.com" in link:
            continue
        title_text = unescape(re.sub(r"<[^>]+>", "", title_html)).strip() or "No title"
        results.append(f"- {title_text}\n  URL: {link}")
        if len(results) >= num_results:
            break

    return "\n".join(results) if results else "No results found."


def normalize_ddg_result_url(raw_link: str) -> str:
    link = (raw_link or "").strip()
    if not link:
        return ""
    if link.startswith("//"):
        link = f"https:{link}"
    elif link.startswith("/"):
        link = f"https://duckduckgo.com{link}"

    parsed = parse.urlparse(link)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        uddg = parse.parse_qs(parsed.query).get("uddg", [])
        if uddg:
            return parse.unquote(uddg[0])
    return link
