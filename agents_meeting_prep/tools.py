"""GCP tools for agents_meeting_prep.

Combines:
1. Dataplex Context Set (wealth-advisor-cs) discovery & read-only BigQuery SQL
   tools reused from agents_bq_cs/tools.py.
2. Business-context domain tools (find_client, get_client_profile, get_holdings,
   get_valuations, get_ips, get_prior_plan, get_crm_interactions,
   get_portfolio_alerts) that resolve table schemas via the Context Set first.
3. Operational BigQuery helpers for wealth_meeting_prep (meeting_context and
   meeting_prep_cache, which sit outside the Context Set).
4. Deterministic compute_pre_flight(client_id, today) backed by Context Set tools.
5. Self-contained market tools (with business-context names + legacy aliases).
"""

import json
import logging
import math
import os
import re
import time
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_SA_KEY = Path(__file__).resolve().parent.parent / "catalog" / "agentic-wealthadvisor-9e4f503e15c0.json"
if _SA_KEY.exists():
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_SA_KEY)

_COMBINED_CA = Path(__file__).resolve().parent.parent / "catalog" / "combined_ca.pem"
if _COMBINED_CA.exists():
    os.environ["REQUESTS_CA_BUNDLE"] = str(_COMBINED_CA)
    os.environ["SSL_CERT_FILE"] = str(_COMBINED_CA)

import google.auth
import google.auth.transport.requests
import requests
from google.cloud import bigquery, dataplex_v1
from google.cloud.dataplex_v1 import EntryView, GetEntryRequest, ListEntriesRequest

from agents_meeting_prep.market_tools import (
    get_approved_commentary as _get_approved_commentary,
    get_benchmark_returns,
    get_fund_news_summary as _get_fund_news_summary,
    get_market_indicators,
    get_portfolio_beta as _get_portfolio_beta,
)

logger = logging.getLogger(__name__)

def _require_env(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(
            f"Missing required environment variable: {name}. Set it in .env or runtime environment."
        )
    return val


PROJECT = _require_env("GCP_PROJECT_ID")
DATASET = _require_env("BQ_DATASET")
MP_DATASET = _require_env("BQ_MP_DATASET")
LOG_DATASET = _require_env("BQ_LOG_DATASET")
LOCATION = _require_env("GCP_LOCATION")
CONTEXT_SET_ID = _require_env("CONTEXT_SET_ID")

CS_EG_NAME = f"projects/{PROJECT}/locations/{LOCATION}/entryGroups/{CONTEXT_SET_ID}"
LOOKUP_URL = f"https://dataplex.googleapis.com/v1/projects/{PROJECT}/locations/{LOCATION}:lookupContext"

_BQ = bigquery.Client(project=PROJECT)
_CATALOG = dataplex_v1.CatalogServiceClient(transport="rest")

_CACHE: dict = {}
_CACHE_TTL = 3600
_AGENT_CTX: dict = {"session_id": "", "advisor_input": ""}


def set_agent_context(session_id: str, advisor_input: str) -> None:
    _AGENT_CTX["session_id"] = session_id
    _AGENT_CTX["advisor_input"] = advisor_input


def _cache_get(key: str):
    e = _CACHE.get(key)
    if e and (time.time() - e["ts"]) < _CACHE_TTL:
        return e["val"]
    return None


def _cache_set(key: str, val: Any) -> None:
    _CACHE[key] = {"val": val, "ts": time.time()}


def _bearer_token() -> str:
    cached = _cache_get("__bearer__")
    if cached:
        return cached
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    _cache_set("__bearer__", creds.token)
    return creds.token


def _serialize_bq_row(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, (datetime, date, dtime)):
            out[k] = v.isoformat()
        elif isinstance(v, float) and not math.isfinite(v):
            out[k] = None
        else:
            out[k] = v
    return out


def _bq_table_ref(table: str) -> str:
    return f"`{PROJECT}.{DATASET}.{table}`"


def _bq_mp_table_ref(table: str) -> str:
    return f"`{PROJECT}.{MP_DATASET}.{table}`"


def _cs_table_entry_name(table: str) -> str:
    return f"{CS_EG_NAME}/entries/assets/bigquery/{PROJECT}/{DATASET}/{table}"


def _get_entry(entry_name: str):
    cached = _cache_get(entry_name)
    if cached:
        return cached
    entry = _CATALOG.get_entry(request=GetEntryRequest(name=entry_name, view=EntryView.ALL))
    _cache_set(entry_name, entry)
    return entry


def _get_overview_content(entry_name: str) -> str:
    try:
        entry = _get_entry(entry_name)
    except Exception:
        return ""
    for k, v in entry.aspects.items():
        if k.endswith(".overview"):
            return dict(v.data).get("content", "")
    return ""


# ── Context Set & BigQuery Tools (reused from agents_bq_cs) ─────────────────

def list_catalog_nodes(layer: str = "") -> list[dict]:
    """List available BQ tables from the wealth-advisor-cs Context Set."""
    cache_key = "cs_list_nodes"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        stubs = list(_CATALOG.list_entries(request=ListEntriesRequest(parent=CS_EG_NAME, page_size=100)))
    except Exception as e:
        return [{"error": str(e)}]

    results = []
    prefix = f"assets/bigquery/{PROJECT}/{DATASET}/"
    for e in stubs:
        eid = e.name.split("/entries/")[-1]
        if not eid.startswith(prefix):
            continue
        table = eid[len(prefix):]
        if "/" in table:
            continue
        results.append({
            "node_name": table,
            "bq_table": _bq_table_ref(table),
            "entry_name": e.name,
            "description": e.entry_source.description if e.entry_source else "",
        })

    _cache_set(cache_key, results)
    return results


def _parse_columns_from_yaml(yaml_str: str) -> list[dict]:
    columns = []
    in_schema = False
    for line in yaml_str.splitlines():
        stripped = line.strip()
        if stripped == "schema:":
            in_schema = True
            continue
        if in_schema:
            if stripped.startswith("name:"):
                columns.append({"name": stripped.split(":", 1)[1].strip(), "type": "", "mode": ""})
            elif stripped.startswith("type:") and columns:
                columns[-1]["type"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("mode:") and columns:
                columns[-1]["mode"] = stripped.split(":", 1)[1].strip()
            elif stripped and not stripped.startswith("-") and ":" in stripped:
                key = stripped.split(":")[0].strip()
                if key not in ("name", "type", "mode", "description", "fields"):
                    in_schema = False
    return columns


def lookup_context(table_names: list[str], context_budget: int = 12000) -> dict:
    """Call Dataplex LookupContext API using wealth-advisor-cs entry names."""
    table_names = [t.lower() for t in table_names]
    cache_key = f"cs_ctx_{'_'.join(sorted(table_names))}_{context_budget}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    entry_names = [_cs_table_entry_name(t) for t in table_names]
    ca_verify = str(_COMBINED_CA) if _COMBINED_CA.exists() else True
    chunks = [entry_names[i:i + 10] for i in range(0, len(entry_names), 10)]
    all_context = []

    for chunk in chunks:
        try:
            resp = requests.post(
                LOOKUP_URL,
                headers={"Authorization": f"Bearer {_bearer_token()}", "Content-Type": "application/json"},
                json={"resources": chunk, "options": {"format": "yaml", "context_budget": str(context_budget)}},
                verify=ca_verify,
            )
        except Exception as e:
            return {"error": str(e)}
        if resp.status_code != 200:
            return {"error": resp.text, "status_code": resp.status_code}
        ctx = resp.json().get("context", "")
        if ctx:
            all_context.append(ctx)

    merged = "\n".join(all_context)
    result = {"context": merged, "entry_names": entry_names, "char_count": len(merged)}
    _cache_set(cache_key, result)
    return result


def lookup_schema(node_name: str) -> dict:
    """Look up schema for a BQ table via the wealth-advisor-cs Context Set."""
    cache_key = f"cs_schema_{node_name.lower()}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    entry_name = _cs_table_entry_name(node_name.lower())
    ctx_result = lookup_context([node_name.lower()])
    if "error" in ctx_result:
        return {"error": ctx_result["error"]}

    cols = _parse_columns_from_yaml(ctx_result.get("context", ""))
    if not cols:
        tname = re.sub(r"[^A-Za-z0-9_]", "", node_name.lower())
        try:
            bq_cols = _BQ.query(
                f"SELECT column_name AS name, data_type AS type, is_nullable AS mode "
                f"FROM `{PROJECT}.{DATASET}.INFORMATION_SCHEMA.COLUMNS` "
                f"WHERE LOWER(table_name) = '{tname}' ORDER BY ordinal_position"
            ).result()
            cols = [
                {"name": r["name"], "type": r["type"], "mode": r["mode"]}
                for r in bq_cols
            ]
        except Exception as e:
            logger.warning("INFORMATION_SCHEMA fallback failed for %s: %s", node_name, e)
    overview = _get_overview_content(entry_name)
    result = {
        "node_name": node_name.lower(),
        "bq_table": _bq_table_ref(node_name.lower()),
        "columns": cols,
        "overview": overview[:3000] if overview else "",
    }
    _cache_set(cache_key, result)
    return result


def _list_doc_entries() -> list[str]:
    cached = _cache_get("cs_doc_entries")
    if cached:
        return cached
    try:
        stubs = list(_CATALOG.list_entries(request=ListEntriesRequest(parent=CS_EG_NAME, page_size=100)))
    except Exception:
        return []
    entries = [
        s.name for s in stubs
        if "/entries/documents/" in s.name
        and "/entries/documents/concepts/" not in s.name
        and s.name.split("/entries/documents/")[-1]
    ]
    _cache_set("cs_doc_entries", entries)
    return entries


def lookup_catalog_rule(concept_name: str) -> dict:
    """Look up a rule or policy from any document entry in wealth-advisor-cs."""
    cache_key = f"cs_rule_{concept_name.lower()}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    keyword = concept_name.lower().replace("-", "_").replace(" ", "_")
    doc_names = _list_doc_entries()
    if not doc_names:
        return {"error": "No document entries found in CS"}

    best_content, best_doc_id, best_score = "", "", -1
    for entry_name in doc_names:
        doc_id = entry_name.split("/entries/documents/")[-1]
        content = _get_overview_content(entry_name)
        if not content:
            continue
        id_score = 100 if keyword.replace("_", "-") in doc_id or keyword in doc_id else 0
        phrase_score = 10 if keyword.replace("_", " ") in content.lower() or keyword in content.lower() else 0
        frag_score = sum(1 for part in keyword.split("_") if part and part in content.lower())
        score = id_score + phrase_score + frag_score
        if score > best_score:
            best_score, best_content, best_doc_id = score, content, doc_id

    if not best_content:
        return {"error": f"No document content found in CS for '{concept_name}'"}

    result = {
        "scenario_id": keyword,
        "display_name": keyword.replace("_", " ").title(),
        "description": best_content,
        "source_doc": best_doc_id,
    }
    _cache_set(cache_key, result)
    return result


def _qualify_table_names(sql: str) -> str:
    if f"{PROJECT}.{DATASET}" in sql:
        return sql
    nodes = list_catalog_nodes()
    table_map = {n["node_name"]: n["bq_table"] for n in nodes if n.get("bq_table")}

    def replacer(m):
        kw, tname = m.group(1), m.group(2).strip("`")
        return f"{kw} {table_map.get(tname, f'`{PROJECT}.{DATASET}.{tname}`')}"

    return re.sub(r"\b(FROM|JOIN)\s+`?(\w+)`?", replacer, sql, flags=re.IGNORECASE)


def execute_bq_query(sql_query: str) -> list[dict]:
    """Execute a read-only SQL SELECT query against BigQuery."""
    blocked = ("INSERT", "UPDATE", "DELETE", "DROP", "MERGE", "TRUNCATE", "CREATE")
    if any(kw in sql_query.upper() for kw in blocked):
        return [{"error": "Only read-only SELECT queries are allowed."}]
    sql_query = _qualify_table_names(sql_query)
    try:
        rows = _BQ.query(sql_query).result()
        return [_serialize_bq_row(dict(row)) for row in rows]
    except Exception as e:
        return [{"error": str(e)}]


# ── Business-Context Data Tools (Context Set First) ──────────────────────────

def _cs_table(table_name: str) -> str:
    """Resolve a BigQuery table reference via the wealth-advisor-cs Context Set."""
    schema = lookup_schema(table_name)
    return schema.get("bq_table") or _bq_table_ref(table_name)


def _safe_cid(client_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", client_id or "")


def find_client() -> Optional[list[dict[str, str]]]:
    """List all clients (client_id, name) discovered via the Context Set."""
    try:
        tbl = _cs_table("client")
        rows = execute_bq_query(f"SELECT client_id, name FROM {tbl}")
        if rows and "error" in rows[0]:
            logger.error("find_client query failed: %s", rows[0]["error"])
            return None
        return rows
    except Exception as e:
        logger.error("find_client failed via Context Set: %s", e)
        return None


def get_client_profile(client_id: str) -> dict:
    """Fetch client profile attributes via Context Set discovery."""
    cid = _safe_cid(client_id)
    tbl = _cs_table("client")
    rows = execute_bq_query(f"SELECT * FROM {tbl} WHERE client_id = '{cid}' LIMIT 1")
    return rows[0] if rows and "error" not in rows[0] else {}


def get_holdings(client_id: str) -> list[dict]:
    """Fetch holdings joined with account & instrument via Context Set discovery."""
    cid = _safe_cid(client_id)
    lookup_context(["account", "holding", "instrument"])
    a_tbl = _cs_table("account")
    h_tbl = _cs_table("holding")
    i_tbl = _cs_table("instrument")
    inst_cols = {c.get("name", "").lower() for c in (lookup_schema("instrument").get("columns") or [])}
    optional_inst = []
    if "benchmark_index" in inst_cols:
        optional_inst.append("i.benchmark_index AS benchmark")
    if "category" in inst_cols:
        optional_inst.append("i.category")
    if "return_2024" in inst_cols:
        optional_inst.append("i.return_2024")
    extra_cols = (", " + ", ".join(optional_inst)) if optional_inst else ""
    sql = (
        f"SELECT h.*, i.name AS fund_name, i.instrument_id AS ticker, i.asset_class{extra_cols} "
        f"FROM {h_tbl} h "
        f"JOIN {a_tbl} a ON h.account_id = a.account_id "
        f"JOIN {i_tbl} i ON h.instrument_id = i.instrument_id "
        f"WHERE a.client_id = '{cid}'"
    )
    rows = execute_bq_query(sql)
    if rows and "error" in rows[0]:
        logger.warning("get_holdings query failed for %s: %s", cid, rows[0]["error"])
        if extra_cols:
            fallback_sql = (
                f"SELECT h.*, i.name AS fund_name, i.instrument_id AS ticker, i.asset_class "
                f"FROM {h_tbl} h "
                f"JOIN {a_tbl} a ON h.account_id = a.account_id "
                f"JOIN {i_tbl} i ON h.instrument_id = i.instrument_id "
                f"WHERE a.client_id = '{cid}'"
            )
            rows = execute_bq_query(fallback_sql)
            if rows and "error" in rows[0]:
                logger.error("get_holdings fallback query failed for %s: %s", cid, rows[0]["error"])
                return []
        else:
            return []
    valid_rows = [r for r in rows if "error" not in r]
    total_mv = sum(float(r.get("market_value") or 0.0) for r in valid_rows)
    if total_mv > 0:
        for r in valid_rows:
            w = r.get("actual_weight")
            if w is None:
                w = round(100.0 * float(r.get("market_value") or 0.0) / total_mv, 2)
                r["actual_weight"] = w
            r.setdefault("weight", w)
            r.setdefault("weight_pct", w)
    return valid_rows


def get_valuations(client_id: str) -> dict:
    """Compute portfolio total value, cost basis, and unrealized gain/loss via Context Set."""
    rows = get_holdings(client_id)
    total_val = round(sum(float(r.get("market_value") or 0.0) for r in rows), 2)
    cost_basis = round(sum(float(r.get("cost_basis") or 0.0) for r in rows), 2)
    return {
        "total_value": total_val,
        "cost_basis": cost_basis,
        "unrealized_gl": round(total_val - cost_basis, 2),
        "holding_count": len(rows),
    }


def get_ips(client_id: str) -> dict:
    """Fetch client's Investment Policy Statement via Context Set discovery."""
    cid = _safe_cid(client_id)
    tbl = _cs_table("investment_policy_statement")
    rows = execute_bq_query(f"SELECT * FROM {tbl} WHERE client_id = '{cid}' LIMIT 1")
    if not rows or "error" in rows[0]:
        return {}
    ips = rows[0]
    if ips.get("equity_min") is None or ips.get("max_single_fund_weight") is None:
        return _enrich_ips_bands(ips, get_client_profile(cid))
    return ips


def get_prior_plan(client_id: str) -> list[dict]:
    """Fetch client's investment objectives / goals via Context Set discovery."""
    cid = _safe_cid(client_id)
    tbl = _cs_table("investment_objective")
    rows = execute_bq_query(f"SELECT * FROM {tbl} WHERE client_id = '{cid}'")
    return [r for r in rows if "error" not in r]


def get_crm_interactions(client_id: str, limit: int = 6) -> list[dict]:
    """Fetch recent CRM interactions (joined with Decision when present) via Context Set discovery."""
    cid = _safe_cid(client_id)
    i_tbl = _cs_table("interaction")
    d_tbl = _cs_table("decision")
    join_sql = (
        f"SELECT i.*, d.type AS decision_type, d.initiated_by, d.description AS decision_description "
        f"FROM {i_tbl} i "
        f"LEFT JOIN {d_tbl} d ON d.interaction_id = i.interaction_id "
        f"WHERE i.client_id = '{cid}' ORDER BY i.date DESC LIMIT {int(limit)}"
    )
    rows = execute_bq_query(join_sql)
    if rows and "error" in rows[0]:
        logger.warning("get_crm_interactions join failed for %s, falling back: %s", cid, rows[0]["error"])
        rows = execute_bq_query(
            f"SELECT * FROM {i_tbl} WHERE client_id = '{cid}' ORDER BY date DESC LIMIT {int(limit)}"
        )
    return [r for r in rows if "error" not in r]


def get_portfolio_alerts(client_id: str) -> list[dict]:
    """Fetch open risk alerts for a client via Context Set discovery."""
    cid = _safe_cid(client_id)
    tbl = _cs_table("risk")
    rows = execute_bq_query(
        f"SELECT * FROM {tbl} WHERE status = 'OPEN' AND risk_id LIKE '{cid}_%'"
    )
    return [r for r in rows if "error" not in r]


def get_approved_commentary(client_id: str) -> dict:
    """Fetch recent Finnhub news for a client's significant holdings (weight > 5%)."""
    return _get_approved_commentary(client_id, holdings_fn=get_holdings)


def get_fund_news_summary(client_id: str) -> list[dict]:
    """Build advisor-facing fund performance summaries for a client's significant holdings (>5%)."""
    return _get_fund_news_summary(client_id, holdings_fn=get_holdings)


def get_portfolio_beta(client_id: str) -> dict:
    """Compute weighted portfolio beta vs SPY for a client's holdings."""
    return _get_portfolio_beta(client_id, holdings_fn=get_holdings)


# ── Operational BigQuery Helpers (wealth_meeting_prep — outside Context Set) ─

def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_meeting(meeting_id: str, meeting_date: str) -> dict:
    """Fetch a meeting row from wealth_meeting_prep.meeting_context."""
    tbl = _bq_mp_table_ref("meeting_context")
    try:
        rows = list(_BQ.query(
            f"SELECT * FROM {tbl} WHERE meeting_id = @mid AND date = @dt "
            f"ORDER BY created_at DESC LIMIT 1",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("mid", "STRING", meeting_id),
                bigquery.ScalarQueryParameter("dt", "STRING", meeting_date),
            ]),
        ).result())
        if not rows:
            return {}
        row = _serialize_bq_row(dict(rows[0]))
        if isinstance(row.get("meeting_objective"), str):
            try:
                row["meeting_objective"] = json.loads(row["meeting_objective"])
            except Exception:
                pass
        return row
    except Exception as e:
        logger.warning("get_meeting failed for %s/%s: %s", meeting_id, meeting_date, e)
        return {}


def ensure_meeting_context(
    client_id: str,
    client_name: str,
    meeting_id: Optional[str] = None,
) -> tuple[str, str]:
    """Ensure a row exists in wealth_meeting_prep.meeting_context without overwriting."""
    dt = today()
    mid = meeting_id or f"direct_{client_id}"
    try:
        existing = get_meeting(mid, dt)
        if not existing:
            tbl = _bq_mp_table_ref("meeting_context")
            objective = json.dumps({
                "statement": f"Advisory meeting with {client_name}",
                "source": "mcp_call",
                "evidence_refs": [f"MCP task for {client_name}"],
                "advisor_validation_required": False,
            })
            _BQ.query(
                f"INSERT INTO {tbl} "
                f"(meeting_id, date, client_id, client_name, time, meeting_type, "
                f"meeting_objective, attendees, source, created_at) "
                f"VALUES (@mid, @dt, @cid, @cname, @tm, @mtype, @obj, @att, @src, @cat)",
                job_config=bigquery.QueryJobConfig(query_parameters=[
                    bigquery.ScalarQueryParameter("mid", "STRING", mid),
                    bigquery.ScalarQueryParameter("dt", "STRING", dt),
                    bigquery.ScalarQueryParameter("cid", "STRING", client_id),
                    bigquery.ScalarQueryParameter("cname", "STRING", client_name),
                    bigquery.ScalarQueryParameter("tm", "STRING", "On demand"),
                    bigquery.ScalarQueryParameter("mtype", "STRING", "Virtual"),
                    bigquery.ScalarQueryParameter("obj", "STRING", objective),
                    bigquery.ArrayQueryParameter("att", "STRING", [client_name]),
                    bigquery.ScalarQueryParameter("src", "STRING", "mcp_tool"),
                    bigquery.ScalarQueryParameter("cat", "STRING", datetime.now(timezone.utc).isoformat()),
                ]),
            ).result()
    except Exception as e:
        logger.warning("ensure_meeting_context failed for %s (non-fatal): %s", mid, e)
    return mid, dt


def get_cached(cache_key: str, date_str: str, user_id: str = "") -> Optional[dict]:
    """Read cached JSON from wealth_meeting_prep.meeting_prep_cache."""
    key = f"{user_id}#{cache_key}" if user_id else cache_key
    tbl = _bq_mp_table_ref("meeting_prep_cache")
    try:
        rows = list(_BQ.query(
            f"SELECT data FROM {tbl} WHERE cache_key = @ck AND date = @dt "
            f"ORDER BY created_at DESC LIMIT 1",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("ck", "STRING", key),
                bigquery.ScalarQueryParameter("dt", "STRING", date_str),
            ]),
        ).result())
        if rows and rows[0].get("data"):
            return json.loads(rows[0]["data"])
    except Exception as e:
        logger.warning("BQ cache get failed for %s/%s: %s", key, date_str, e)
    return None


def put_cached(cache_key: str, date_str: str, data: dict, user_id: str = "") -> None:
    """Upsert cached JSON into wealth_meeting_prep.meeting_prep_cache."""
    key = f"{user_id}#{cache_key}" if user_id else cache_key
    tbl = _bq_mp_table_ref("meeting_prep_cache")
    try:
        _BQ.query(
            f"""
            MERGE {tbl} T
            USING (SELECT @ck AS cache_key, @dt AS date) S
            ON T.cache_key = S.cache_key AND T.date = S.date
            WHEN MATCHED THEN UPDATE SET
                data = @data, user_id = @uid, created_at = @cat
            WHEN NOT MATCHED THEN INSERT
                (cache_key, date, user_id, data, created_at)
                VALUES (@ck, @dt, @uid, @data, @cat)
            """,
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("ck", "STRING", key),
                bigquery.ScalarQueryParameter("dt", "STRING", date_str),
                bigquery.ScalarQueryParameter("uid", "STRING", user_id),
                bigquery.ScalarQueryParameter("data", "STRING", json.dumps(data)),
                bigquery.ScalarQueryParameter("cat", "STRING", datetime.now(timezone.utc).isoformat()),
            ]),
        ).result()
    except Exception as e:
        logger.warning("BQ cache put failed for %s/%s: %s", key, date_str, e)


# ── Deterministic Pre-Compute (via Context Set Business Tools) ───────────────

def _months_between(today_str: str, target_str: str) -> Optional[int]:
    try:
        t = date.fromisoformat(today_str)
        d = date.fromisoformat(target_str[:10])
        return max(0, (d.year - t.year) * 12 + (d.month - t.month))
    except Exception:
        return None


_DEFAULT_IPS_BANDS_BY_TOLERANCE: dict[str, dict[str, float]] = {
    "conservative": {
        "equity_min": 20.0, "equity_target": 30.0, "equity_max": 40.0,
        "bond_min": 40.0, "bond_target": 50.0, "bond_max": 60.0,
        "cash_min": 10.0, "cash_target": 20.0, "cash_max": 30.0,
        "max_single_fund_weight": 20.0,
    },
    "moderate": {
        "equity_min": 50.0, "equity_target": 60.0, "equity_max": 70.0,
        "bond_min": 15.0, "bond_target": 20.0, "bond_max": 30.0,
        "cash_min": 5.0, "cash_target": 20.0, "cash_max": 30.0,
        "max_single_fund_weight": 20.0,
    },
    "moderate growth": {
        "equity_min": 60.0, "equity_target": 70.0, "equity_max": 80.0,
        "bond_min": 15.0, "bond_target": 20.0, "bond_max": 30.0,
        "cash_min": 5.0, "cash_target": 10.0, "cash_max": 15.0,
        "max_single_fund_weight": 20.0,
    },
    "growth": {
        "equity_min": 70.0, "equity_target": 80.0, "equity_max": 95.0,
        "bond_min": 5.0, "bond_target": 15.0, "bond_max": 25.0,
        "cash_min": 2.0, "cash_target": 5.0, "cash_max": 10.0,
        "max_single_fund_weight": 20.0,
    },
    "aggressive": {
        "equity_min": 75.0, "equity_target": 85.0, "equity_max": 95.0,
        "bond_min": 0.0, "bond_target": 10.0, "bond_max": 20.0,
        "cash_min": 2.0, "cash_target": 5.0, "cash_max": 10.0,
        "max_single_fund_weight": 20.0,
    },
    "aggressive growth": {
        "equity_min": 75.0, "equity_target": 85.0, "equity_max": 95.0,
        "bond_min": 0.0, "bond_target": 10.0, "bond_max": 20.0,
        "cash_min": 2.0, "cash_target": 5.0, "cash_max": 10.0,
        "max_single_fund_weight": 20.0,
    },
}


def _enrich_ips_bands(ips: dict, client: dict) -> dict:
    """Fill missing IPS band columns from stated_risk_tolerance without overwriting DB values."""
    if not ips:
        return ips
    tol = str(client.get("stated_risk_tolerance") or "moderate").strip().lower()
    bands = _DEFAULT_IPS_BANDS_BY_TOLERANCE.get(tol, _DEFAULT_IPS_BANDS_BY_TOLERANCE["moderate"])
    enriched = dict(ips)
    for k, v in bands.items():
        if enriched.get(k) is None:
            enriched[k] = v
    return enriched


def compute_pre_flight(client_id: str, today_str: Optional[str] = None) -> dict:
    """Run deterministic threshold checks using business-context Context Set tools."""
    today_str = today_str or date.today().isoformat()
    client = get_client_profile(client_id)
    ips = _enrich_ips_bands(get_ips(client_id), client)
    holdings = get_holdings(client_id)
    goals = get_prior_plan(client_id)
    interactions = get_crm_interactions(client_id, limit=2)

    if not client or not ips:
        return {"signal_facts": [], "scenario_fires": {}}

    signal_facts: list[dict] = []
    scenario_fires = {
        "CASH_DEPLOYMENT": False,
        "CONCENTRATION_MANAGEMENT": False,
        "RETIREMENT_TRANSITION": False,
        "CHANGING_MARKET_CONDITIONS": False,
        "GOAL_FUNDING": False,
    }

    total_mv = sum(float(h.get("market_value") or 0.0) for h in holdings) or 1.0
    by_class: dict[str, float] = {}
    for h in holdings:
        ac = h.get("asset_class") or "Unknown"
        by_class[ac] = by_class.get(ac, 0.0) + float(h.get("market_value") or 0.0)

    equity_pct = round(100.0 * by_class.get("Equity", 0.0) / total_mv, 2)
    bond_pct = round(100.0 * by_class.get("Fixed Income", 0.0) / total_mv, 2)
    cash_pct = round(100.0 * by_class.get("Cash", 0.0) / total_mv, 2)

    eq_min = float(ips.get("equity_min") or 0.0)
    eq_max = float(ips.get("equity_max") or 100.0)
    eq_tgt = float(ips.get("equity_target") or 60.0)
    cash_max = float(ips.get("cash_max") or 30.0)
    cash_tgt = float(ips.get("cash_target") or 10.0)
    bond_min = float(ips.get("bond_min") or 0.0)
    max_fund = float(ips.get("max_single_fund_weight") or 20.0)

    if cash_pct > cash_max:
        signal_facts.append({
            "signal_type": "allocation_drift", "asset_class": "Cash", "current_pct": cash_pct,
            "ips_max": cash_max, "ips_target": cash_tgt, "breach": True,
            "drift": round(cash_pct - cash_max, 2), "policy_status": "breach", "materiality": "high",
        })
        scenario_fires["CASH_DEPLOYMENT"] = True
    elif cash_pct > cash_tgt:
        signal_facts.append({
            "signal_type": "allocation_drift", "asset_class": "Cash", "current_pct": cash_pct,
            "ips_target": cash_tgt, "ips_max": cash_max, "breach": False,
            "drift": round(cash_pct - cash_tgt, 2), "policy_status": "warning", "materiality": "medium",
        })
        scenario_fires["CASH_DEPLOYMENT"] = True

    if equity_pct < eq_min:
        signal_facts.append({
            "signal_type": "portfolio_underweight", "asset_class": "Equity", "current_pct": equity_pct,
            "ips_min": eq_min, "ips_target": eq_tgt, "breach": True,
            "drift": round(equity_pct - eq_min, 2), "policy_status": "breach", "materiality": "high",
        })
    elif equity_pct > eq_max:
        signal_facts.append({
            "signal_type": "portfolio_overweight", "asset_class": "Equity", "current_pct": equity_pct,
            "ips_max": eq_max, "ips_target": eq_tgt, "breach": True,
            "drift": round(equity_pct - eq_max, 2), "policy_status": "breach", "materiality": "high",
        })

    if bond_pct < bond_min and bond_min > 0:
        signal_facts.append({
            "signal_type": "portfolio_underweight", "asset_class": "Fixed Income", "current_pct": bond_pct,
            "ips_min": bond_min, "ips_target": float(ips.get("bond_target") or bond_min), "breach": True,
            "drift": round(bond_pct - bond_min, 2), "policy_status": "breach", "materiality": "medium",
        })

    concentration_fired = False
    for h in holdings:
        w = float(h.get("actual_weight") or (100.0 * float(h.get("market_value") or 0.0) / total_mv))
        if w > max_fund:
            signal_facts.append({
                "signal_type": "security_concentration",
                "fund": h.get("fund_name") or h.get("ticker") or "unknown",
                "ticker": h.get("ticker"), "current_pct": round(w, 2), "ips_max": max_fund,
                "breach": True, "excess": round(w - max_fund, 2), "policy_status": "breach",
                "materiality": "high" if (w - max_fund) > 5 else "medium",
            })
            concentration_fired = True
    if concentration_fired:
        scenario_fires["CONCENTRATION_MANAGEMENT"] = True

    net_worth = float(client.get("net_worth") or 0.0)
    cash_balance = float(client.get("cash_balance") or 0.0)
    if net_worth > 0:
        cash_nw_pct = round(100.0 * cash_balance / net_worth, 2)
        if cash_nw_pct > 15:
            signal_facts.append({
                "signal_type": "excess_cash", "cash_balance": cash_balance, "net_worth": net_worth,
                "cash_pct_of_net_worth": cash_nw_pct, "threshold": 15.0, "breach": True,
                "policy_status": "breach" if cash_nw_pct > 30 else "warning",
                "materiality": "high" if cash_nw_pct > 30 else "medium",
            })
            scenario_fires["CASH_DEPLOYMENT"] = True

    for g in goals:
        target_amount = float(g.get("target_amount") or 0.0)
        if target_amount and net_worth and target_amount > net_worth:
            gap_pct = round(100.0 * (target_amount - net_worth) / target_amount, 2)
            months_remaining = _months_between(today_str, str(g.get("target_date", "")))
            signal_facts.append({
                "signal_type": "goal_funding_gap", "goal_id": g.get("goal_id"), "goal_type": g.get("type"),
                "target_amount": target_amount, "current_net_worth": net_worth, "gap_pct": gap_pct,
                "months_remaining": months_remaining,
                "policy_status": "breach" if gap_pct > 20 else "warning",
                "materiality": "high" if gap_pct > 40 and (months_remaining is None or months_remaining <= 60) else "medium",
            })
            scenario_fires["GOAL_FUNDING"] = True
            if months_remaining is not None and months_remaining <= 60:
                scenario_fires["RETIREMENT_TRANSITION"] = True

        if str(g.get("type", "")).lower() in ("retirement", "retirement_income"):
            months_remaining = _months_between(today_str, str(g.get("target_date", "")))
            if months_remaining is not None and 0 <= months_remaining <= 60:
                signal_facts.append({
                    "signal_type": "retirement_horizon_approaching", "goal_id": g.get("goal_id"),
                    "months_remaining": months_remaining, "target_date": str(g.get("target_date")),
                    "policy_status": "warning",
                    "materiality": "high" if months_remaining <= 24 else "medium",
                })
                scenario_fires["RETIREMENT_TRANSITION"] = True

    negative = {"anxious", "risk_averse", "worried", "frustrated"}
    if len(interactions) >= 2 and all(i.get("sentiment", "") in negative for i in interactions[:2]):
        signal_facts.append({
            "signal_type": "sentiment_trend_negative",
            "last_sentiments": [i.get("sentiment") for i in interactions[:2]],
            "count": 2, "policy_status": "warning", "materiality": "high",
        })

    open_risks = get_portfolio_alerts(client_id)
    silence = next((r for r in open_risks if r.get("type") == "interaction_silence"), None)
    if silence:
        signal_facts.append({
            "signal_type": "interaction_silence", "signal_detail": silence.get("signal_detail", ""),
            "signal_value": silence.get("signal_value"), "source": "client_monitor",
            "policy_status": "warning", "materiality": "medium",
        })

    indicators = get_market_indicators()
    vix = indicators.get("vix_current")
    if vix is not None and vix >= 20.0:
        scenario_fires["CHANGING_MARKET_CONDITIONS"] = True
        signal_facts.append({
            "signal_type": "market_condition_change", "indicator": "VIX",
            "current_value": vix, "threshold": 20.0, "policy_status": "warning",
            "materiality": "medium" if vix < 25.0 else "high",
        })

    # Pre-compute holding_trend facts for significant holdings (>8%, non-CASH/MMKT)
    sig_holdings = [
        h for h in holdings
        if (h.get("ticker") or h.get("instrument_id")) not in (None, "", "CASH", "MMKT")
        and float(h.get("actual_weight") or (100.0 * float(h.get("market_value") or 0.0) / total_mv)) > 8.0
    ]
    if sig_holdings:
        sig_tickers = [h.get("ticker") or h.get("instrument_id") for h in sig_holdings]
        returns_map = get_benchmark_returns(sig_tickers)
        for h in sig_holdings:
            tkr = h.get("ticker") or h.get("instrument_id")
            w = round(float(h.get("actual_weight") or (100.0 * float(h.get("market_value") or 0.0) / total_mv)), 2)
            rinfo = returns_map.get(tkr, {})
            signal_facts.append({
                "signal_type": "holding_trend",
                "producer_skill": "portfolio-performance-insight-mvp",
                "fund": h.get("fund_name") or tkr,
                "ticker": tkr,
                "weight_pct": w,
                "return_ytd": rinfo.get("return_ytd"),
                "return_1y": rinfo.get("return_1y"),
                "policy_status": "within",
                "materiality": "medium",
            })

    # Deterministic Risk Metrics Panel (5 metrics)
    all_interactions = get_crm_interactions(client_id, limit=6)
    dec_rows = [i for i in all_interactions if i.get("initiated_by") or i.get("decision_type") or i.get("decision")]
    total_dec = len(dec_rows) if dec_rows else len(all_interactions)
    client_led = sum(
        1 for i in (dec_rows or all_interactions)
        if str(i.get("initiated_by") or "").lower() == "client"
        or str(i.get("decision_type") or i.get("decision") or "").lower() in ("defer", "hold", "no_action")
    )
    override_val = f"{client_led} / {total_dec}" if total_dec > 0 else "0 / 0"
    override_status = "green" if client_led == 0 else ("amber" if client_led == 1 else "red")

    beta_res = get_portfolio_beta(client_id)
    beta_val = beta_res.get("portfolio_beta") if not beta_res.get("error") else None
    if beta_val is None:
        vol_val, vol_status = "Unknown", "amber"
    elif beta_val <= 0.75:
        vol_val, vol_status = "Low", "green"
    elif beta_val <= 1.0:
        vol_val, vol_status = "Moderate", "amber"
    else:
        vol_val, vol_status = "High", "red"

    neg_count = sum(1 for i in all_interactions[:2] if str(i.get("sentiment") or "").lower() in negative)
    if neg_count >= 2:
        beh_val, beh_status = "High", "red"
    elif neg_count == 1:
        beh_val, beh_status = "Medium", "amber"
    else:
        beh_val, beh_status = "Low", "green"

    last_sent = str(all_interactions[0].get("sentiment") or "Neutral") if all_interactions else "Neutral"
    sent_label = last_sent.replace("_", "-").title()
    if last_sent.lower() in negative:
        sent_status = "red"
    elif last_sent.lower() in ("cautious", "concerned"):
        sent_status = "amber"
    else:
        sent_status = "green"

    has_breach = any(f.get("policy_status") == "breach" and f.get("signal_type") in ("allocation_drift", "portfolio_underweight", "portfolio_overweight", "security_concentration") for f in signal_facts)
    has_warn = any(f.get("policy_status") == "warning" and f.get("signal_type") in ("allocation_drift", "portfolio_underweight", "portfolio_overweight", "security_concentration") for f in signal_facts)
    if has_breach:
        ips_val, ips_status = "Breach", "red"
    elif has_warn:
        ips_val, ips_status = "Warning", "amber"
    else:
        ips_val, ips_status = "Within Band", "green"

    risk_metrics = [
        {"label": "Override Score", "value": override_val, "status": override_status},
        {"label": "Volatility Reactivity", "value": vol_val, "status": vol_status},
        {"label": "Behavioral Risk", "value": beh_val, "status": beh_status},
        {"label": "Sentiment", "value": sent_label, "status": sent_status},
        {"label": "IPS Alignment", "value": ips_val, "status": ips_status},
    ]

    return {"signal_facts": signal_facts, "scenario_fires": scenario_fires, "risk_metrics": risk_metrics}


# Backward-compatible wrapper functions (distinct __name__ for ADK schemas)
fetch_all_clients_via_cs = find_client


def get_meeting_context(meeting_id: str, meeting_date: str) -> dict:
    """Alias for get_meeting: fetch a row from wealth_meeting_prep.meeting_context."""
    return get_meeting(meeting_id, meeting_date)


def yfinance_price(tickers: list[str]) -> dict:
    """Alias for get_benchmark_returns: fetch live YTD, 2024, 1Y, and 1D returns."""
    return get_benchmark_returns(tickers)


def finnhub_news_with_benchmarks(client_id: str) -> dict:
    """Alias for get_approved_commentary: fetch Finnhub news with benchmark fallback."""
    return get_approved_commentary(client_id)
