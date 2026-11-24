"""Pure ingest policy: period scan, dedup, signatory backfill, incremental save + evaluate.

No IO in this module (no httpx, no dati.senato.it): source, texts, store and
evaluator are injected as adapters at the seam. The workflow wires the real
adapters (SPARQL, AKN, Supabase, Mistral); tests drive ingest_period with fakes
through the same interface.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .evaluation import Evaluation
    from .models import Amendment

SPARQL_PAGE = 100
ENRICH_CONCURRENCY = 5
EVALUATION_CONCURRENCY = 10


@dataclass
class IngestDeps:
    fetch_page: Callable[[int, int, int], Awaitable[list["Amendment"]]]
    enrich: Callable[["Amendment"], Awaitable["Amendment"]]
    saved_map: Callable[[int], Awaitable[dict[str, dict[str, str | None]]]]
    save: Callable[[list["Amendment"]], Awaitable[int]]
    evaluate: Callable[["Amendment"], Awaitable["Evaluation"]]
    save_evaluations: Callable[[list["Evaluation"]], Awaitable[int]]
    page_size: int = SPARQL_PAGE
    enrich_concurrency: int = ENRICH_CONCURRENCY
    evaluation_concurrency: int = EVALUATION_CONCURRENCY


@dataclass
class IngestReport:
    metadata_fetched: int
    analyzed: int
    found: int
    saved: int
    evaluated: int
    ids: list[str]
    errors: list[str]
    evaluation_errors: list[str]


async def ingest_period(
    start_date: str,
    end_date: str,
    legislature: int,
    limit: int,
    deps: IngestDeps,
) -> IngestReport:
    enrich_sem = asyncio.Semaphore(deps.enrich_concurrency)

    async def _enrich(m: "Amendment") -> "Amendment":
        async with enrich_sem:
            return await deps.enrich(m)

    already_saved = await deps.saved_map(legislature)
    eval_sem = asyncio.Semaphore(deps.evaluation_concurrency)

    async def _evaluate(a: "Amendment") -> "Evaluation":
        async with eval_sem:
            return await deps.evaluate(a)

    metadata: list["Amendment"] = []
    analyzed_count = 0
    in_period: list["Amendment"] = []
    errors: list[str] = []
    evaluation_errors: list[str] = []
    saved = 0
    evaluated = 0
    offset = 0
    while len(metadata) < limit:
        try:
            page = await deps.fetch_page(
                legislature, min(deps.page_size, limit - len(metadata)), offset
            )
        except Exception as e:
            errors.append(f"fetch_metadata_page offset={offset}: {type(e).__name__}: {e}")
            break
        if not page:
            break
        metadata.extend(page)
        offset += len(page)
        # re-enrich amendments already saved but still without signatories (backfill)
        to_enrich = [
            m
            for m in page
            if m.id not in already_saved or not already_saved[m.id]["firmatari"]
        ]
        results = await asyncio.gather(
            *[_enrich(m) for m in to_enrich],
            return_exceptions=True,
        )
        errors.extend(
            f"{m.id}: {type(r).__name__}: {r}"
            for m, r in zip(to_enrich, results)
            if isinstance(r, BaseException)
        )
        page_ok = [r for r in results if not isinstance(r, BaseException)]
        analyzed_count += len(page_ok)
        dates = [a.date for a in page_ok if a.date]
        dates += [
            already_saved[m.id]["data"]
            for m in page
            if m.id in already_saved and already_saved[m.id]["data"]
        ]
        page_in_period = [a for a in page_ok if a.date and start_date <= a.date <= end_date]
        if page_in_period:
            saved += await deps.save(page_in_period)
            in_period.extend(page_in_period)
            # evaluate only newly seen, money-relevant amendments: already-saved ones have a
            # valutazione already, and non-relevant ones never reach the LLM (pre-filter R1)
            to_evaluate = [
                a for a in page_in_period if a.id not in already_saved and a.relevant_spending
            ]
            eval_results = await asyncio.gather(
                *[_evaluate(a) for a in to_evaluate],
                return_exceptions=True,
            )
            evaluation_errors.extend(
                f"{a.id}: {type(r).__name__}: {r}"
                for a, r in zip(to_evaluate, eval_results)
                if isinstance(r, BaseException)
            )
            evaluations = [r for r in eval_results if not isinstance(r, BaseException)]
            evaluated += await deps.save_evaluations(evaluations) if evaluations else 0
        if dates and min(dates) < start_date:
            break

    return IngestReport(
        metadata_fetched=len(metadata),
        analyzed=analyzed_count,
        found=len(in_period),
        saved=saved,
        evaluated=evaluated,
        ids=[a.id for a in in_period],
        errors=errors,
        evaluation_errors=evaluation_errors,
    )
