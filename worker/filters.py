import re
from collections import Counter

RELEVANT_TITLE = re.compile(
    r"\b(developer|programmer|software|web|website|wordpress|php|laravel|react|"
    r"full[- ]?stack|front[- ]?end|back[- ]?end|qa|quality assurance|sdet|cms|api)\b",
    re.IGNORECASE,
)

# Management-level titles and employment types we never want. Senior is allowed on purpose.
REJECTED_TITLE = re.compile(
    r"\b(lead|principal|head|manager|director|architect|staff|vp|chief|"
    r"intern|internship|contract|freelance|part[- ]?time|temporary)\b",
    re.IGNORECASE,
)

KL_SELANGOR_PLACES = [
    "kuala lumpur", "kl", "selangor", "petaling jaya", "pj", "shah alam", "subang", "usj",
    "puchong", "cyberjaya", "klang", "kajang", "bangi", "seri kembangan", "serdang", "sepang",
    "rawang", "ampang", "cheras", "bukit jalil", "sri petaling", "damansara", "mont kiara",
    "bangsar", "sunway", "setia alam", "petaling", "gombak", "batu caves", "sungai buloh",
    "kelana jaya", "semenyih", "banting", "kuala selangor", "hulu langat", "balakong",
    "kepong", "sentul", "setapak", "wangsa maju", "bukit bintang", "klcc", "mid valley",
    "segambut", "salak selatan", "bandar utama", "ara damansara", "taman tun dr ismail", "ttdi",
    "desa parkcity", "bukit damansara", "sri hartamas", "taman desa", "old klang road",
    "seri kembangan", "bandar sunway", "bandar baru bangi", "telok panglima garang",
]
ALLOWED_LOCATION = re.compile(
    r"\b(" + "|".join(re.escape(place) for place in KL_SELANGOR_PLACES) + r"|remote)\b",
    re.IGNORECASE,
)


def reject_reason(job: dict) -> str | None:
    title = job.get("title", "")
    location = job.get("location", "")

    bad = REJECTED_TITLE.search(title)
    if bad:
        return f"title: {bad.group(1).lower()}"

    if not RELEVANT_TITLE.search(title):
        return "title: not a dev role"

    # A bare "Malaysia" gives no city, so let the scorer judge instead of dropping it.
    if location.strip().lower() in ("", "malaysia"):
        return None

    if not ALLOWED_LOCATION.search(location):
        return f"location: {location}"

    return None


def count_reasons(dropped: list[dict]) -> dict:
    return dict(Counter(job["reason"] for job in dropped))


def explain_empty_result(dropped: list[dict]) -> dict:
    causes, solution = [], []

    nested = sum(1 for job in dropped if isinstance(job.get("jobs"), dict))
    untitled = sum(1 for job in dropped if not job.get("title")) - nested
    if nested:
        causes.append(f"{nested} job(s) are wrapped inside a 'jobs' field (the Split Out node does this), so 'title' is not at the top level")
        solution.append("Unwrap in the HTTP Request body: {{ JSON.stringify({ jobs: $input.all().map(i => i.json.jobs) }) }} and turn on Settings > Execute Once")
    if untitled > 0:
        causes.append(f"{untitled} job(s) have no 'title' field, so the request body shape is probably wrong")
        solution.append("Each entry sent to /filter must have title, company and location at the top level")

    reasons = [job["reason"] for job in dropped if job.get("title")]
    if any(reason.startswith("location") for reason in reasons):
        causes.append("Some locations are outside KL/Selangor")
        solution.append("Read 'dropped' reasons; add a missing place to KL_SELANGOR_PLACES in worker/filters.py")
    if any(reason.startswith("title") for reason in reasons):
        causes.append("Some titles hit the senior/contract/not-a-dev-role rules")
        solution.append("Read 'dropped' reasons; loosen REJECTED_TITLE or RELEVANT_TITLE in worker/filters.py")

    return {
        "error_message": f"All {len(dropped)} jobs were dropped, none were kept",
        "causes": causes,
        "solution": solution,
    }


def filter_jobs(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    kept, dropped = [], []
    for job in jobs:
        reason = reject_reason(job)
        if reason is None:
            kept.append(job)
        else:
            dropped.append({**job, "reason": reason})
    return kept, dropped
