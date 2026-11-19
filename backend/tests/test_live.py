"""Live Desk engine (§14): async runs, real-event SSE stream, caption re-runs, live feed."""

import io
import json
import os
import stat
import threading
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, pipeline, runner
from app.live import feed
from app.main import app
from app.schemas import Claim, Finding, Investigation
from app.storage import RunStore
from app.witnesses.base import Witness

client = TestClient(app)
pytestmark = pytest.mark.usefixtures("offline")   # no live weather calls

CASE_A = "Massive flooding in Chennai yesterday evening, roads completely underwater"


@pytest.fixture(autouse=True)
def runs_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (40, 90, 160)).save(buf, format="PNG")
    return buf.getvalue()


def _post(caption=CASE_A, asynchronous=False):
    url = "/api/investigations" + ("?async=true" if asynchronous else "")
    return client.post(url, data={"caption": caption, "posted_at": "2026-11-19"},
                       files=[("files", ("photo.png", _png(), "image/png"))])


def read_sse(url, headers=None):
    """[(id, event, data)] until the server closes the stream."""
    out, msg = [], {}
    with client.stream("GET", url, headers=headers or {}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if not line:
                if "event" in msg:
                    out.append((int(msg["id"]) if "id" in msg else None, msg["event"], json.loads(msg["data"])))
                msg = {}
            elif not line.startswith((":", "retry:")):
                key, _, value = line.partition(": ")
                msg[key] = value
    return out


class SlowLipsync(Witness):
    """Takes 1 s, so the stream can show it finishing last."""
    name = "lipsync"
    label = "Lip-sync"

    def applicable(self, ctx): return True

    def run(self, ctx):
        time.sleep(1.0)
        return self.placeholder()


@pytest.fixture
def slow_lipsync(monkeypatch):
    original = runner.all_witnesses
    patched = lambda: [SlowLipsync() if w.name == "lipsync" else w for w in original()]  # noqa: E731
    monkeypatch.setattr(runner, "all_witnesses", patched)
    monkeypatch.setattr(pipeline, "all_witnesses", patched)


# --- 1 + 2: async start and the real event stream ---------------------------------------

def test_async_returns_immediately_then_streams_real_events(slow_lipsync):
    t0 = time.monotonic()
    r = _post(asynchronous=True)
    assert r.status_code == 202 and time.monotonic() - t0 < 0.8           # the 1 s witness is still running
    body = r.json()
    inv_id = body["id"]
    assert body["status"] == "running" and body["events"] == f"/api/investigations/{inv_id}/events"
    assert client.get(f"/api/investigations/{inv_id}").status_code == 202  # still running

    events = read_sse(body["events"])
    names = [e for _, e, _ in events]
    assert [i for i, _, _ in events] == list(range(len(events)))            # ids are line numbers
    assert names[0] == "file_received" and names[1] == "claims_extracted"
    assert names[-3:] == ["assessment_computed", "graph_ready", "done"]
    assert names.count("witness_started") == 7 and names.count("witness_completed") == 7

    by = {e: d for _, e, d in events}
    assert [c["source"]["text"] for c in by["claims_extracted"]["data"]["claims"]] == \
        ["flooding", "Chennai", "yesterday evening"]
    started_at = {d["data"]["witness"]: i for i, e, d in events if e == "witness_started"}
    completed = [(i, d["data"]) for i, e, d in events if e == "witness_completed"]
    for i, data in completed:
        assert started_at[data["witness"]] < i and data["findings"][0]["witness"] == data["witness"]
    assert completed[-1][1]["witness"] == "lipsync"                         # real order: the slow one last

    inv = Investigation.model_validate(client.get(f"/api/investigations/{inv_id}").json())
    assert by["done"]["data"]["id"] == inv_id and by["done"]["data"]["headline"] == inv.assessment.headline
    assert by["assessment_computed"]["data"]["assessment"] == inv.assessment.model_dump(mode="json")
    assert by["graph_ready"]["data"]["graph"] == inv.graph.model_dump(mode="json")
    streamed = {f["id"]: f["status"] for _, d in completed for f in d["findings"]}
    assert streamed == {f.id: f.status for f in inv.findings}


def test_finished_run_replays_and_resumes_after_last_event_id():
    inv_id = _post().json()["id"]
    events = read_sse(f"/api/investigations/{inv_id}/events")
    assert events[-1][1] == "done"
    resumed = read_sse(f"/api/investigations/{inv_id}/events", headers={"Last-Event-ID": "3"})
    assert [i for i, _, _ in resumed] == list(range(4, len(events)))


def test_run_saved_before_live_engine_still_finishes():
    inv_id = _post().json()["id"]
    store = RunStore(inv_id)
    old = [line for line in store.history_path.read_text(encoding="utf-8").splitlines()
           if json.loads(line)["event"] not in ("graph_ready", "done")]
    store.history_path.write_text("\n".join(old) + "\n", encoding="utf-8")
    events = read_sse(f"/api/investigations/{inv_id}/events")
    assert events[-1][1] == "done" and events[-1][2]["data"]["id"] == inv_id


def test_events_for_unknown_run_is_404():
    assert client.get("/api/investigations/inv_20200101_000000_zzzz/events").status_code == 404


def test_history_payloads_stay_out_of_the_contract():
    inv = _post().json()
    assert all(set(h) == {"ts", "event", "detail"} for h in inv["history"])
    raw = RunStore(inv["id"]).events()
    assert any("data" in e for e in raw)                                   # but the log keeps them


# --- 3: caption re-run ---------------------------------------------------------------------

def test_rerun_reuses_files_and_caption_independent_findings():
    parent = _post().json()
    r = client.post(f"/api/investigations/{parent['id']}/rerun",
                    json={"caption": "Heavy rain in Pune yesterday", "posted_at": "2026-11-19"})
    assert r.status_code == 200, r.text
    child = Investigation.model_validate(r.json())
    assert child.parent_id == parent["id"] and child.id != parent["id"]
    assert [i.sha256 for i in child.inputs] == [i["sha256"] for i in parent["inputs"]]
    copied = RunStore(child.id).run_dir / child.inputs[0].stored_path
    assert copied.is_file() and not os.access(copied, os.W_OK)

    assert [c.value for c in child.claims if c.type == "location"] == ["Pune"]
    p_ela = next(f for f in parent["findings"] if f["witness"] == "ela")
    c_ela = next(f for f in child.findings if f.witness == "ela")
    assert c_ela.details == p_ela["details"] and c_ela.title == p_ela["title"]
    assert any("Re-used unchanged from investigation" in lim for lim in c_ela.limitations)
    assert (RunStore(child.id).run_dir / c_ela.details["heatmap"]).is_file()      # artifact came along
    weather = next(f for f in child.findings if f.witness == "weather")
    assert set(weather.claim_ids) <= {c.id for c in child.claims}                 # re-run on the new claims

    details = [h.detail for h in child.history]
    assert any("copied read-only from" in d and "hash verified" in d for d in details)
    assert any(d.startswith("ela:") and "re-used from" in d for d in details)
    assert not any(h.event == "witness_started" and h.detail == "ela" for h in child.history)
    rerun = {h.detail for h in child.history if h.event == "witness_started"}
    assert rerun == {"metadata", "weather", "prior_appearance", "enf"}     # ENF depends on the claimed country
    assert set(config.RERUN_REUSED) == {"ela", "lipsync", "synthetic_signals"}
    assert [f.witness for f in child.findings] == [f["witness"] for f in parent["findings"]]   # display order
    rows = {s["id"]: s for s in client.get("/api/investigations").json()}
    assert rows[child.id]["parent_id"] == parent["id"]


def test_rerun_async_streams_with_parent_id():
    parent_id = _post().json()["id"]
    r = client.post(f"/api/investigations/{parent_id}/rerun?async=true", json={"caption": "Rain in Chennai today"})
    assert r.status_code == 202 and r.json()["parent_id"] == parent_id
    events = read_sse(r.json()["events"])
    assert events[-1][1] == "done" and events[-1][2]["data"]["parent_id"] == parent_id


def test_rerun_errors():
    assert client.post("/api/investigations/inv_20200101_000000_zzzz/rerun",
                       json={"caption": "x"}).status_code == 404
    parent_id = _post().json()["id"]
    assert client.post(f"/api/investigations/{parent_id}/rerun", json={"caption": "  "}).status_code == 422

    running = RunStore.create()
    running.log("file_received", "x")
    r = client.post(f"/api/investigations/{running.inv_id}/rerun", json={"caption": "Rain in Pune"})
    assert r.status_code == 409 and "running" in r.json()["detail"]

    original = RunStore(parent_id).run_dir / "originals" / "in_1_photo.png"
    os.chmod(original, stat.S_IWRITE | stat.S_IREAD)
    original.write_bytes(b"tampered")
    r = client.post(f"/api/investigations/{parent_id}/rerun", json={"caption": "Rain in Pune"})
    assert r.status_code == 409 and "SHA-256" in r.json()["detail"]


def test_remap_claim_ids():
    def claim(cid, value, lat):
        return Claim(id=cid, type="location", value=value, normalized={"lat": lat, "lon": 80.0, "country": "IN"},
                     source={"field": "caption", "start": 0, "end": len(value), "text": value}, extracted_by="rules")
    f = Finding(id="f_enf_1", witness="enf", family="signal", claim_ids=["c2"], status="consistent",
                reliability="medium", title="t", summary="s", alternatives=["a"], limitations=["l"])
    kept = pipeline.remap_claim_ids(f.model_copy(deep=True), [claim("c2", "Chennai", 13.0)], [claim("c1", "Chennai", 13.0)])
    assert kept.claim_ids == ["c1"]
    dropped = pipeline.remap_claim_ids(f.model_copy(deep=True), [claim("c2", "Chennai", 13.0)], [claim("c1", "Pune", 18.5)])
    assert dropped.claim_ids == [] and any("no longer makes" in lim for lim in dropped.limitations)


# --- 4: live feed -------------------------------------------------------------------------

def test_feed_generator_receives_published_messages():
    g = feed.stream(limit=2)
    assert next(g).startswith("retry:")                  # subscribed now
    feed.publish("investigation_started", {"id": "inv_a"})
    feed.publish("investigation_done", {"id": "inv_a", "headline": "insufficient_evidence"})
    msgs = list(g)
    assert "event: investigation_started" in msgs[0] and '"id": "inv_a"' in msgs[0]
    assert "event: investigation_done" in msgs[1]
    assert feed.subscriber_count() == 0                  # unsubscribed when the stream ends


def test_feed_announces_new_investigations_over_http():
    got = []
    t = threading.Thread(target=lambda: got.extend(read_sse("/api/live/feed?limit=2")))
    t.start()
    deadline = time.monotonic() + 5
    while feed.subscriber_count() == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    inv_id = _post().json()["id"]
    t.join(timeout=10)
    assert [e for _, e, _ in got] == ["investigation_started", "investigation_done"]
    started, done = got[0][2], got[1][2]
    assert started["id"] == done["id"] == inv_id
    assert started["caption"] == CASE_A and started["inputs"] == ["photo.png"] and started["parent_id"] is None
    assert done["headline"] and done["human_review"] in ("priority", "standard")
