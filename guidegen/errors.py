"""Error taxonomy: one code per failure point, with a user-facing message and an operator hint.

Rules:
* ``user_message`` is safe to render: no model names, keys, stack traces, paths, SQL or HTTP jargon.
* ``operator_hint`` goes to logs and the debug view only.
* Every error carries a stage, an HTTP status and whether a retry can plausibly help.
* Missing facts are warnings, not errors — a guide can be ``partial`` and still useful.
"""

from __future__ import annotations

from dataclasses import dataclass

STAGES = {"input", "resolve", "research", "extract", "assemble", "verify", "render", "storage", "web", "job"}


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    stage: str
    status: int
    user_message: str
    retryable: bool
    operator_hint: str


def _e(code: str, stage: str, status: int, user: str, retryable: bool, hint: str) -> ErrorSpec:
    return ErrorSpec(code, stage, status, user, retryable, hint)


ERRORS: dict[str, ErrorSpec] = {
    # input
    "INPUT_EMPTY": _e("INPUT_EMPTY", "input", 400,
                      "Enter an app name or a website.", False, "empty POST body"),
    "INPUT_TOO_LONG": _e("INPUT_TOO_LONG", "input", 400,
                         "That input is too long. Use an app name or a website address.", False,
                         "input > 200 chars"),
    "INPUT_CONTROL_CHARS": _e("INPUT_CONTROL_CHARS", "input", 400,
                              "That input has characters we can't use. Paste the app name or website.", False,
                              "control characters / bidi overrides / zero-width chars"),
    "INPUT_BAD_SCHEME": _e("INPUT_BAD_SCHEME", "input", 400,
                           "Use the app's normal website address, not a file or app link.", False,
                           "scheme not http/https (file, javascript, data, mailto, ftp, ws)"),
    "INPUT_CREDENTIALS": _e("INPUT_CREDENTIALS", "input", 400,
                            "Remove the username and password from the address.", False,
                            "userinfo present in the URL — never forward or log it"),
    "INPUT_BAD_PORT": _e("INPUT_BAD_PORT", "input", 400,
                         "Use the app's normal website address without a custom port.", False,
                         "port not 80/443/absent"),
    "INPUT_BAD_HOST": _e("INPUT_BAD_HOST", "input", 400,
                         "That doesn't look like a website address. Try the app's main domain.", False,
                         "IP literal, localhost, .local/.internal suffix, bad label or no TLD"),
    # resolve
    "AMBIGUOUS_PROVIDER": _e("AMBIGUOUS_PROVIDER", "resolve", 409,
                             "That name matches more than one app. Pick one below.", False,
                             "alias/fuzzy tie — return candidates, never pick"),
    "PROVIDER_UNKNOWN": _e("PROVIDER_UNKNOWN", "resolve", 404,
                           "We couldn't match that. Try the app's website address instead.", False,
                           "no registry hit and no OIDC discovery"),
    "HOST_BLOCKED": _e("HOST_BLOCKED", "resolve", 400,
                       "That address can't be checked from here. Use the app's main website.", False,
                       "SSRF guard rejected a private/loopback/metadata IP"),
    "DNS_FAILED": _e("DNS_FAILED", "resolve", 502,
                     "We couldn't find that website. Check the spelling.", True,
                     "NXDOMAIN / no A/AAAA record"),
    "FETCH_TIMEOUT": _e("FETCH_TIMEOUT", "resolve", 504,
                        "That website took too long to answer. Try again.", True,
                        "probe exceeded 10s"),
    "FETCH_FAILED": _e("FETCH_FAILED", "resolve", 502,
                       "We couldn't read that website.", True,
                       "TLS/connection/redirect error — record the failing hop"),
    # research
    "DOCS_NOT_FOUND": _e("DOCS_NOT_FOUND", "research", 404,
                         "We couldn't find setup docs for this app.", False,
                         "seed URLs all 404; check the provider registry docs list"),
    "DOCS_FORBIDDEN": _e("DOCS_FORBIDDEN", "research", 502,
                         "This provider blocks automated reading of its docs. Open their docs directly.", False,
                         "403/401 from docs host; link out, do not retry loops"),
    "DOCS_RATE_LIMITED": _e("DOCS_RATE_LIMITED", "research", 429,
                            "The provider is limiting requests right now. Try again in a few minutes.", True,
                            "429 from docs host; backoff and cache"),
    "DOCS_LOGIN_WALLED": _e("DOCS_LOGIN_WALLED", "research", 502,
                            "These docs are behind a login, so we can't read them.", False,
                            "redirect to /login or SSO; record the final URL"),
    "DOCS_UNPARSEABLE": _e("DOCS_UNPARSEABLE", "research", 422,
                           "We found the docs but couldn't read the setup steps.", False,
                           "no auth/scopes/redirect sections after selection"),
    # extract
    "LLM_NOT_CONFIGURED": _e("LLM_NOT_CONFIGURED", "extract", 503,
                             "The guide service isn't available right now.", False,
                             "OPENCODE_API_KEY missing or empty"),
    "LLM_AUTH_FAILED": _e("LLM_AUTH_FAILED", "extract", 503,
                          "The guide service isn't available right now.", False,
                          "401/403 from inference; check key workspace/plan"),
    "LLM_MODEL_UNAVAILABLE": _e("LLM_MODEL_UNAVAILABLE", "extract", 503,
                                "The guide service is busy. Try again shortly.", True,
                                "model not in /v1/models for this plan"),
    "LLM_RATE_LIMITED": _e("LLM_RATE_LIMITED", "extract", 429,
                           "The guide service is busy. Try again in a minute.", True,
                           "429 from inference; count attempts and backoff"),
    "LLM_TIMEOUT": _e("LLM_TIMEOUT", "extract", 504,
                      "The guide took too long to generate. Try again.", True,
                      "chunk call exceeded the per-call timeout"),
    "LLM_BAD_OUTPUT": _e("LLM_BAD_OUTPUT", "extract", 502,
                         "We couldn't turn those docs into a reliable guide.", True,
                         "JSON invalid or schema-invalid after 2 repairs; keep the raw response"),
    "LLM_REFUSED": _e("LLM_REFUSED", "extract", 422,
                      "We stopped rather than guess: the guide service declined to read this page.", False,
                      "refusal/finish_reason=content_filter; record prompt hash"),
    # assemble / verify
    "SPEC_INCOMPLETE": _e("SPEC_INCOMPLETE", "assemble", 422,
                          "The docs don't state enough to write a setup guide.", False,
                          "no auth path or no endpoints after assembly"),
    "VERIFY_FAILED": _e("VERIFY_FAILED", "verify", 502,
                        "The guide failed our accuracy check, so we didn't publish it.", True,
                        "provenance/domain/cross-format gate failed; list failing fields"),
    # render / storage / job
    "RENDER_FAILED": _e("RENDER_FAILED", "render", 500,
                        "We couldn't render the guide.", True,
                        "template error — include template name and job id"),
    "STORAGE_FAILED": _e("STORAGE_FAILED", "storage", 500,
                         "Something broke on our side.", True,
                         "SQLite lock/disk full; include the sqlite error"),
    "JOB_NOT_FOUND": _e("JOB_NOT_FOUND", "job", 404,
                        "That guide doesn't exist or has expired.", False,
                        "unknown job id or retention window passed"),
    "JOB_TIMEOUT": _e("JOB_TIMEOUT", "job", 504,
                      "This guide is taking longer than expected. Check back in a minute.", True,
                      "worker exceeded 120s — record the last completed stage"),
    "INTERNAL": _e("INTERNAL", "job", 500,
                   "Something broke on our side. Send us the job id below.", False,
                   "unhandled exception — log the traceback with the job id"),
}

WARNINGS: dict[str, str] = {
    "TIME_UNKNOWN": "Setup time is an estimate: the docs don't state review timelines.",
    "FIELD_UNVERIFIED": "Some details weren't stated in the docs and are marked as such.",
    "ENDPOINT_CONFLICT": "Sources disagree on an endpoint. The guide shows the first value found; the alternatives are under \"Sources that disagree\" — check which product surface you are integrating.",
    "PARTIAL_GUIDE": "This guide is partial — the sections marked 'not stated' could not be verified.",
}


def spec(code: str) -> ErrorSpec:
    try:
        return ERRORS[code]
    except KeyError as exc:
        raise KeyError(f"unknown error code: {code}") from exc


def user_payload(code: str, job_id: str | None = None, *, retryable: bool | None = None) -> dict:
    """The only shape the browser ever sees. No operator_hint, no detail, no stack."""
    s = spec(code)
    payload = {
        "error": {
            "code": s.code,
            "stage": s.stage,
            "message": s.user_message,
            "retryable": s.retryable if retryable is None else retryable,
        }
    }
    if job_id:
        payload["error"]["job_id"] = job_id
    return payload


class GuideError(Exception):
    """Raised inside the pipeline; carries the code and operator detail, never rendered raw."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.spec = spec(code)
        self.detail = detail
        super().__init__(f"{code}: {detail or self.spec.user_message}")
