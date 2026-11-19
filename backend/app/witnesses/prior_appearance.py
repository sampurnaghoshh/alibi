"""
Prior appearance witness (CLAUDE.md §6.4). Owner: Nooman (handed over from Adithi).   Family: context.
Max reliability: high.

Is this footage already in our archive of verified clips, and does its verified date/place fit the caption?

  1. load_archive():  config.ARCHIVE_JSON, built by scripts/build_archive.py (Sampurna).
  2. Sample the upload with app/media.sample_frames at the ARCHIVE's sample_fps and default width, and hash
     each frame with media.phash_hex, so upload and archive hashes come from identical code paths.
  3. Frame match: media.hamming <= PRIOR_MAX_HAMMING (8). Clip match: >= PRIOR_MIN_MATCHED_FRAMES (3) frames or
     >= PRIOR_MIN_MATCHED_FRACTION (30%) of sampled frames match the SAME archive clip.
     Short inputs (photos, clips with < 3 sampled frames) let one frame decide, so that frame must be
     near-identical: Hamming <= PRIOR_SHORT_MAX_HAMMING (4).
  4. Compare the best-matching clip's verified date / place with the caption's time / location claims:
       date:     verified_date outside the claim window +/- PRIOR_DATE_TOLERANCE_DAYS -> disagrees
       location: verified_lat/lon > PRIOR_LOCATION_TOLERANCE_KM from the claim -> disagrees; without archive
                 coordinates, a place-name match agrees and a mismatch is "not compared" (never a contradiction).
     any disagreement -> contradiction (high); agreement -> consistent (high);
     match but nothing to compare -> inconclusive (medium); no match -> inconclusive (low);
     archive missing / empty / unreadable -> inconclusive (low). Never a crash.
  5. Guards (other takes of the same scene also pass the frame threshold):
       ambiguity:  the upload also matches another archive clip with a different verified date/place
                   -> inconclusive (medium), all matches listed.
       near-copy:  high only if the matched frames' median Hamming <= PRIOR_HIGH_MAX_MEDIAN_HAMMING (1.5);
                   otherwise inconclusive (medium), match and rule outcome reported. Measured: true copies incl.
                   WhatsApp-style re-encodes 0-1.0, other takes 2-8.

Run alone (from backend/):
    python -m app.witnesses.prior_appearance --file ../demo/case_a/clip.mp4 --caption "Massive flooding in Chennai yesterday" --posted-at 2026-11-19
"""

from __future__ import annotations

import json
import math
import statistics
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from app import config
from app.media import frame_at, hamming, load_image, media_kind, phash_hex, sample_frames
from app.schemas import Claim, Finding
from app.witnesses.base import InputRef, InvestigationContext, Witness, run_cli

ALTERNATIVES = [
    "The same scene may have been filmed again from a similar position",
    "A widely shared stock or news clip can appear in many unrelated posts",
    "Generic scenes (flood water, traffic, sky) can look alike to a perceptual hash by coincidence",
]


def archive_limitation(n: int) -> str:
    return f"Archive is small ({n} clips). In production this connects to reverse-image search and fact-checker databases."


# ---------------------------------------------------------------------------
# Archive + matching
# ---------------------------------------------------------------------------

def load_archive(path: Optional[Path] = None) -> dict:
    """The archive dict. Raises FileNotFoundError / ValueError when missing or malformed."""
    path = Path(path or config.ARCHIVE_JSON)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("clips"), list):
        raise ValueError(f"{path.name} has no 'clips' list")
    return data


def nearest_frames(upload_hashes: list[str], clip_hashes: list[str]) -> list[tuple[int, int]]:
    """For each upload frame: (smallest Hamming distance to this archive clip, index of that archive frame)."""
    return [min((hamming(h, c), j) for j, c in enumerate(clip_hashes)) for h in upload_hashes]


def match_clip(upload_hashes: list[str], clip_hashes: list[str], max_hamming: int) -> list[Optional[int]]:
    """For each upload frame: its smallest Hamming distance to any frame of this archive clip, if <= max_hamming."""
    return [d if d <= max_hamming else None for d, _ in nearest_frames(upload_hashes, clip_hashes)]


def clip_matches(n_matched: int, n_frames: int) -> bool:
    """§6.4: >= PRIOR_MIN_MATCHED_FRAMES matched frames, or >= PRIOR_MIN_MATCHED_FRACTION of the sampled frames."""
    return n_frames > 0 and n_matched > 0 and (
        n_matched >= config.PRIOR_MIN_MATCHED_FRAMES or n_matched / n_frames >= config.PRIOR_MIN_MATCHED_FRACTION)


def threshold_for(n_frames: int) -> int:
    """Short inputs (photos, < PRIOR_MIN_MATCHED_FRAMES sampled frames): one frame decides, so be stricter."""
    return config.PRIOR_SHORT_MAX_HAMMING if n_frames < config.PRIOR_MIN_MATCHED_FRAMES else config.PRIOR_MAX_HAMMING


def best_matches(upload_hashes: list[str], clips: list[dict]) -> list[dict]:
    """Per archive clip: matched frame indices and distances, sorted best first
    (most matched frames, then lowest median distance). Includes non-matching clips (for 'closest')."""
    thr = threshold_for(len(upload_hashes))
    rows = []
    for clip in clips:
        hashes = clip.get("frame_hashes") or []
        if not hashes:
            continue
        nearest = nearest_frames(upload_hashes, hashes)
        pairs = [(i, j, d) for i, (d, j) in enumerate(nearest) if d <= thr]   # (upload idx, archive idx, hamming)
        dists = [d for _, _, d in pairs]
        closest = min(d for d, _ in nearest) if nearest else None
        rows.append({
            "clip": clip,
            "matched_idx": [i for i, _, _ in pairs],
            "pairs": pairs,
            "dists": dists,
            "closest": closest,
            "is_match": clip_matches(len(dists), len(upload_hashes)),
        })
    rows.sort(key=lambda r: (-len(r["dists"]), statistics.median(r["dists"]) if r["dists"] else 99,
                             r["closest"] if r["closest"] is not None else 99))
    return rows


def spread_pairs(pairs: list[tuple[int, int, int]], k: int) -> list[tuple[int, int, int]]:
    """Up to k matched pairs spread across the upload: split the time-ordered matches into k chunks and
    take the lowest-Hamming pair of each chunk."""
    pairs = sorted(pairs)
    if len(pairs) <= k:
        return pairs
    size = len(pairs) / k
    chunks = [pairs[round(i * size): round((i + 1) * size)] for i in range(k)]
    return [min(c, key=lambda p: (p[2], p[0])) for c in chunks if c]


def archive_source(clip: dict) -> Optional[Path]:
    """The archived clip's file: manifest paths are relative to the manifest's folder (data/archive/).
    Looked up next to archive.json first, then next to the manifest. None if not found."""
    rel = clip.get("file")
    if not rel:
        return None
    for base in (Path(config.ARCHIVE_JSON).parent, Path(config.ARCHIVE_MANIFEST).parent):
        path = Path(rel) if Path(rel).is_absolute() else base / rel
        if path.is_file():
            return path
    return None


def _save_jpg(img, path: Path) -> None:
    """Small JPG for the Live Desk: <= PRIOR_TIME_MACHINE_WIDTH px wide, quality PRIOR_TIME_MACHINE_JPEG_QUALITY."""
    w = config.PRIOR_TIME_MACHINE_WIDTH
    if img.width > w:
        img = img.resize((w, max(1, round(img.height * w / img.width))))
    img.convert("RGB").save(path, "JPEG", quality=config.PRIOR_TIME_MACHINE_JPEG_QUALITY)


def matched_spans(times: list[float], idx: list[int], step: float, duration: Optional[float]) -> list[tuple[float, float]]:
    """Matched sample times -> merged [start, end] ranges; each sample covers one sampling step."""
    spans: list[list[float]] = []
    for i in sorted(idx):
        s = times[i]
        e = s + step if duration is None else min(s + step, duration)
        if spans and s <= spans[-1][1] + 1e-6:
            spans[-1][1] = max(spans[-1][1], e)
        else:
            spans.append([s, e])
    return [(round(s, 3), round(max(e, s), 3)) for s, e in spans]


# ---------------------------------------------------------------------------
# Comparing the verified context with the caption's claims
# ---------------------------------------------------------------------------

def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine), km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def compare_date(verified: str, time_claims: list[Claim]) -> dict:
    """agree / disagree / not_compared, with the numbers. Agrees if inside ANY claimed window +/- tolerance."""
    usable = [c for c in time_claims if c.normalized and c.normalized.get("start") and c.normalized.get("end")]
    if not usable or not verified:
        return {"result": "not_compared", "reason": "no time claim" if not usable else "no verified date"}
    v = date.fromisoformat(verified)
    tol = timedelta(days=config.PRIOR_DATE_TOLERANCE_DAYS)
    best = None
    for c in usable:
        start = _as_date(c.normalized["start"])
        end = _as_date(c.normalized["end"])
        gap = 0 if start - tol <= v <= end + tol else min(abs((v - start).days), abs((v - end).days))
        row = {"claim_id": c.id, "claim_text": c.source.text, "window": [start.isoformat(), end.isoformat()], "days_apart": gap}
        if best is None or gap < best["days_apart"]:
            best = row
    return {"result": "agree" if best["days_apart"] == 0 else "disagree", "verified_date": verified,
            "tolerance_days": config.PRIOR_DATE_TOLERANCE_DAYS, **best}


def _as_date(x: Any) -> date:
    """Claim window bound (ISO string or datetime) -> its calendar date in the claim's own timezone."""
    from datetime import datetime  # noqa: PLC0415

    return (x if isinstance(x, datetime) else datetime.fromisoformat(str(x))).date()


def compare_location(clip: dict, loc_claims: list[Claim]) -> dict:
    """Coordinates when both sides have them (agree within tolerance of ANY claimed place, else disagree).
    Without archive coordinates: a place-name match agrees; a mismatch is not compared (never a contradiction)."""
    if not loc_claims:
        return {"result": "not_compared", "reason": "no location claim"}
    vlat, vlon = clip.get("verified_lat"), clip.get("verified_lon")
    with_coords = [c for c in loc_claims if c.normalized and c.normalized.get("lat") is not None]
    if vlat is not None and vlon is not None and with_coords:
        best = min(({"claim_id": c.id, "claim_text": c.source.text,
                     "distance_km": round(_km(vlat, vlon, c.normalized["lat"], c.normalized["lon"]), 1)}
                    for c in with_coords), key=lambda r: r["distance_km"])
        return {"result": "agree" if best["distance_km"] <= config.PRIOR_LOCATION_TOLERANCE_KM else "disagree",
                "method": "coordinates", "verified_location": clip.get("verified_location"),
                "tolerance_km": config.PRIOR_LOCATION_TOLERANCE_KM, **best}
    place = str(clip.get("verified_location") or "").lower()
    for c in loc_claims:
        names = {c.value.lower(), c.source.text.lower()}
        if place and any(n and n in place for n in names):
            return {"result": "agree", "method": "place name", "verified_location": clip.get("verified_location"),
                    "claim_id": c.id, "claim_text": c.source.text}
    return {"result": "not_compared", "method": "place name", "verified_location": clip.get("verified_location"),
            "reason": "archive entry has no coordinates and the place names differ (aliases possible)"}


def same_context(a: dict, b: dict) -> bool:
    """Do two archive entries describe the same verified context (date within PRIOR_DATE_TOLERANCE_DAYS and place
    within PRIOR_LOCATION_TOLERANCE_KM, or the same place name when coordinates are missing)?"""
    try:
        days = abs((date.fromisoformat(a.get("verified_date", "")) - date.fromisoformat(b.get("verified_date", ""))).days)
    except ValueError:
        days = 0 if a.get("verified_date") == b.get("verified_date") else 10**6
    if days > config.PRIOR_DATE_TOLERANCE_DAYS:
        return False
    if None not in (a.get("verified_lat"), a.get("verified_lon"), b.get("verified_lat"), b.get("verified_lon")):
        km = _km(a["verified_lat"], a["verified_lon"], b["verified_lat"], b["verified_lon"])
        return km <= config.PRIOR_LOCATION_TOLERANCE_KM
    return str(a.get("verified_location", "")).strip().lower() == str(b.get("verified_location", "")).strip().lower()


def match_row(r: dict) -> dict:
    c = r["clip"]
    return {"clip_id": c["clip_id"], "title": c.get("title"), "verified_date": c.get("verified_date"),
            "verified_location": c.get("verified_location"), "matched_frames": len(r["dists"]),
            "median_hamming": statistics.median(r["dists"]) if r["dists"] else None}


def _fmt_date(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%d %b %Y").lstrip("0")
    except (TypeError, ValueError):
        return str(iso)


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------

class PriorAppearanceWitness(Witness):
    name = "prior_appearance"
    label = "Prior appearance"
    implemented = True

    def applicable(self, ctx: InvestigationContext) -> bool:
        return bool(ctx.inputs_of("video") or ctx.inputs_of("image"))

    def run(self, ctx: InvestigationContext) -> list[Finding]:
        try:
            archive = load_archive()
        except Exception as exc:  # noqa: BLE001 - missing / unreadable archive is not a crash
            return [self._no_archive(f"Archive could not be read: {type(exc).__name__}: {exc}", 0)]
        clips = [c for c in archive["clips"] if c.get("frame_hashes")]
        if not clips:
            return [self._no_archive("The archive of verified clips is empty", len(archive["clips"]))]

        inputs = (ctx.inputs_of("video") + ctx.inputs_of("image"))[: config.PRIOR_MAX_INPUTS]
        findings = []
        for n, inp in enumerate(inputs, 1):
            try:
                findings.append(self._check(ctx, inp, n, len(inputs), archive, clips))
            except Exception as exc:  # noqa: BLE001 - principle 5: never crash an investigation
                findings.append(self.finding(
                    n=n, status="inconclusive", reliability="low",
                    title="Prior-appearance check could not be completed",
                    summary=f"The archive comparison for {inp.name} failed, so no match information is available.",
                    details={"file": inp.name, "error": f"{type(exc).__name__}: {exc}"},
                    alternatives=["No comparison was completed, so this finding says nothing about the media"],
                    limitations=[f"Analysis error: {type(exc).__name__}: {exc}", archive_limitation(len(clips))]))
        return findings

    # --- one input ----------------------------------------------------------------

    def _check(self, ctx: InvestigationContext, inp: InputRef, n: int, total: int, archive: dict,
               clips: list[dict]) -> Finding:
        fps = float(archive.get("sample_fps") or config.PRIOR_SAMPLE_FPS)
        frames = sample_frames(inp.path, fps, inp.mime)        # media.py defaults: same width as the archive
        times = [t for t, _ in frames]
        hashes = [phash_hex(img) for _, img in frames]
        if not hashes:
            raise RuntimeError("no frames could be decoded")
        n_frames, thr = len(hashes), threshold_for(len(hashes))
        rows = best_matches(hashes, clips)
        best = rows[0] if rows else None
        is_photo = inp.kind == "image"
        where = f" ({inp.name})" if total > 1 else ""

        limitations = [archive_limitation(len(clips)),
                       "Perceptual hashes survive re-encoding and resizing, but not heavy crops, mirroring or overlays"]
        if n_frames < config.PRIOR_MIN_MATCHED_FRAMES:
            limitations.append(f"{'A single image' if is_photo else 'A very short clip'} is matched on one frame, "
                               f"so a stricter threshold is used (Hamming <= {thr})")
        built_w, now_w = archive.get("frame_max_width"), config.FRAME_SAMPLE_MAX_WIDTH
        if built_w is not None and built_w != now_w:
            limitations.append(f"Archive was hashed at {built_w} px wide but uploads at {now_w} px; rebuild the archive")

        details: dict[str, Any] = {
            "file": inp.name,
            "archive_size": len(clips),
            "sampled_frames": n_frames,
            "sample_fps": fps,
            "hamming_threshold": thr,
            "match_rule": {"min_frames": config.PRIOR_MIN_MATCHED_FRAMES, "min_fraction": config.PRIOR_MIN_MATCHED_FRACTION},
        }
        if best is not None:
            details.update({
                "matched_frames": len(best["dists"]),
                "matched_fraction": round(len(best["dists"]) / n_frames, 3),
                "min_hamming": min(best["dists"]) if best["dists"] else best["closest"],
                "median_hamming": statistics.median(best["dists"]) if best["dists"] else None,
                "closest_clip_id": best["clip"]["clip_id"],
            })

        if best is None or not best["is_match"]:
            return self.finding(
                n=n, status="inconclusive", reliability="low", details=details,
                title=f"No prior appearance found in the archive{where}",
                summary=f"Not found in our archive of {len(clips)} verified clips. This does not mean the footage is new.",
                evidence=[{"kind": "input", "ref": inp.file_id, "label": inp.name}],
                alternatives=ALTERNATIVES + ["The footage may exist elsewhere, outside this small archive"],
                limitations=limitations,
            )

        clip = best["clip"]
        matching = [r for r in rows if r["is_match"]]
        others = [match_row(r) for r in matching[1:]]
        # Guard 1, ambiguity: the upload also matches another archive clip with a different verified date or place
        # (e.g. two takes of the same scene). Which verified record applies is then unclear -> no conclusion.
        conflicting = [r for r in matching[1:] if not same_context(clip, r["clip"])]
        details.update({
            "matched_clip": {k: clip.get(k) for k in ("clip_id", "title", "verified_date", "verified_location",
                                                      "verified_lat", "verified_lon", "verified_by", "source_note")},
            "matched_times_s": [times[i] for i in best["matched_idx"]],
            "other_matching_clips": others,
        })
        if not conflicting:   # an ambiguous match shows no single "verified" record (Live Desk Time Machine)
            details.update(self._time_machine(ctx, inp, total, clip, best, frames, limitations))
        date_cmp = compare_date(clip.get("verified_date", ""), ctx.claims_of("time"))
        loc_cmp = compare_location(clip, ctx.claims_of("location"))
        details["comparison"] = {"date": date_cmp, "location": loc_cmp}

        locations = [] if is_photo else [
            {"kind": "timestamp", "file_id": inp.file_id, "start_s": s, "end_s": e}
            for s, e in matched_spans(times, best["matched_idx"], 1.0 / fps, inp.duration_s)]
        evidence = [{"kind": "archive_match", "ref": clip["clip_id"],
                     "label": f"{clip.get('title', clip['clip_id'])} (verified {clip.get('verified_location')}, "
                              f"{clip.get('verified_date')})"},
                    {"kind": "input", "ref": inp.file_id, "label": inp.name}]
        place, when = clip.get("verified_location", "an unknown place"), _fmt_date(clip.get("verified_date", ""))
        match_txt = (f"{len(best['dists'])} of {n_frames} sampled frames match archive clip {clip['clip_id']} "
                     f"(Hamming {min(best['dists'])}-{max(best['dists'])}, threshold {thr})")
        compared = [c for c in (date_cmp, loc_cmp) if c["result"] != "not_compared"]
        claim_ids = sorted({c["claim_id"] for c in compared if c.get("claim_id")})

        # Guard 2, near-duplicate gate: a high-reliability conclusion needs a near-identical copy. Re-filmed takes of
        # the same scene also pass the frame threshold, but at a larger median distance.
        median_h = statistics.median(best["dists"])
        near_duplicate = median_h <= config.PRIOR_HIGH_MAX_MEDIAN_HAMMING
        would_be = ("contradiction" if any(c["result"] == "disagree" for c in compared)
                    else "consistent" if compared else "inconclusive")
        details["guards"] = {
            "ambiguous": bool(conflicting),
            "conflicting_clip_ids": [r["clip"]["clip_id"] for r in conflicting],
            "matched_median_hamming": median_h,
            "high_max_median_hamming": config.PRIOR_HIGH_MAX_MEDIAN_HAMMING,
            "near_duplicate": near_duplicate,
            "rule_status": would_be,
        }
        if conflicting:
            all_rows = [match_row(r) for r in matching]
            listing = "; ".join(f"{m['clip_id']} ({m['verified_location']}, {_fmt_date(m['verified_date'] or '')}: "
                                f"{m['matched_frames']} frames, median Hamming {m['median_hamming']:g})" for m in all_rows)
            return self.finding(
                n=n, status="inconclusive", reliability="medium", claim_ids=claim_ids, details=details,
                evidence=[{"kind": "archive_match", "ref": m["clip_id"],
                           "label": f"{m['title'] or m['clip_id']} (verified {m['verified_location']}, {m['verified_date']})"}
                          for m in all_rows] + [{"kind": "input", "ref": inp.file_id, "label": inp.name}],
                locations=locations,
                title=f"Frames match {len(all_rows)} archive clips with different verified contexts{where}",
                summary=f"The upload matches several archived clips whose verified dates or places differ: {listing}. "
                        "It is not clear which verified record applies, so no conclusion is drawn.",
                alternatives=ALTERNATIVES + ["Several archived clips may show the same place filmed at different times"],
                limitations=limitations + ["Matches to several verified clips with different contexts are not "
                                           "resolved automatically"],
            )
        if compared and not near_duplicate:
            return self.finding(
                n=n, status="inconclusive", reliability="medium", claim_ids=claim_ids, details=details,
                evidence=evidence, locations=locations,
                title=f"Frames resemble a clip verified as recorded in {place} on {when}{where}",
                summary=f"{match_txt}, but the median distance ({median_h:g}) is above the near-copy limit "
                        f"({config.PRIOR_HIGH_MAX_MEDIAN_HAMMING}), so this may be a similar scene rather than the same "
                        f"footage. The comparison with the caption ({would_be}) is reported in details only.",
                alternatives=ALTERNATIVES,
                limitations=limitations + ["Only near-identical copies can support a high-reliability conclusion"],
            )

        if any(c["result"] == "disagree" for c in compared):
            diffs = []
            if date_cmp["result"] == "disagree":
                diffs.append(f"the caption's time (\"{date_cmp['claim_text']}\") is {date_cmp['days_apart']} days from "
                             f"the verified date")
            if loc_cmp["result"] == "disagree":
                diffs.append(f"the caption's place (\"{loc_cmp['claim_text']}\") is {loc_cmp['distance_km']:,.0f} km "
                             f"from the verified location")
            return self.finding(
                n=n, status="contradiction", reliability="high", claim_ids=claim_ids, details=details,
                evidence=evidence, locations=locations,
                title=f"Frames match a clip verified as recorded in {place} on {when}",
                summary=f"{match_txt}. The archive entry was verified by {clip.get('verified_by', 'the team')}; "
                        f"{' and '.join(diffs)}. This contradicts the stated context and requires human review.",
                alternatives=ALTERNATIVES + ["The archive entry's verified date or place could itself be wrong"],
                limitations=limitations,
            )
        if compared:
            agreed = " and ".join(k for k, c in (("date", date_cmp), ("place", loc_cmp)) if c["result"] == "agree")
            return self.finding(
                n=n, status="consistent", reliability="high", claim_ids=claim_ids, details=details,
                evidence=evidence, locations=locations,
                title=f"Frames match a verified clip whose {agreed} {'fit' if ' and ' in agreed else 'fits'} "
                      f"the caption{where}",
                summary=f"{match_txt}, verified as recorded in {place} on {when}; the caption's {agreed} "
                        f"{'is' if ' and ' not in agreed else 'are'} consistent with it.",
                alternatives=ALTERNATIVES,
                limitations=limitations,
            )
        return self.finding(
            n=n, status="inconclusive", reliability="medium", details=details, evidence=evidence, locations=locations,
            title=f"Frames match a clip verified as recorded in {place} on {when}",
            summary=f"{match_txt}. The caption has no time or place that can be compared with the verified record, "
                    "so the match is reported without a conclusion.",
            alternatives=ALTERNATIVES,
            limitations=limitations,
        )

    def _time_machine(self, ctx: InvestigationContext, inp: InputRef, total: int, clip: dict, best: dict,
                      frames: list, limitations: list[str]) -> dict:
        """Live Desk "Time Machine" fields (§14): details.frame_matches[] with upload/archive frame JPGs saved in
        artifacts/frames/, plus verified_date / verified_location / verified_by. Never raises: on a missing
        archive clip archive_frame is null; on any other problem frame_matches is empty. Both add a limitation."""
        out: dict[str, Any] = {k: clip.get(k) for k in ("verified_date", "verified_location", "verified_by")}
        out.update(matched_clip_id=clip["clip_id"], matched_clip_title=clip.get("title"), frame_matches=[])
        try:
            prefix = "" if total == 1 else f"{inp.file_id}_"
            times = clip.get("frame_times_s") or []
            src = archive_source(clip)
            if src is None:
                limitations.append(f"The archived source file for {clip['clip_id']} was not found, so archive "
                                   "frames are not shown")
            still = load_image(src) if src is not None and media_kind(src) == "image" else None
            failed = False
            for ui, ai, d in spread_pairs(best["pairs"], config.PRIOR_TIME_MACHINE_MAX_PAIRS):
                archive_s = times[ai] if ai < len(times) else round(ai / float(clip.get("sample_fps") or 1.0), 3)
                up_ref = f"frames/{prefix}up_{ui:03d}.jpg"
                _save_jpg(frames[ui][1], ctx.artifact_path(up_ref))
                ar_ref = None
                if src is not None:
                    try:
                        img = still if still is not None else frame_at(src, archive_s)[1]
                        ar_ref = f"frames/{prefix}ar_{ai:03d}.jpg"
                        _save_jpg(img, ctx.artifact_path(ar_ref))
                    except Exception:  # noqa: BLE001 - a frame we can't read is shown as missing, not a crash
                        ar_ref, failed = None, True
                out["frame_matches"].append({
                    "upload_s": frames[ui][0], "archive_s": archive_s, "hamming": d,
                    "upload_frame": ctx.artifact_ref(up_ref),
                    "archive_frame": ctx.artifact_ref(ar_ref) if ar_ref else None,
                })
            if failed:
                limitations.append(f"Some archive frames of {clip['clip_id']} could not be read, so they are not shown")
        except Exception as exc:  # noqa: BLE001 - the Time Machine is a display aid; never fail the finding
            out["frame_matches"] = []
            limitations.append(f"Matched frames could not be saved for display ({type(exc).__name__})")
        return out

    def _no_archive(self, why: str, n: int) -> Finding:
        return self.finding(
            status="inconclusive", reliability="low",
            title="Prior-appearance check not available",
            summary=f"{why}, so the upload could not be compared with verified clips.",
            details={"archive_path": str(config.ARCHIVE_JSON), "archive_size": n},
            alternatives=["No comparison was made, so this finding says nothing about whether the footage appeared before"],
            limitations=[why, archive_limitation(n)],
        )


if __name__ == "__main__":
    run_cli(PriorAppearanceWitness())
