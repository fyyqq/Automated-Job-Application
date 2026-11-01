import html
import random
import re
import time
from urllib.parse import urlparse

import httpx

SEARCH_URL = "https://html.duckduckgo.com/html/"
SEARCH_PAUSE_SECONDS = (8, 15)
MAX_EMAIL_JOBS = 10

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
NOT_AN_EMAIL_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")
LEGAL_WORDS = {
    "sdn", "bhd", "berhad", "plt", "ltd", "limited", "inc", "corp", "corporation", "llc", "pte", "co", "company",
    "malaysia", "the", "and",
}
FREE_MAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "live.com", "proton.me", "protonmail.com"}
# Mailboxes a company opens for applicants, best first. Anything else on the page looks personal and is skipped.
HIRING_WORDS = ("career", "recruit", "hiring", "talent", "job", "hr", "humanresource", "people")
GENERIC_BOXES = ("hello", "info", "contact", "enquiry", "enquiries", "admin", "office", "general")
GENERIC_TLDS = {"com", "net", "org", "io", "co", "asia", "tech", "biz", "info", "app", "dev", "ai"}
MALAYSIA_HINTS = re.compile(r"malaysia|kuala lumpur|selangor|petaling jaya|cyberjaya|shah alam|\+60|\.my\b", re.IGNORECASE)
NO_REPLY = re.compile(r"no-?reply|donotreply|do-not-reply|mailer-daemon", re.IGNORECASE)


class SearchBlocked(Exception):
    pass


last_search_at = 0.0


def search_queries(company: str) -> list[str]:
    # "Malaysia" keeps worldwide names (Google, Grab) pointed at the local office.
    name = re.sub(r"\s+", " ", company or "").strip()
    return [f"{name} Malaysia career email", f"{name} Malaysia hr email"]


def search_web(query: str) -> list[dict]:
    """One DuckDuckGo request. Returns [{url, text}]; raises SearchBlocked when it refuses us."""
    global last_search_at
    # Every search is spaced out, also across companies, because a quick burst gets HTTP 202.
    wait = random.uniform(*SEARCH_PAUSE_SECONDS) - (time.monotonic() - last_search_at)
    if last_search_at and wait > 0:
        time.sleep(wait)
    last_search_at = time.monotonic()
    try:
        resp = httpx.post(SEARCH_URL, data={"q": query}, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    except httpx.HTTPError as e:
        raise SearchBlocked(f"search request failed: {type(e).__name__}")
    if resp.status_code != 200:
        raise SearchBlocked(f"search returned HTTP {resp.status_code}")
    if "anomaly" in resp.text or "g-recaptcha" in resp.text:
        raise SearchBlocked("search asked for a captcha")
    return parse_results(resp.text)


def parse_results(page: str) -> list[dict]:
    results = []
    for block in re.split(r'<div class="result results_links', page)[1:]:
        link = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if link is None:
            continue
        snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL)
        text = f"{link.group(2)} {snippet.group(1) if snippet else ''}"
        results.append({"url": html.unescape(link.group(1)), "text": html.unescape(re.sub(r"<[^>]+>", " ", text))})
    return results


def extract_emails(text: str) -> list[str]:
    seen = []
    for match in EMAIL.findall(text or ""):
        email = match.strip(".").lower()
        if email.endswith(NOT_AN_EMAIL_SUFFIX) or NO_REPLY.search(email) or email in seen:
            continue
        seen.append(email)
    return seen


def company_words(company: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (company or "").lower())
    return [w for w in words if w not in LEGAL_WORDS]


def domain_matches_company(email: str, company: str) -> bool:
    words = company_words(company)
    if not words:
        return False
    # A whole label must match: grab.com yes, grabhub.my no (an unrelated site that merely contains the name).
    labels = {label.replace("-", "") for label in email.split("@", 1)[1].split(".")}
    return "".join(words) in labels or (len(words[0]) >= 4 and words[0] in labels)


def mailbox_rank(email: str, company: str = "") -> int:
    """Lower is better; 99 means the mailbox looks personal and is skipped."""
    local = re.sub(r"[^a-z]", "", email.split("@", 1)[0])
    # "wikicareer@" must not count as a careers box just because the company name contains "career".
    for word in sorted(company_words(company), key=len, reverse=True):
        local = local.replace(word, "")
    if not local:
        return len(HIRING_WORDS) + 1
    for rank, word in enumerate(HIRING_WORDS):
        if local.startswith(word) or (len(word) > 3 and word in local):
            return rank
    if local in GENERIC_BOXES:
        return len(HIRING_WORDS)
    return 99


def pick_from_posting(description: str) -> str | None:
    # An address printed in the ad is meant for applicants, so any real mailbox counts.
    for email in extract_emails(description):
        if not email.endswith("linkedin.com"):
            return email
    return None


def domain_tier(email: str, result: dict, company: str) -> int | None:
    """0 = a Malaysian (.my) address, 1 = a .com on the company's own site that mentions Malaysia, None = reject.

    Other country domains (.in, .sg, .co.uk) are most likely another office or another company.
    """
    domain = email.split("@", 1)[1]
    if domain.endswith(".my"):
        return 0
    if domain.rsplit(".", 1)[-1] in GENERIC_TLDS:
        on_own_site = domain_matches_company(f"x@{urlparse(result['url']).netloc}", company)
        if on_own_site and MALAYSIA_HINTS.search(f"{result['text']} {result['url']}"):
            return 1
    return None


def pick_from_results(results: list[dict], company: str) -> tuple[str, str] | None:
    best = None
    for result in results:
        for email in extract_emails(result["text"]):
            domain = email.split("@", 1)[1]
            if domain in FREE_MAIL or not domain_matches_company(email, company):
                continue
            tier = domain_tier(email, result, company)
            rank = mailbox_rank(email, company)
            if tier is None or rank >= 99:
                continue
            if best is None or (tier, rank) < best[0]:
                best = ((tier, rank), email, result["url"])
    return (best[1], best[2]) if best else None


def email_from_posting(job: dict) -> dict | None:
    email = pick_from_posting(job.get("description") or "")
    if not email:
        return None
    return {"career_email": email, "email_source": "job ad", "email_found_at": job.get("url", "")}


def email_from_web(job: dict, search=search_web) -> tuple[dict | None, str | None]:
    """Return (found, blocked_reason). found is None when no real address turned up."""
    company = job.get("company", "")
    for query in search_queries(company):
        try:
            results = search(query)
        except SearchBlocked as e:
            return None, str(e)
        picked = pick_from_results(results, company)
        if picked:
            email, url = picked
            return {"career_email": email, "email_source": "web search", "email_found_at": url}, None
    return None, None


def explain_blocked(reason: str, searched: int) -> dict:
    return {
        "error_message": f"The web search stopped after {searched} company search(es): {reason}",
        "causes": [
            "DuckDuckGo refused or limited the request (it is a free page, not an official API)",
            "Too many searches in a short time from this IP",
        ],
        "solution": [
            "Nothing to do now: the remaining jobs simply have no career email and stay in the sheet",
            "Do not retry in a loop; the next daily run tries again",
            "If it blocks every run, tell me and we switch the search source",
        ],
    }


def explain_none_found(count: int) -> dict:
    return {
        "error_message": f"No career email found for {count} job(s)",
        "causes": [
            "The company publishes no HR or careers address (common for large companies)",
            "The only addresses found were free mail (gmail), personal-looking, or on a domain that does not match the company name",
            "The company name in the job is different from the name on its website",
        ],
        "solution": [
            "Nothing to fix: these jobs are applied through the platform link instead",
            "Optional: add the company's real address to the sheet by hand",
        ],
    }
