import os
import secrets

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from alerts import parse_linkedin_alert
from cover import MAX_JOBS_PER_CALL, explain_cover_failures, write_cover_note
from dedupe import explain_no_new_jobs, normalize, sheet_row, split_new_jobs
from emailfinder import MAX_EMAIL_JOBS, explain_blocked, explain_none_found, email_from_posting, email_from_web
from filters import count_reasons, explain_empty_result, filter_jobs
from llm import complete
from scoring import PASS_SCORE, deep_check, explain_deep_blocked, explain_no_scores, load_resume, score_jobs

app = FastAPI(title="automated-job-application-engine-worker")
bearer = HTTPBearer(auto_error=False)

# Every error response carries `error_message`, `causes` and `solution` so a failed n8n run explains itself.
HTTP_HINTS = {
    401: (
        ["Authorization header is missing or malformed", "The token in the n8n credential differs from WORKER_TOKEN in .env"],
        ["Header Auth: Name 'Authorization', Value 'Bearer <token>' (one space, no quotes)", "After editing .env run: docker compose up -d"],
    ),
    404: (["Wrong URL path"], ["Check the endpoint name, e.g. /parse-alerts, /filter, /dedupe, /score, /health"]),
    405: (["Wrong HTTP method"], ["Use POST for /parse-alerts, /filter, /dedupe, /score, /cover-note, /find-email and /llm-test; GET for /health"]),
    500: (
        ["worker/resume.txt is missing, empty, or still has [EXAMPLE] placeholders", "docker-compose.yml does not mount ./worker/resume.txt"],
        ["Fill in worker/resume.txt (copy worker/resume.example.txt)", "Then run: docker compose up -d"],
    ),
    429: (
        [
            "The free OpenRouter allowance is used up: 50 requests/day (20/min) for the whole account, across all free models",
            "A new API key or another free model does not help: the limit belongs to the account",
        ],
        [
            "Wait until the reset time in error_message, then run again (the daily run is not affected tomorrow)",
            "Or add 10 credits on openrouter.ai to raise free models to 1000 requests/day",
            "Or switch LLM_PROVIDER to Claude Haiku (production plan) when it is wired",
        ],
    ),
    501: (["LLM_PROVIDER is not set to 'openrouter'"], ["Set LLM_PROVIDER=openrouter in .env, then run: docker compose up -d"]),
    502: (
        ["OpenRouter rejected or failed the request", "Free-tier limit hit (20 requests/min, 50/day) or the model name is no longer valid"],
        ["Wait a minute and retry", "Check OPENROUTER_MODEL and OPENROUTER_API_KEY in .env"],
    ),
}
DEFAULT_HINT = (["Unexpected worker error"], ["Run: docker compose logs worker --tail 50"])

NO_SHORTLIST = {
    "error_message": "No jobs were sent, so nothing was done",
    "causes": [
        "No job scored 75 or more in today's Score step (this is normal on a quiet day)",
        "The n8n body filter j.passed removed every job",
    ],
    "solution": [
        "Nothing to fix on a quiet day",
        "To test the node anyway, temporarily use j.score >= 60 instead of j.passed in the JSON Body",
    ],
}


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc: StarletteHTTPException):
    causes, solution = HTTP_HINTS.get(exc.status_code, DEFAULT_HINT)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error_message": exc.detail, "causes": causes, "solution": solution},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error_message": "Request body does not match what this endpoint expects",
            "fields": [{"where": ".".join(map(str, e["loc"])), "problem": e["msg"]} for e in exc.errors()],
            "causes": ["The JSON body is missing a required field or has the wrong type", "n8n sent an empty or unevaluated expression"],
            "solution": [
                "Open the HTTP Request node's INPUT panel and check the fields match 'fields' above",
                "Build the body with JSON.stringify({...}) in Expression mode",
            ],
        },
    )


def require_token(creds: HTTPAuthorizationCredentials | None = Depends(bearer)):
    expected = os.environ.get("WORKER_TOKEN", "")
    # Fail closed: no token configured means nobody gets in.
    if not expected or creds is None or not secrets.compare_digest(creds.credentials, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health", dependencies=[Depends(require_token)])
def health():
    return {
        "status": "ok",
        "llm_provider": os.environ.get("LLM_PROVIDER", ""),
        "auto_submit": os.environ.get("AUTO_SUBMIT", "false").lower() == "true",
        "daily_cap": int(os.environ.get("DAILY_CAP") or 0),
    }


class AlertEmail(BaseModel):
    text: str
    email_id: str = ""


@app.post("/parse-alerts", dependencies=[Depends(require_token)])
def parse_alerts(email: AlertEmail):
    jobs = parse_linkedin_alert(email.text)
    return {
        "count": len(jobs),
        "jobs": [{**job, "source": "LinkedIn", "email_id": email.email_id} for job in jobs],
    }


class FilterRequest(BaseModel):
    jobs: list[dict]


@app.post("/filter", dependencies=[Depends(require_token)])
def filter_endpoint(body: FilterRequest):
    kept, dropped = filter_jobs(body.jobs)
    reason_counts = count_reasons(dropped)
    print(f"/filter kept={len(kept)} dropped={len(dropped)} reasons={reason_counts}", flush=True)

    result = {
        "kept_count": len(kept),
        "dropped_count": len(dropped),
        "reason_counts": reason_counts,
        "kept": kept,
        "dropped": dropped,
    }
    if not kept and dropped:
        result["debug"] = explain_empty_result(dropped)
    return result


class DedupeRequest(BaseModel):
    jobs: list[dict]
    existing: list[dict] = []


@app.post("/dedupe", dependencies=[Depends(require_token)])
def dedupe_endpoint(body: DedupeRequest):
    new, duplicates = split_new_jobs(body.jobs, body.existing)
    print(f"/dedupe new={len(new)} duplicates={len(duplicates)}", flush=True)

    result = {
        "new_count": len(new),
        "duplicate_count": len(duplicates),
        "new": new,
        "rows": [sheet_row(job) for job in new],
        "duplicates": duplicates,
    }
    if not new and duplicates:
        result["debug"] = explain_no_new_jobs(duplicates, body.existing)
    return result


class ScoreRequest(BaseModel):
    jobs: list[dict]
    deep: bool = False


@app.post("/score", dependencies=[Depends(require_token)])
def score_endpoint(body: ScoreRequest):
    try:
        resume = load_resume()
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=500, detail=f"Cannot use worker/resume.txt: {e}")

    scored, failed = score_jobs(body.jobs, resume) if body.jobs else ([], [])

    deep = {"enabled": body.deep, "pages_opened": 0, "stopped_reason": None}
    if body.deep and scored:
        scored, blocked, opened = deep_check(scored, resume)
        deep.update(pages_opened=opened, stopped_reason=blocked)

    scored.sort(key=lambda job: job["score"], reverse=True)
    passed_count = sum(1 for job in scored if job["passed"])
    print(
        f"/score scored={len(scored)} failed={len(failed)} passed={passed_count} "
        f"deep_pages={deep['pages_opened']} stopped={deep['stopped_reason']}",
        flush=True,
    )

    # Jobs the model could not score are left out of `rows` so tomorrow's run tries them again.
    rows = [
        sheet_row(job, score=job["score"], status="Shortlisted" if job["passed"] else "Low score")
        for job in scored
    ]

    result = {
        "scored_count": len(scored),
        "failed_count": len(failed),
        "passed_count": passed_count,
        "threshold": PASS_SCORE,
        "deep": deep,
        "scored": scored,
        "rows": rows,
        "failed": failed,
    }
    if body.jobs and not scored:
        result["debug"] = explain_no_scores(failed)
    if deep["stopped_reason"]:
        result["warning"] = explain_deep_blocked(deep["stopped_reason"], deep["pages_opened"])
    return result


class CoverNoteRequest(BaseModel):
    jobs: list[dict]


@app.post("/cover-note", dependencies=[Depends(require_token)])
def cover_note_endpoint(body: CoverNoteRequest):
    try:
        resume = load_resume()
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=500, detail=f"Cannot use worker/resume.txt: {e}")

    notes, failed = [], []
    for job in body.jobs[:MAX_JOBS_PER_CALL]:
        info, problem = write_cover_note(job, resume)
        if info:
            notes.append({**job, **info, "email_subject": f"Application for {job.get('title', 'the role')}"})
        else:
            failed.append({**job, "reason": problem})
    skipped = body.jobs[MAX_JOBS_PER_CALL:]
    print(f"/cover-note written={len(notes)} failed={len(failed)} skipped={len(skipped)}", flush=True)

    result = {"written_count": len(notes), "failed_count": len(failed), "notes": notes, "failed": failed, "skipped": skipped}
    if not body.jobs:
        result["debug"] = NO_SHORTLIST
    elif not notes:
        result["debug"] = explain_cover_failures(failed)
    return result


class FindEmailRequest(BaseModel):
    jobs: list[dict]


@app.post("/find-email", dependencies=[Depends(require_token)])
def find_email_endpoint(body: FindEmailRequest):
    found, not_found, everyone, blocked, searched = [], [], [], None, 0
    web_results: dict[str, dict | None] = {}

    for job in body.jobs[:MAX_EMAIL_JOBS]:
        info = email_from_posting(job)
        if info is None:
            key = normalize(job.get("company"))
            # The same company in two jobs is searched once, and nothing is searched after a block.
            if key not in web_results and not blocked:
                web_results[key], blocked = email_from_web(job)
                searched += 1
            info = web_results.get(key)
        if info:
            found.append({**job, **info})
        else:
            not_found.append({**job, "reason": f"search stopped: {blocked}" if blocked else "no matching career email found"})
        # `jobs` keeps every job (email fields empty when none) so n8n can carry on to Telegram either way.
        everyone.append({**job, "career_email": "", "email_source": "", "email_found_at": "", **(info or {})})

    skipped = body.jobs[MAX_EMAIL_JOBS:]
    print(f"/find-email found={len(found)} not_found={len(not_found)} searched={searched} stopped={blocked}", flush=True)

    result = {
        "found_count": len(found),
        "not_found_count": len(not_found),
        "jobs": everyone,
        "rows": [
            {"Dedupe Key": job.get("dedupe_key", ""), "Career Email": job["career_email"], "Email Source": job["email_source"]}
            for job in found
        ],
        "found": found,
        "not_found": not_found,
        "skipped": skipped,
        "stopped_reason": blocked,
    }
    if blocked:
        result["warning"] = explain_blocked(blocked, searched)
    elif not body.jobs:
        result["debug"] = NO_SHORTLIST
    elif not found:
        result["debug"] = explain_none_found(len(not_found))
    return result


@app.post("/llm-test", dependencies=[Depends(require_token)])
def llm_test():
    return {"reply": complete("Reply with OK", max_tokens=200)}
