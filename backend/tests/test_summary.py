"""Shared evidence summary (app/engine/summary.py): one wording for the Live Desk and the report."""

import json

from fastapi.testclient import TestClient

from app import config
from app.engine.summary import summarize
from app.schemas import Investigation

FIXTURE = json.loads(config.FIXTURE_PATH.read_text(encoding="utf-8"))


def _inv(mutate=None) -> Investigation:
    raw = json.loads(json.dumps(FIXTURE))
    if mutate:
        mutate(raw)
    return Investigation.model_validate(raw)


def test_fixture_sentence_and_per_witness_counts():
    inv = _inv()
    s = summarize(inv.findings, inv.claims, unavailable=set())
    assert s["sentence"] == ("Recorded rainfall does not support the claimed flood. "
                             "Frames match an archive clip verified with a different time and location. "
                             "4 other checks gave no clear answer.")
    assert s["counts"] == {"contradiction": 2, "consistent": 0, "other": 4, "unavailable": 0}


def _metadata_consistent_on_time(raw):
    """Metadata agrees with the time claim only; weather still contradicts the flood."""
    for f in raw["findings"]:
        if f["id"] == "f_metadata_1":
            f["status"], f["claim_ids"] = "consistent", ["c3"]
    raw["assessment"]["counts"].update({"consistent": 1, "inconclusive": 1})


def test_consistent_witness_says_what_it_does_not_confirm():
    inv = _inv(_metadata_consistent_on_time)
    s = summarize(inv.findings, inv.claims, unavailable={"prior_appearance"})
    assert s["sentence"] == ("Recorded rainfall does not support the claimed flood. "
                             "The capture time matches the stated time, which does not confirm the location or the event. "
                             "3 other checks gave no clear answer. "
                             "Prior appearance is not available in this build.")
    assert s["counts"] == {"contradiction": 1, "consistent": 1, "other": 3, "unavailable": 1}
    assert {"witness": "prior_appearance", "status": "unavailable"} in s["witnesses"]


def test_nothing_decisive_says_so():
    def all_inconclusive(raw):
        for f in raw["findings"]:
            if f["status"] == "contradiction":
                f["status"] = "inconclusive"
        raw["assessment"].update(headline="insufficient_evidence", human_review="standard",
                                 families={"context": "inconclusive", "signal": "inconclusive", "pixels": "inconclusive"})
        raw["assessment"]["counts"].update({"contradiction": 0, "inconclusive": 4})
    inv = _inv(all_inconclusive)
    s = summarize(inv.findings, inv.claims, unavailable=set())
    assert s["sentence"] == "None of the checks could confirm or contradict the caption with this media."


def test_no_forbidden_words_in_any_template():
    import re
    inv = _inv(_metadata_consistent_on_time)
    for unav in (set(), {"prior_appearance"}):
        text = summarize(inv.findings, inv.claims, unavailable=unav)["sentence"]
        assert not re.search(config.FORBIDDEN_WORDS_RE, text, re.IGNORECASE)


def test_summary_endpoint_matches_the_function(tmp_path, monkeypatch):
    from app.main import app
    from app.storage import RunStore

    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    inv = _inv()
    (tmp_path / inv.id).mkdir()
    RunStore(inv.id, root=tmp_path).save_investigation(inv)
    r = TestClient(app).get(f"/api/investigations/{inv.id}/summary")
    assert r.status_code == 200
    assert r.json() == summarize(inv.findings, inv.claims)
    assert TestClient(app).get("/api/investigations/inv_20200101_000000_zzzz/summary").status_code == 404


def test_live_desk_no_longer_builds_its_own_sentence():
    js = (config.LIVE_STATIC_DIR / "live.js").read_text(encoding="utf-8")
    for gone in ("Recorded rainfall does not support", "other check", "function plainSentence"):
        assert gone not in js, gone
    assert "d.summary" in js and "/summary" in js


# --- the plain authenticity answer ----------------------------------------------------------

def _with_metadata(details, title):
    def mutate(raw):
        for f in raw["findings"]:
            if f["id"] == "f_metadata_1":
                f["details"] = details
                f["title"] = title
    return mutate


def test_authenticity_ai_marker_beats_everything():
    inv = _inv(_with_metadata({"ai_markers": [{"label": "Software = DALL-E 3", "generator": "DALL-E"}],
                               "generators": ["DALL-E"]}, "AI-generation marker found: Software = DALL-E 3"))
    au = summarize(inv.findings, inv.claims, unavailable=set())["ai_check"]
    assert au == {"question": "Is this media AI-generated or manipulated?", "kind": "ai_marker",
                  "answer": "AI-generation marker found (DALL-E)", "note": au["note"]}


def test_authenticity_editing_traces():
    inv = _inv(_with_metadata({"software_tags": ["CapCut"]}, "Editing software tag present"))
    au = summarize(inv.findings, inv.claims, unavailable=set())["ai_check"]
    assert (au["kind"], au["answer"]) == ("editing", "Editing traces found (CapCut)")


def test_authenticity_none_and_never_forbidden_words():
    import re
    inv = _inv()
    au = summarize(inv.findings, inv.claims, unavailable=set())["ai_check"]
    assert (au["kind"], au["answer"]) == ("none", "No manipulation indicators found")
    assert "not proof" in au["note"]
    assert au["question"] in config.FORBIDDEN_WORDS_ALLOWED_PHRASES          # the one allowed question
    for text in (au["answer"], au["note"]):
        assert not config.forbidden_word(text)


def test_only_the_exact_question_is_exempt():
    assert config.forbidden_word("Is this media AI-generated or manipulated? AI-generation marker found (DALL-E)") is None
    assert config.forbidden_word("This media is AI-generated") is not None
    assert config.forbidden_word("authentic footage") is not None


def test_ai_marker_gets_its_own_sentence_not_a_capture_time_one():
    def mutate(raw):
        for f in raw["findings"]:
            if f["id"] == "f_metadata_1":
                f.update(status="contradiction", reliability="medium", claim_ids=[],
                         title="AI-generation marker found: Software = Adobe Firefly 2.1",
                         details={"ai_markers": [{"label": "Software = Adobe Firefly 2.1", "generator": "Adobe Firefly"}],
                                  "generators": ["Adobe Firefly"]})
        raw["assessment"]["counts"].update({"contradiction": 3, "inconclusive": 1})
    inv = _inv(mutate)
    s = summarize(inv.findings, inv.claims, unavailable=set())
    assert "The file's metadata carries an AI-generation marker (Adobe Firefly)." in s["sentence"]
    assert "capture time" not in s["sentence"]
    assert s["ai_check"]["answer"] == "AI-generation marker found (Adobe Firefly)"



def test_caption_less_run_says_only_the_file_was_examined():
    def no_claims(raw):
        raw["caption"], raw["claims"] = "", []
        for f in raw["findings"]:
            f["claim_ids"] = []
        raw["graph"]["edges"] = [e for e in raw["graph"]["edges"] if not e["target"].startswith("c")]
        raw["graph"]["nodes"] = [n for n in raw["graph"]["nodes"] if n["type"] != "claim"]
    inv = _inv(no_claims)
    s = summarize(inv.findings, inv.claims, unavailable=set())
    assert s["caption_less"] is True
    assert s["sentence"].startswith("No caption to check, so only the file itself was examined.")
    assert s["ai_check"]["answer"] == "No manipulation indicators found"
