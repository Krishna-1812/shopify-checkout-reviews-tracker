"""Fetch and parse public reviews from the Shopify App Store.

The review pages are plain server-rendered HTML:

    https://apps.shopify.com/<slug>/reviews?sort_by=newest&page=<n>

Each review is a ``<div data-merchant-review data-review-content-id="...">``
block. Ten reviews per page, newest first; a page past the end returns no
review blocks. The page header also states how many reviews the app has
("Reviews (398)"), which the health check uses to tell "nothing new today"
apart from "the parser silently broke".
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

import requests
from bs4 import BeautifulSoup, Tag

from . import config

log = logging.getLogger(__name__)

DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2},\s+\d{4}"
)
STARS_RE = re.compile(r"([\d.]+)\s+out of 5 stars")
TOTAL_RE = re.compile(r"\((\d[\d,]*)\)")
STAR_LEVEL_RE = re.compile(r"ratings are (\d) stars?")


class FetchError(RuntimeError):
    """A page could not be fetched even after retries."""


# --------------------------------------------------------------------------
# Parsing (pure functions: HTML in, data out; unit-tested with saved pages)
# --------------------------------------------------------------------------
@dataclass
class PageResult:
    reviews: list[dict]
    declared_total: int | None          # "Reviews (N)" on the page; None = unreadable
    overall_rating: float | None
    rating_counts: dict[str, int] | None  # {"5": 363, "4": 10, ...}
    malformed: int = 0                   # review blocks we could not fully parse


def _text(node: Tag | None) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() if node else ""


def _multiline_text(node: Tag | None) -> str:
    """Keep paragraph breaks in review bodies, collapse other whitespace."""
    if node is None:
        return ""
    paras = node.find_all("p")
    parts = [_text(p) for p in paras] if paras else [_text(node)]
    return "\n".join(p for p in parts if p).strip()


def content_hash(text: str, rating: int) -> str:
    """Fingerprint of what the merchant wrote, used to notice edited reviews."""
    return hashlib.sha1(f"{rating}|{text}".encode("utf-8")).hexdigest()[:16]


def _parse_header(soup: BeautifulSoup) -> tuple[int | None, float | None, dict | None]:
    declared: int | None = None
    for h2 in soup.find_all("h2"):
        label = _text(h2)
        if label.startswith("Reviews"):
            m = TOTAL_RE.search(label)
            if m:
                declared = int(m.group(1).replace(",", ""))
            break

    overall: float | None = None
    marker = soup.find(string=re.compile(r"^\s*Overall rating\s*$"))
    if marker:
        node = marker.find_parent().find_next(attrs={"aria-label": STARS_RE})
        if node:
            overall = float(STARS_RE.search(node["aria-label"]).group(1))

    counts: dict[str, int] = {}
    for span in soup.find_all("span", string=STAR_LEVEL_RE):
        li = span.find_parent("li")
        level = STAR_LEVEL_RE.search(span.get_text()).group(1)
        num = None
        link = li.find(attrs={"aria-label": re.compile(r"total reviews")}) if li else None
        if link:
            num = re.search(r"\d[\d,]*", link["aria-label"]).group(0)
        elif li:
            # the count is the last bare number in the list item
            nums = re.findall(r"\b\d[\d,]*\b", _text(li))
            num = nums[-1] if nums else None
        if num is not None:
            counts[level] = int(num.replace(",", ""))

    if declared is None:
        if counts and len(counts) == 5:
            declared = sum(counts.values())
        elif soup.find(string=re.compile(r"No reviews yet")):
            declared = 0
    return declared, overall, (counts or None)


def _parse_review(block: Tag, app_slug: str) -> dict | None:
    review_id = block.get("data-review-content-id", "").strip()
    stars = block.find(attrs={"aria-label": STARS_RE})
    date_node = block.find(string=DATE_RE)
    body = block.find(attrs={"data-truncate-content-copy": True})
    if not (review_id and stars and date_node and body is not None):
        return None

    rating = int(float(STARS_RE.search(stars["aria-label"]).group(1)))
    date = datetime.strptime(DATE_RE.search(date_node).group(0), "%B %d, %Y").date()
    text = _multiline_text(body)

    # Right-hand column: store name, then country, then "N days using the app".
    store_node = block.find("span", attrs={"title": True})
    store = (store_node.get("title") or _text(store_node)).strip() if store_node else ""
    country, usage = "", ""
    if store_node:
        side = store_node.find_parent("div").find_parent("div")
        extra = [
            _text(d) for d in side.find_all("div", recursive=False)[1:]
            if _text(d)
        ]
        for value in extra:
            if "using the app" in value:
                usage = value
            elif not country:
                country = value

    reply_node = block.find(attrs={"data-merchant-review-reply": True})
    reply_text = _text(reply_node)

    return {
        "id": review_id,
        "app": app_slug,
        "url": f"{config.BASE_URL}/reviews/{review_id}",
        "rating": rating,
        "date": date.isoformat(),
        "store": store,
        "country": country,
        "usage": usage,
        "text": text,
        "has_reply": bool(reply_text),
        "content_hash": content_hash(text, rating),
    }


def parse_reviews_page(html: str, app_slug: str) -> PageResult:
    soup = BeautifulSoup(html, "html.parser")
    declared, overall, counts = _parse_header(soup)
    reviews, malformed = [], 0
    for block in soup.find_all(attrs={"data-review-content-id": True}):
        parsed = _parse_review(block, app_slug)
        if parsed is None:
            malformed += 1
        else:
            reviews.append(parsed)
    return PageResult(reviews, declared, overall, counts, malformed)


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def fetch(session: requests.Session, url: str) -> str:
    """GET with retries and exponential backoff on 429 / 5xx / network errors."""
    last_error = ""
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS)
            if resp.status_code == 200:
                return resp.text
            last_error = f"HTTP {resp.status_code}"
            if resp.status_code not in (429, 500, 502, 503, 504):
                break  # 403/404 etc. will not fix themselves
            wait = float(resp.headers.get("Retry-After") or 2 ** attempt * 2)
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            wait = 2 ** attempt * 2
        log.warning("fetch %s failed (%s), attempt %d", url, last_error, attempt)
        if attempt < config.MAX_RETRIES:
            time.sleep(min(wait, 60))
    raise FetchError(f"{url} -> {last_error}")


def page_url(slug: str, page: int) -> str:
    return f"{config.BASE_URL}/{slug}/reviews?sort_by=newest&page={page}"


@dataclass
class AppCrawl:
    slug: str
    mode: str                               # "incremental" or "full"
    reviews: list[dict] = field(default_factory=list)
    pages_fetched: int = 0
    first_page_count: int | None = None     # reviews parsed on page 1
    declared_total: int | None = None
    overall_rating: float | None = None
    rating_counts: dict | None = None
    malformed: int = 0
    error: str | None = None


def crawl_app(
    session: requests.Session,
    slug: str,
    known_ids: set[str],
    full: bool,
    simulate_empty: bool = False,
    delay: float = config.REQUEST_DELAY_SECONDS,
) -> AppCrawl:
    """Walk an app's review pages newest-first.

    Incremental mode stops at the first page whose reviews are all already
    stored. Full mode walks to the last page (used for the first run and
    whenever stored coverage falls behind Shopify's own count).
    """
    crawl = AppCrawl(slug=slug, mode="full" if full else "incremental")
    try:
        for page in range(1, config.MAX_PAGES_PER_APP + 1):
            if page > 1:
                time.sleep(delay)
            html = fetch(session, page_url(slug, page))
            result = parse_reviews_page(html, slug)
            if simulate_empty:
                # Test hook: behave as if Shopify changed its markup and the
                # parser now finds nothing. Used to prove the alert fires.
                result.reviews, result.malformed = [], 0
            crawl.pages_fetched += 1
            crawl.malformed += result.malformed
            if page == 1:
                crawl.first_page_count = len(result.reviews)
                crawl.declared_total = result.declared_total
                crawl.overall_rating = result.overall_rating
                crawl.rating_counts = result.rating_counts
            if not result.reviews:
                break
            crawl.reviews.extend(result.reviews)
            if not full and all(r["id"] in known_ids for r in result.reviews):
                break
        else:
            crawl.error = f"hit MAX_PAGES_PER_APP={config.MAX_PAGES_PER_APP}"
    except FetchError as exc:
        crawl.error = str(exc)
    except Exception as exc:  # parser bug etc. -> recorded, alerted on
        log.exception("crawl of %s crashed", slug)
        crawl.error = f"{type(exc).__name__}: {exc}"
    return crawl
