"""
Weather witness (CLAUDE.md §6.3). Owner: Adithi.   Family: context. Max reliability: medium.

TODO (Adithi):
- Needs a location claim + a time claim + a weather_condition/event claim; otherwise not applicable.
- Claimed date within config.WEATHER_RECENT_DAYS -> forecast API with past_days; older -> archive API.
- Query 5 points: centre + N/S/E/W at +/- config.WEATHER_GRID_OFFSET_DEG.
- Report (a) centre rain in the claimed window/day, (b) max across surrounding points, (c) centre 72 h total.
- flood/heavy_rain/rain claims:
    (a) < 1 and (b) < 1 and (c) < 5 mm -> contradiction (medium)
    (a) >= 10 or (c) >= 30 mm         -> consistent (medium)
    otherwise                         -> inconclusive
- Alternatives: upstream/catchment rain, drainage failure, reservoir release, localised rain below grid resolution.
- Cache API responses under config.CACHE_DIR.

Run alone (from backend/), using the fixture's claims until claim extraction is ready:
    python -m app.witnesses.weather --claims-json ../shared/fixtures/sample_investigation.json
"""

from __future__ import annotations

from app.schemas import Finding
from app.witnesses.base import InvestigationContext, Witness, run_cli


class WeatherWitness(Witness):
    name = "weather"
    label = "Weather"
    implemented = False  # set True when done

    def applicable(self, ctx: InvestigationContext) -> bool:
        has_condition = bool(ctx.claims_of("weather_condition") or ctx.claims_of("event"))
        return bool(ctx.claims_of("location") and ctx.claims_of("time") and has_condition)

    def run(self, ctx: InvestigationContext) -> list[Finding]:
        return self.placeholder()


if __name__ == "__main__":
    run_cli(WeatherWitness())
