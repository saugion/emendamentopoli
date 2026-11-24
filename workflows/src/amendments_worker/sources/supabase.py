"""Thin PostgREST helpers (Supabase): paginated reads, chunked upserts, RPC."""

import os

import httpx

from .common import SUPABASE_PAGE

SAVE_CHUNK = 500


def supabase_base_and_key() -> tuple[str, str]:
    """Returns (SUPABASE_URL without trailing slash, SUPABASE_KEY) from the environment."""
    return os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_KEY"]


def supabase_headers(key: str, prefer: str | None = None) -> dict[str, str]:
    """apikey + Authorization Bearer + Content-Type: application/json (+ Prefer if given)."""
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


async def select_all(
    client: httpx.AsyncClient,
    table: str,
    select: str,
    filters: dict[str, str] | None = None,
    page_size: int = SUPABASE_PAGE,
    key: str = "id",
) -> list[dict]:
    """Reads every row of `table` matching `filters` with keyset pagination on the column `key`.

    Request i: GET {base}/rest/v1/{table} with params {"select": select, "order": "{key}.asc",
    "limit": str(page_size), **filters} and, from the second request on, "{key}": f"gt.{last}"
    where last is the `key` of the last row of the previous page (select MUST include the key column).
    Stops when a page returns fewer than page_size rows. Raises on non-2xx.
    """
    base, api_key = supabase_base_and_key()
    headers = supabase_headers(api_key)
    rows: list[dict] = []
    last: object = None
    while True:
        params: dict[str, str] = {
            "select": select,
            "order": f"{key}.asc",
            "limit": str(page_size),
            **(filters or {}),
        }
        if last is not None:
            params[key] = f"gt.{last}"
        resp = await client.get(f"{base}/rest/v1/{table}", params=params, headers=headers)
        resp.raise_for_status()
        page = resp.json()
        rows.extend(page)
        if len(page) < page_size:
            return rows
        last = page[-1][key]


async def upsert(
    client: httpx.AsyncClient,
    table: str,
    rows: list[dict],
    on_conflict: str,
    ignore_duplicates: bool = False,
    return_rows: bool = False,
) -> list[dict]:
    """POST {base}/rest/v1/{table}?on_conflict={on_conflict} in chunks of SAVE_CHUNK.

    Prefer = "resolution=ignore-duplicates" if ignore_duplicates else "resolution=merge-duplicates",
    plus ",return=representation" if return_rows else ",return=minimal".
    Returns the concatenated JSON bodies when return_rows, else [].
    """
    base, api_key = supabase_base_and_key()
    resolution = "ignore-duplicates" if ignore_duplicates else "merge-duplicates"
    ret = "representation" if return_rows else "minimal"
    headers = supabase_headers(api_key, prefer=f"resolution={resolution},return={ret}")
    out: list[dict] = []
    for i in range(0, len(rows), SAVE_CHUNK):
        chunk = rows[i : i + SAVE_CHUNK]
        resp = await client.post(
            f"{base}/rest/v1/{table}",
            params={"on_conflict": on_conflict},
            json=chunk,
            headers=headers,
        )
        resp.raise_for_status()
        if return_rows:
            out.extend(resp.json())
    return out


async def rpc(client: httpx.AsyncClient, function: str, payload: dict) -> object:
    """POST {base}/rest/v1/rpc/{function} with JSON payload; returns resp.json()."""
    base, api_key = supabase_base_and_key()
    resp = await client.post(
        f"{base}/rest/v1/rpc/{function}",
        json=payload,
        headers=supabase_headers(api_key),
    )
    resp.raise_for_status()
    return resp.json()
