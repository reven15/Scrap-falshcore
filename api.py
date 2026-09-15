#!/usr/bin/env python3
"""API HTTP para o scraper de odds do Flashscore.

Pensada para ser chamada por outro pipeline: pedes um scrape (liga, data,
mercados), recebes um job_id de imediato, e consultas o resultado depois
— um scrape de uma liga inteira pode demorar minutos, por isso não faz
sentido bloquear o pedido HTTP à espera.

Endpoints:
    POST /scrape        -> cria um job, devolve {job_id, status_url}
    GET  /jobs/{job_id}  -> {status: pending|running|done|error, result?, error?}
    GET  /health         -> {status: ok}
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from scraper import scrape_league_odds

log = logging.getLogger("flashscore.api")

app = FastAPI(title="Flashscore Odds Scraper", version="1.0.0")

# Um scrape usa um browser Chromium inteiro; manter poucos workers em
# paralelo evita esgotar CPU/RAM no home server.
_executor = ThreadPoolExecutor(max_workers=2)
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

JobStatus = Literal["pending", "running", "done", "error"]


class ScrapeRequest(BaseModel):
    league_url: str = Field(..., description="URL da página da liga no Flashscore")
    date: str | None = Field(
        default=None, description="Data DD.MM.YYYY dos jogos a extrair (default: hoje)"
    )
    markets: list[str] | None = Field(
        default=None,
        description="Slugs de mercados a extrair (ex: ['1x2', 'mais-menos']). Omitido = todos.",
    )
    delay: float = Field(default=1.5, ge=0, description="Segundos de espera entre pedidos ao site")


class ScrapeAccepted(BaseModel):
    job_id: str
    status_url: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    created_at: str
    request: ScrapeRequest
    result: dict | None = None
    error: str | None = None


def _run_job(job_id: str, req: ScrapeRequest) -> None:
    with _jobs_lock:
        _jobs[job_id]["status"] = "running"
    try:
        target_date = req.date or dt.date.today().strftime("%d.%m.%Y")
        result = scrape_league_odds(
            league_url=req.league_url,
            target_date=target_date,
            delay=req.delay,
            markets=req.markets,
        )
        with _jobs_lock:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["result"] = result
    except Exception as exc:  # o job fica registado com o erro, nunca "desaparece"
        log.exception("Job %s falhou", job_id)
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(exc)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/scrape", response_model=ScrapeAccepted, status_code=202)
def create_scrape(req: ScrapeRequest) -> ScrapeAccepted:
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "pending",
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "request": req,
            "result": None,
            "error": None,
        }
    _executor.submit(_run_job, job_id, req)
    return ScrapeAccepted(job_id=job_id, status_url=f"/jobs/{job_id}")


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: str) -> JobStatusResponse:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job desconhecido")
        return JobStatusResponse(**job)
