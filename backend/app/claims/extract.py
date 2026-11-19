"""
Claim extraction (CLAUDE.md §6.1). Owner: Sampurna (taken over from Nooman).
Not a witness: it turns the caption into claims that every witness checks.

    extract_claims(caption, posted_at) -> list[Claim]

Rules path (default, fully offline, deterministic):
  locations   gazetteer.json (coordinates included), case-insensitive whole words, longest match first
  times       today / yesterday / last night / "2 days ago" / explicit dates (dateparser,
              RELATIVE_BASE = posted_at), with morning/afternoon/evening/night windows
  conditions  keyword map -> flood | heavy_rain | rain | dry | storm
  speakers    only obvious "X said" / "speech by X" patterns
LLM path (only when ALIBI_LLM_PROVIDER is anthropic/gemini and a key is set):
  asks for [{type, value, source_text}], keeps a claim ONLY if source_text is literally in the
  caption (principle 3), normalizes it with the same code, and adds it where the rules found
  nothing. Any LLM problem -> rules only.

Every claim's source span is computed from the caption itself, so caption[start:end] == text.

Run alone (from backend/):
    python -m app.claims.extract --caption "Massive flooding in Chennai yesterday evening" --posted-at 2026-11-19
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from functools import lru_cache
from typing import Any, Optional

from pydantic import ValidationError

from app import config
from app.claims import llm
from app.schemas import Claim

log = logging.getLogger("alibi.claims")

CLAIM_TYPES = {"location", "time", "event", "weather_condition", "speaker", "other"}


@dataclass
class Cand:
    """A claim candidate before overlap resolution and id assignment."""

    start: int
    end: int
    type: str
    value: str
    normalized: Optional[dict[str, Any]]
    extracted_by: str = "rules"

    def overlaps(self, other: "Cand") -> bool:
        return self.start < other.end and other.start < self.end


def _words(pattern: str) -> re.Pattern:
    """Whole-word, case-insensitive pattern."""
    return re.compile(rf"(?<!\w)(?:{pattern})(?!\w)", re.IGNORECASE)


# =============================================================================
# Locations
# =============================================================================

@lru_cache(maxsize=1)
def _gazetteer() -> tuple[list[tuple[str, re.Pattern, dict]], dict[str, dict]]:
    """(patterns longest-alias-first, {lowercase alias: place})."""
    places = json.loads(config.GAZETTEER_PATH.read_text(encoding="utf-8"))["places"]
    patterns, by_alias = [], {}
    for place in places:
        for alias in [place["name"], *place.get("aliases", [])]:
            body = r"[\s-]+".join(re.escape(w) for w in alias.split())  # "Navi Mumbai" ~ "Navi-Mumbai"
            patterns.append((alias, _words(body), place))
            by_alias[" ".join(alias.lower().split())] = place
    patterns.sort(key=lambda t: -len(t[0]))
    return patterns, by_alias


def _loc_normalized(place: dict) -> dict[str, Any]:
    return {"lat": place["lat"], "lon": place["lon"], "country": place["country"]}


def lookup_place(text: str) -> Optional[dict]:
    """Exact gazetteer lookup of a whole place name (any alias, any case)."""
    return _gazetteer()[1].get(" ".join(text.lower().split()))


def find_locations(caption: str) -> list[Cand]:
    out = []
    for _alias, pattern, place in _gazetteer()[0]:
        for m in pattern.finditer(caption):
            if place.get("strict") and not caption[m.start()].isupper():
                continue  # "puri" the bread, "Puri" the city
            out.append(Cand(m.start(), m.end(), "location", m.group(0), _loc_normalized(place)))
    return out


def geocode(name: str) -> Optional[dict[str, Any]]:
    """Open-Meteo geocoding for places not in the gazetteer. Cached on disk; None when offline."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:80]
    if not slug:
        return None
    path = config.GEOCODE_CACHE_DIR / f"{slug}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        try:
            import requests  # noqa: PLC0415

            r = requests.get(
                config.GEOCODE_URL,
                params={"name": name, "count": 1, "language": "en", "format": "json"},
                timeout=config.GEOCODE_TIMEOUT_S,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:  # noqa: BLE001 - offline is normal; just skip
            log.info("geocoding unavailable for %r: %s", name, exc)
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")   # empty results are cached too
    results = data.get("results") or []
    if not results:
        return None
    top = results[0]
    return {"lat": round(top["latitude"], 4), "lon": round(top["longitude"], 4), "country": top.get("country_code")}


# =============================================================================
# Weather / event conditions
# =============================================================================

_CONDITIONS: list[tuple[re.Pattern, str]] = [
    (_words(r"flash[\s-]+floods?|floods?|flooding|flooded|water[\s-]?logg(?:ed|ing)|under[\s-]?water|"
            r"inundat(?:ed|ion|ing)|submerged|deluge"), "flood"),
    (_words(r"(?:very[\s-]+)?heavy[\s-]+(?:rain(?:s|fall)?|showers|downpours?)|torrential[\s-]+rain(?:s|fall)?|"
            r"incessant[\s-]+rain(?:s|fall)?|extreme[\s-]+rainfall|downpours?|cloud[\s-]?bursts?"), "heavy_rain"),
    (_words(r"rain(?:s|ing|ed|y|fall)?|drizzl(?:e|ing)"), "rain"),
    (_words(r"thunder[\s-]?storms?|hail[\s-]?storms?|storms?|stormy|cyclones?|cyclonic|hurricanes?|typhoons?|"
            r"gales?|squalls?"), "storm"),
    (_words(r"droughts?|dry[\s-]+spell|dry[\s-]+weather|bone[\s-]+dry|parched|heat[\s-]?waves?"), "dry"),
]


def find_conditions(caption: str) -> list[Cand]:
    return [
        Cand(m.start(), m.end(), "weather_condition", m.group(0), {"condition": cond})
        for pattern, cond in _CONDITIONS
        for m in pattern.finditer(caption)
    ]


# =============================================================================
# Times
# =============================================================================

_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
          r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?![a-z])\.?")
_D = r"\d{1,2}(?:st|nd|rd|th)?(?!\d)"
_EXPLICIT = (rf"(?:{_D}\s+(?:of\s+)?{_MONTH}(?:,?\s+\d{{4}})?|{_MONTH}\s+{_D}(?:,?\s+\d{{4}})?|"
             r"\d{4}-\d{2}-\d{2}|\d{1,2}[/.]\d{1,2}[/.](?:\d{4}|\d{2}))")
_NUM = r"\d+|an?|one|two|three|four|five|six|seven|ten"
_REL = rf"(?:the\s+)?day\s+before\s+yesterday|yesterday|today|(?:{_NUM})\s+days?\s+ago"
_WEEKDAY = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day"
_DAY = rf"(?:{_REL}|{_EXPLICIT}|(?:last\s+)?{_WEEKDAY})"
_PART = r"(?:early\s+|late\s+)?(?:morning|afternoon|evening|night)"

_TIME_PATTERNS = {
    "part_of": _words(rf"(?:the\s+)?(?P<part>{_PART})\s+of\s+(?P<day>{_DAY})"),
    "day": _words(rf"(?P<day>{_DAY})(?:\s*,?\s+(?:in\s+the\s+|on\s+)?(?P<part>{_PART}))?"),
    "special": _words(rf"last\s+night|tonight|this\s+(?P<thispart>{_PART})"),
    "month": _words(rf"(?P<month>{_MONTH})\s+(?P<year>(?:19|20)\d{{2}})"),
    "year": _words(r"(?:in|since|during)\s+(?P<year>(?:19|20)\d{2})"),
    "hours": _words(rf"(?P<n>{_NUM})\s+hours?\s+ago"),
    "week": _words(r"(?:last|past)\s+week"),
}
_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "ten": 10}
_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _local_base(posted_at: datetime) -> datetime:
    """posted_at in its own timezone; IST when it has none."""
    return posted_at if posted_at.tzinfo else posted_at.replace(tzinfo=config.DEFAULT_TZ)


def _num(text: str) -> int:
    return int(text) if text.isdigit() else _NUMBER_WORDS[text.lower()]


def _parse_day(text: str, base: datetime) -> Optional[date]:
    """A day phrase -> calendar date, relative to base. None if it can't be resolved."""
    t = " ".join(text.lower().split())
    today = base.date()
    if re.fullmatch(r"(?:the )?day before yesterday", t):
        return today - timedelta(days=2)
    if m := re.fullmatch(rf"(last )?({_WEEKDAY})", t):
        idx = _WEEKDAYS.index(m.group(2)[:3])
        delta = (today.weekday() - idx) % 7
        if m.group(1) and delta == 0:
            delta = 7
        return today - timedelta(days=delta)
    if m := re.fullmatch(rf"({_NUM}) days? ago", t):
        return today - timedelta(days=_num(m.group(1)))

    import dateparser  # noqa: PLC0415 - slow import, only when needed

    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t).replace(" of ", " ")
    parsed = dateparser.parse(cleaned, languages=["en"], settings={
        "RELATIVE_BASE": base.replace(tzinfo=None),
        "PREFER_DATES_FROM": "past",
        "DATE_ORDER": "DMY",
        "RETURN_AS_TIMEZONE_AWARE": False,
    })
    return parsed.date() if parsed else None


def _at(d: date, hour: int, tz: tzinfo) -> datetime:
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=tz)


def _window(start: datetime, end: datetime, precision: str) -> dict[str, Any]:
    return {"date": start.date().isoformat(), "start": start.isoformat(), "end": end.isoformat(), "precision": precision}


def _day_window(d: date, part: Optional[str], tz: tzinfo) -> dict[str, Any]:
    if not part:
        start = _at(d, 0, tz)
        return _window(start, start + timedelta(days=1), "day")
    part = part.lower().split()[-1]                     # "early morning" -> "morning"
    h0, h1 = config.PART_OF_DAY_HOURS[part]
    start = _at(d, h0, tz)
    end = _at(d, h1, tz) + (timedelta(days=1) if h1 <= h0 else timedelta())   # night wraps past midnight
    return _window(start, end, "part_of_day")


def _time_normalized(kind: str, m: re.Match, base: datetime) -> Optional[dict[str, Any]]:
    tz = base.tzinfo
    today = base.date()
    if kind in ("part_of", "day"):
        d = _parse_day(m.group("day"), base)
        return _day_window(d, m.group("part"), tz) if d else None
    if kind == "special":
        t = " ".join(m.group(0).lower().split())
        if t == "last night":
            return _day_window(today - timedelta(days=1), "night", tz)
        if t == "tonight":
            return _day_window(today, "night", tz)
        return _day_window(today, m.group("thispart"), tz)
    if kind == "month":
        y, mo = int(m.group("year")), _MONTHS.index(m.group("month")[:3].lower()) + 1
        start = datetime(y, mo, 1, tzinfo=tz)
        end = datetime(y + (mo == 12), mo % 12 + 1, 1, tzinfo=tz)
        return _window(start, end, "month")
    if kind == "year":
        y = int(m.group("year"))
        return _window(datetime(y, 1, 1, tzinfo=tz), datetime(y + 1, 1, 1, tzinfo=tz), "year")
    if kind == "hours":
        start = (base - timedelta(hours=_num(m.group("n")))).replace(minute=0, second=0, microsecond=0)
        return _window(start, start + timedelta(hours=1), "hour")
    if kind == "week":
        return _window(_at(today - timedelta(days=7), 0, tz), _at(today, 0, tz), "week")
    return None


def find_times(caption: str, posted_at: datetime) -> list[Cand]:
    base = _local_base(posted_at)
    out = []
    for kind, pattern in _TIME_PATTERNS.items():
        for m in pattern.finditer(caption):
            start, end = m.span()
            if kind == "part_of":
                start, end = m.start("part"), m.end("day")      # drop a leading "the"
            elif kind == "year":
                start, end = m.span("year")                     # just "2019", not "in 2019"
            norm = _time_normalized(kind, m, base)
            if norm:
                out.append(Cand(start, end, "time", caption[start:end], norm))
    return out


# =============================================================================
# Speakers (deliberately simple)
# =============================================================================

_NAME = r"[A-Z][\w.'’-]*(?:\s+[A-Z][\w.'’-]*){0,3}"
_SPEAKER_PATTERNS = [
    re.compile(rf"(?P<name>{_NAME})\s+(?:said|says|told|stated|claimed|announced|alleged)\b"),
    # Trigger words in any case ("Speech by"); the name itself must be capitalised.
    re.compile(rf"\b(?i:speech|address|statement|remarks|interview)\s+(?i:by|from)\s+(?P<name>{_NAME})"),
]
_NOT_NAMES = {
    "he", "she", "they", "it", "we", "i", "you", "this", "that", "the", "yesterday", "today", "earlier",
    "later", "then", "now", "also", "here", "there", "officials", "police", "sources", "reports",
    "residents", "people", "video", "watch", "breaking", "massive", "huge",
}


def find_speakers(caption: str) -> list[Cand]:
    out = []
    for pattern in _SPEAKER_PATTERNS:
        for m in pattern.finditer(caption):
            base = m.start("name")
            tokens = list(re.finditer(r"\S+", m.group("name")))
            while tokens and tokens[0].group(0).lower().strip(".,'’") in _NOT_NAMES:
                tokens.pop(0)                                  # "Yesterday PM Modi said" -> "PM Modi"
            if not tokens:
                continue
            start, end = base + tokens[0].start(), base + tokens[-1].end()
            name = caption[start:end]
            if lookup_place(name):
                continue
            out.append(Cand(start, end, "speaker", name, None))
    return out


# =============================================================================
# LLM path
# =============================================================================

_LLM_SYSTEM = (
    "You extract checkable claims from a caption that was shared with a photo, video or audio clip. "
    "Return ONLY a JSON array, no prose. Each item is an object with exactly these keys: "
    '"type" (one of "location", "time", "event", "weather_condition", "speaker", "other"), '
    '"value" (a short normalised value, e.g. a city name), and '
    '"source_text" (the exact characters copied from the caption that state the claim, same spelling, case and spacing). '
    "Only include claims the caption actually states; do not infer or add anything. "
    "Do not judge whether a claim is true. Return [] if there are none."
)


def _normalize_llm(typ: str, src: str, value: str, posted_at: datetime) -> Optional[dict[str, Any]]:
    """Normalize an LLM claim with the same deterministic code as the rules path."""
    if typ == "location":
        place = lookup_place(src) or lookup_place(value)
        if place:
            return _loc_normalized(place)
        return geocode(value or src)
    if typ == "time":
        found = find_times(src, posted_at)
        return max(found, key=lambda c: c.end - c.start).normalized if found else None
    if typ in ("weather_condition", "event"):
        found = find_conditions(src)
        return found[0].normalized if found else None
    return None


def llm_candidates(caption: str, posted_at: datetime) -> list[Cand]:
    prompt = f"Caption: {json.dumps(caption, ensure_ascii=False)}\nShared at: {_local_base(posted_at).isoformat()}"
    data = llm.complete_json(_LLM_SYSTEM, prompt, max_tokens=4000)
    if isinstance(data, dict):
        data = data.get("claims", [])
    if not isinstance(data, list):
        raise llm.LLMError("expected a JSON list of claims")
    out = []
    for item in data[:30]:
        if not isinstance(item, dict):
            continue
        typ, src, value = item.get("type"), item.get("source_text"), item.get("value")
        if typ not in CLAIM_TYPES or not isinstance(src, str) or not src.strip():
            continue
        src = src.strip()
        start = caption.find(src)
        if start < 0:   # principle 3: not literally in the caption -> drop
            log.info("dropping LLM claim whose source_text is not in the caption: %r", src)
            continue
        value = value.strip() if isinstance(value, str) and value.strip() else src
        out.append(Cand(start, start + len(src), typ, value[:200], _normalize_llm(typ, src, value, posted_at), "llm"))
    return out


# =============================================================================
# Assembly
# =============================================================================

def rule_candidates(caption: str, posted_at: datetime) -> list[Cand]:
    return find_conditions(caption) + find_locations(caption) + find_times(caption, posted_at) + find_speakers(caption)


def _resolve_overlaps(cands: list[Cand]) -> list[Cand]:
    """Keep the longest candidate wherever spans overlap ("heavy rain" beats "rain", "New Delhi" beats "Delhi")."""
    picked: list[Cand] = []
    for c in sorted(cands, key=lambda c: (-(c.end - c.start), c.start)):
        if not any(c.overlaps(p) for p in picked):
            picked.append(c)
    return picked


def _dedupe_key(c: Cand) -> tuple:
    n = c.normalized or {}
    if c.type == "location" and "lat" in n:
        return ("location", n["lat"], n["lon"])
    if "condition" in n:
        return ("condition", n["condition"])
    if c.type == "time" and "start" in n:
        return ("time", n["start"], n["end"])
    return (c.type, c.value.lower())


def _dedupe(cands: list[Cand]) -> list[Cand]:
    """Same place / condition / time window mentioned twice -> keep the first mention."""
    seen, out = set(), []
    for c in sorted(cands, key=lambda c: c.start):
        key = _dedupe_key(c)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _build(caption: str, cands: list[Cand]) -> list[Claim]:
    claims: list[Claim] = []
    for c in sorted(cands, key=lambda c: c.start):
        text = caption[c.start:c.end]
        try:
            claims.append(Claim(
                id=f"c{len(claims) + 1}",
                type=c.type,
                value=c.value,
                normalized=c.normalized,
                source={"field": "caption", "start": c.start, "end": c.end, "text": text},
                extracted_by=c.extracted_by,
            ))
        except ValidationError as exc:   # e.g. a malformed normalized dict: drop, don't crash
            log.warning("dropping claim %r: %s", text, exc)
    return claims


def extract_claims(caption: str, posted_at: datetime) -> list[Claim]:
    """caption + posted_at -> claims, each traceable to an exact caption span. Never raises."""
    if not caption or not caption.strip():
        return []
    try:
        rules = _resolve_overlaps(rule_candidates(caption, posted_at))
    except Exception:  # noqa: BLE001 - principle 5
        log.exception("rule-based claim extraction failed")
        rules = []

    extra: list[Cand] = []
    if llm.available():
        try:
            extra = _resolve_overlaps(llm_candidates(caption, posted_at))
        except Exception as exc:  # noqa: BLE001 - any LLM problem -> rules only
            log.warning("LLM claim extraction failed, using rules only: %s", exc)
            extra = []

    # LLM claims only fill gaps: wherever the rules found something, the rules version wins.
    merged = rules + [c for c in extra if not any(c.overlaps(r) for r in rules)]
    return _build(caption, _dedupe(merged))


if __name__ == "__main__":
    from app.storage import parse_posted_at

    ap = argparse.ArgumentParser(description="Extract claims from a caption.")
    ap.add_argument("--caption", required=True)
    ap.add_argument("--posted-at", default=None, help="e.g. 2026-11-19 (default now)")
    args = ap.parse_args()

    claims = extract_claims(args.caption, parse_posted_at(args.posted_at))
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps([c.model_dump(mode="json") for c in claims], indent=2, ensure_ascii=False))
