import pytest
import asyncio
import subprocess
import json
import re
from search_service import search_articles, clean_term, highlight_title

@pytest.mark.asyncio
async def test_empty_query():
    res = await search_articles("")
    assert res["query"] == ""
    assert res["total"] == 0
    assert res["results"] == []

    res2 = await search_articles("   ")
    assert res2["query"] == "   "
    assert res2["total"] == 0
    assert res2["results"] == []

@pytest.mark.asyncio
async def test_invalid_arguments():
    with pytest.raises(ValueError):
        await search_articles("test", limit=-1)
    with pytest.raises(ValueError):
        await search_articles("test", limit="10")
    with pytest.raises(ValueError):
        await search_articles("test", limit=True)
    with pytest.raises(ValueError):
        await search_articles("test", offset=-5)
    with pytest.raises(ValueError):
        await search_articles("test", offset="0")
    with pytest.raises(ValueError):
        await search_articles("test", offset=False)
    with pytest.raises(ValueError):
        await search_articles("test", status="invalid")
    with pytest.raises(ValueError):
        await search_articles("test", status=123)

@pytest.mark.asyncio
async def test_matching_and_ranking():
    # 'quokka' matches 3 articles
    res = await search_articles("quokka")
    assert res["total"] == 3
    results = res["results"]
    assert len(results) == 3
    
    # Check ranking: title (quokka-cluster-provisioning) > summary (fleet-preparation-window) > body (spare-hardware-notes)
    assert results[0]["slug"] == "quokka-cluster-provisioning"
    assert results[1]["slug"] == "fleet-preparation-window"
    assert results[2]["slug"] == "spare-hardware-notes"
    
    # Scores must be strictly decreasing
    assert results[0]["score"] > results[1]["score"]
    assert results[1]["score"] > results[2]["score"]

@pytest.mark.asyncio
async def test_morphological_matching():
    # 'policies' must match 'policy' in release-freeze-policy and ledger-archive-policy
    res = await search_articles("policies")
    slugs = {item["slug"] for item in res["results"]}
    assert "release-freeze-policy" in slugs
    assert "ledger-archive-policy" in slugs

@pytest.mark.asyncio
async def test_deterministic_tie_break():
    # 'ledger' has many matches with same scores, should be sorted by slug ascending
    res = await search_articles("ledger", status="draft")
    slugs = [item["slug"] for item in res["results"]]
    assert slugs == sorted(slugs)

@pytest.mark.asyncio
async def test_filters():
    # 'ledger' with status='published' and tag='database'
    res = await search_articles("ledger", status="published", tag="database")
    assert res["total"] == 6
    for item in res["results"]:
        assert item["status"] == "published"
        assert "database" in item["tags"]

@pytest.mark.asyncio
async def test_pagination():
    res = await search_articles("ledger", status="published", tag="database", limit=2, offset=2)
    assert res["total"] == 6
    assert len(res["results"]) == 2
    assert res["results"][0]["rank"] == 3
    assert res["results"][1]["rank"] == 4

    # Limit 0
    res_zero = await search_articles("ledger", limit=0)
    assert res_zero["total"] > 0
    assert res_zero["results"] == []

    # Offset beyond last match
    res_beyond = await search_articles("quokka", offset=10)
    assert res_beyond["total"] == 3
    assert res_beyond["results"] == []

def test_clean_term():
    assert clean_term("policy") == "policy"
    assert clean_term('"payments"') == "payments"
    assert clean_term("-ops") == "ops"
    assert clean_term("123") == "123"
    assert clean_term("#tag!") == "tag"
    assert clean_term("!") == ""

def test_highlighting():
    # Title: "Canary rollout guide for the payments API"
    # Query: "canary payments"
    assert highlight_title("Canary rollout guide for the payments API", ["canary", "payments"]) == "<b>Canary</b> rollout guide for the <b>payments</b> API"
    
    # Title: "Canary rollout guide for the payments API"
    # Query: "can" -> no match (not whole-word)
    assert highlight_title("Canary rollout guide for the payments API", ["can"]) == "Canary rollout guide for the payments API"
    
    # Title: "Canary rollout guide for the payments API"
    # Query: "api" -> match case-insensitive and preserve original
    assert highlight_title("Canary rollout guide for the payments API", ["api"]) == "Canary rollout guide for the payments <b>API</b>"

def test_cli():
    # Valid call
    cmd = ["python3", "search_cli.py", "--query", "quokka"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0
    assert proc.stderr == ""
    data = json.loads(proc.stdout)
    assert data["query"] == "quokka"
    assert data["total"] == 3

    # Invalid call: missing query
    cmd_err = ["python3", "search_cli.py"]
    proc_err = subprocess.run(cmd_err, capture_output=True, text=True)
    assert proc_err.returncode == 2
    assert proc_err.stdout == ""
    assert "error" in proc_err.stderr

    # Invalid call: negative limit
    cmd_err2 = ["python3", "search_cli.py", "--query", "test", "--limit", "-1"]
    proc_err2 = subprocess.run(cmd_err2, capture_output=True, text=True)
    assert proc_err2.returncode == 2
    assert proc_err2.stdout == ""
    assert "error" in proc_err2.stderr
