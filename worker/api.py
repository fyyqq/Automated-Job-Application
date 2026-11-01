import os
import secrets

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from alerts import parse_linkedin_alert
from dedupe import explain_no_new_jobs, sheet_row, split_new_jobs
from filters import count_reasons, explain_empty_result, filter_jobs
from llm import complete
from scoring import PASS_SCORE, explain_no_scores, load_resume, score_jobs

app = FastAPI(title="automated-job-application-engine-worker")
bearer = HTTPBearer(auto_error=False)

# Every error response carries `error_message`, `causes` and `solution` so a failed n8n run explains itself.
HTTP_HINTS = {
    401: (
        ["Authorization header is missing or malformed", "The token in the n8n credential differs from WORKER_TOKEN in .env"],
        ["Header Auth: Name 'Authorization', Value 'Bearer <token>' (one space, no quotes)", "After editing .env run: docker compose up -d"],
    ),
    404: (["Wrong URL path"], ["Check the endpoint name, e.g. /parse-alerts, /filter, /dedupe, /score, /health"]),
    405: (["Wrong HTTP method"], ["Use POST for /parse-alerts, /filter, /dedupe, /score and /llm-test; GET for /health"]),
    500: (
        ["worker/resume.txt is missing, empty, or still has [EXAMPLE] placeholders", "docker-compose.yml does not mount ./worker/resume.txt"],
        ["Fill in worker/resume.txt (copy worker/resume.example.txt)", "Then run: docker compose up -d"],
    ),
    501: (["LLM_PROVIDER is not set to 'openrouter'"], ["Set LLM_PROVIDER=openrouter in .env, then run: docker compose up -d"]),
    502: (
        ["OpenRouter rejected or failed the request", "Free-tier limit hit (20 requests/min, 50/day) or the model name is no longer valid"],
        ["Wait a minute and retry", "Check OPENROUTER_MODEL and OPENROUTER_API_KEY in .env"],
    ),
}
DEFAULT_HINT = (["Unexpected worker error"], ["Run: docker compose logs worker --tail 50"])


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


@app.post("/score", dependencies=[Depends(require_token)])
def score_endpoint(body: ScoreRequest):
    try:
        resume = load_resume()
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=500, detail=f"Cannot use worker/resume.txt: {e}")

    scored, failed = score_jobs(body.jobs, resume) if body.jobs else ([], [])
    scored.sort(key=lambda job: job["score"], reverse=True)
    passed_count = sum(1 for job in scored if job["passed"])
    print(f"/score scored={len(scored)} failed={len(failed)} passed={passed_count}", flush=True)

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
        "scored": scored,
        "rows": rows,
        "failed": failed,
    }
    if body.jobs and not scored:
        result["debug"] = explain_no_scores(failed)
    return result


@app.post("/llm-test", dependencies=[Depends(require_token)])
def llm_test():
    return {"reply": complete("Reply with OK", max_tokens=200)}
