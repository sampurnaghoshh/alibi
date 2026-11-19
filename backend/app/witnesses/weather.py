from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from app import config
from app.witnesses.base import InvestigationContext, Witness, run_cli


logger = logging.getLogger(__name__)


ALTERNATIVES = [
    "Rain may have occurred upstream or elsewhere in the catchment.",
    "Flooding may have resulted from drainage failure or blocked waterways.",
    "Reservoir release or water overflow may have caused flooding.",
    "Localized rainfall may not be captured by the weather grid.",
    "Weather station or gridded precipitation estimates may differ from actual rainfall.",
]


class WeatherWitness(Witness):
    name = "weather"
    label = "Weather"
    implemented = True

    def applicable(self, ctx: InvestigationContext) -> bool:
        return bool(
            ctx.claims_of("location")
            and ctx.claims_of("time")
            and (
                ctx.claims_of("weather_condition")
                or ctx.claims_of("event")
            )
        )

    def run(self, ctx: InvestigationContext) -> list[Any]:
        location_claims = ctx.claims_of("location")
        time_claims = ctx.claims_of("time")

        condition_claims = (
            ctx.claims_of("weather_condition")
            or ctx.claims_of("event")
        )

        if not location_claims or not time_claims or not condition_claims:
            return [
                self.finding(
                    status="inconclusive",
                    reliability="low",
                    title="Insufficient weather claims",
                    summary="Location, time, or weather condition claims are missing.",
                    details={
                        "reason": "Required claims were not available."
                    },
                    alternatives=ALTERNATIVES,
                    limitations=[
                        "Weather verification requires a location, time, and condition."
                    ],
                )
            ]

        location = self._normalized(location_claims[0])
        time_data = self._normalized(time_claims[0])
        condition_data = self._normalized(condition_claims[0])

        lat = self._get(location, "lat")
        lon = self._get(location, "lon")

        claimed_date = self._get(time_data, "date")
        claimed_start = self._get(time_data, "start")
        claimed_end = self._get(time_data, "end")

        condition = (
            self._get(condition_data, "condition")
            or self._get(condition_data, "event")
            or ""
        )

        condition = str(condition).lower().strip()

        claim_ids = [
            self._claim_id(c)
            for c in (
                location_claims[0],
                time_claims[0],
                condition_claims[0],
            )
            if self._claim_id(c)
        ]

        if lat is None or lon is None:
            return [
                self.finding(
                    status="inconclusive",
                    reliability="low",
                    claim_ids=claim_ids,
                    title="Weather verification unavailable",
                    summary="The location claim does not contain usable coordinates.",
                    details={
                        "location": location,
                        "reason": "Missing latitude or longitude.",
                    },
                    alternatives=ALTERNATIVES,
                    limitations=[
                        "Weather data cannot be queried without coordinates."
                    ],
                )
            ]

        if condition not in {"rain", "heavy_rain", "flood"}:
            return [
                self.finding(
                    status="inconclusive",
                    reliability="low",
                    claim_ids=claim_ids,
                    title="Weather condition not evaluated",
                    summary="This witness currently evaluates rain and flood-related claims.",
                    details={
                        "claimed_condition": condition,
                    },
                    alternatives=ALTERNATIVES,
                    limitations=[
                        "The rainfall thresholds are designed for rain and flood claims.",
                        "Other weather conditions are not evaluated by this witness.",
                    ],
                )
            ]

        try:
            start_dt, end_dt = self._claim_window(
                claimed_date,
                claimed_start,
                claimed_end,
            )
        except (ValueError, TypeError, OverflowError) as exc:
            return [
                self.finding(
                    status="inconclusive",
                    reliability="low",
                    claim_ids=claim_ids,
                    title="Weather time window unavailable",
                    summary="The claimed time could not be interpreted.",
                    details={
                        "error": str(exc),
                        "claimed_date": str(claimed_date),
                        "claimed_start": str(claimed_start),
                        "claimed_end": str(claimed_end),
                    },
                    alternatives=ALTERNATIVES,
                    limitations=[
                        "A valid claimed date and time window are required."
                    ],
                )
            ]

        center_lat = float(lat)
        center_lon = float(lon)

        offset = float(config.WEATHER_GRID_OFFSET_DEG)

        points = {
            "center": (center_lat, center_lon),
            "north": (center_lat + offset, center_lon),
            "south": (center_lat - offset, center_lon),
            "east": (center_lat, center_lon + offset),
            "west": (center_lat, center_lon - offset),
        }

        claimed_date_obj = start_dt.date()
        cutoff = date.today() - timedelta(
            days=config.WEATHER_RECENT_DAYS
        )

        if claimed_date_obj >= cutoff:
            endpoint = config.WEATHER_FORECAST_URL

            common_params = {
                "hourly": "precipitation",
                "daily": "precipitation_sum",
                "past_days": config.WEATHER_PAST_DAYS,
                "timezone": "auto",
            }

        else:
            endpoint = config.WEATHER_ARCHIVE_URL

            archive_start = (start_dt - timedelta(days=3)).date()
            archive_end = end_dt.date()

            common_params = {
                "hourly": "precipitation",
                "start_date": archive_start.isoformat(),
                "end_date": archive_end.isoformat(),
                "timezone": "auto",
            }

        weather_data: dict[str, Any] = {}
        errors: dict[str, str] = {}

        for point_name, (point_lat, point_lon) in points.items():
            params = {
                **common_params,
                "latitude": point_lat,
                "longitude": point_lon,
            }

            try:
                weather_data[point_name] = self._cached_request(
                    endpoint,
                    params,
                )

            except Exception as exc:
                logger.warning(
                    "Weather fetch failed for %s: %s",
                    point_name,
                    exc,
                )

                errors[point_name] = str(exc)

        center_data = weather_data.get("center")

        if not center_data:
            return [
                self.finding(
                    status="inconclusive",
                    reliability="low",
                    claim_ids=claim_ids,
                    title="Weather data unavailable",
                    summary="Historical or forecast weather data could not be retrieved for the claimed location.",
                    details={
                        "claimed_condition": condition,
                        "claimed_window": {
                            "start": start_dt.isoformat(),
                            "end": end_dt.isoformat(),
                        },
                        "errors": errors,
                    },
                    alternatives=ALTERNATIVES,
                    limitations=[
                        "Weather API data was unavailable for the center point.",
                        "No conclusion can be drawn without center-point rainfall data.",
                    ],
                )
            ]

        # Rainfall during the claimed time window at the center point.
        center_rain, center_coverage = self._sum_hourly(
            center_data,
            start_dt,
            end_dt,
        )

        # Rainfall during the claimed time window at surrounding points.
        surrounding_rain: dict[str, float | None] = {}
        surrounding_coverage: dict[str, bool] = {}

        for point_name in ("north", "south", "east", "west"):
            point_data = weather_data.get(point_name)

            if point_data is None:
                surrounding_rain[point_name] = None
                surrounding_coverage[point_name] = False
                continue

            rainfall, coverage = self._sum_hourly(
                point_data,
                start_dt,
                end_dt,
            )

            surrounding_rain[point_name] = rainfall
            surrounding_coverage[point_name] = coverage

        max_surrounding_rain = (
            max(
                value
                for value in surrounding_rain.values()
                if value is not None
            )
            if any(
                value is not None
                for value in surrounding_rain.values()
            )
            else None
        )

        # Rainfall over the 72 hours preceding the claimed event end.
        center_72h, coverage_72h = self._sum_72h(
            center_data,
            end_dt,
        )

        complete_grid = all(surrounding_coverage.values())

        details = {
            "claimed_condition": condition,
            "coordinates": {
                "latitude": center_lat,
                "longitude": center_lon,
            },
            "claimed_window": {
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
            },
            "rainfall_mm": {
                "center_claimed_window": center_rain,
                "center_coverage_complete": center_coverage,
                "surrounding_points": surrounding_rain,
                "surrounding_coverage": surrounding_coverage,
                "max_surrounding_point": max_surrounding_rain,
                "center_72h": center_72h,
                "center_72h_coverage_complete": coverage_72h,
            },
            "grid_complete": complete_grid,
            "errors": errors,
            "data_source": endpoint,
        }

        status = "inconclusive"
        reliability = "low"

        title = "Rainfall evidence inconclusive"

        summary = (
            "Weather data was retrieved, but it is insufficient "
            "to confirm or contradict the claimed rainfall event."
        )

        dry_threshold = config.WEATHER_DRY_POINT_MM
        dry_72h_threshold = config.WEATHER_DRY_72H_MM
        wet_threshold = config.WEATHER_WET_POINT_MM
        wet_72h_threshold = config.WEATHER_WET_72H_MM

        # -----------------------------------------------------
        # Evidence supporting significant rainfall
        # -----------------------------------------------------

        if center_coverage and coverage_72h:

            if (
                center_rain >= wet_threshold
                or center_72h >= wet_72h_threshold
            ):
                status = "consistent"
                reliability = "medium"

                title = "Rainfall evidence supports the claim"

                summary = (
                    "Rainfall measurements indicate significant precipitation "
                    "at or near the claimed location."
                )

            # -------------------------------------------------
            # Evidence contradicting a rainfall/flood claim
            # -------------------------------------------------

            elif (
                complete_grid
                and center_rain < dry_threshold
                and max_surrounding_rain is not None
                and max_surrounding_rain < dry_threshold
                and center_72h < dry_72h_threshold
            ):
                status = "contradiction"
                reliability = "medium"

                title = "Weather data contradicts the rainfall claim"

                summary = (
                    "Rainfall was very low at the claimed location, "
                    "surrounding grid points, and over the preceding 72 hours."
                )

        limitations = [
            "Weather data is gridded and may not represent rainfall at the exact location.",
            "Rainfall estimates are supporting evidence, not ground truth.",
            "Flooding may occur without rain at the claimed location.",
            "Localized rainfall may be missed by the grid.",
        ]

        if not center_coverage:
            limitations.append(
                "The claimed time-window rainfall data has incomplete coverage."
            )

        if not coverage_72h:
            limitations.append(
                "The 72-hour rainfall data has incomplete coverage."
            )

        if not complete_grid:
            limitations.append(
                "Not all surrounding grid points returned complete rainfall data."
            )

        if errors:
            limitations.append(
                "One or more weather API requests failed."
            )

        return [
            self.finding(
                status=status,
                reliability=reliability,
                claim_ids=claim_ids,
                title=title,
                summary=summary,
                details=details,
                alternatives=ALTERNATIVES,
                limitations=limitations,
            )
        ]

    # =========================================================
    # Claim helpers
    # =========================================================

    @staticmethod
    def _normalized(claim: Any) -> Any:
        normalized = getattr(claim, "normalized", None)

        if normalized is None and isinstance(claim, dict):
            normalized = claim.get("normalized", {})

        return normalized or {}

    @staticmethod
    def _get(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(key)

        return getattr(obj, key, None)

    @staticmethod
    def _claim_id(claim: Any) -> str | None:
        if isinstance(claim, dict):
            return claim.get("id")

        return getattr(claim, "id", None)

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value

        if value is None:
            raise ValueError("Missing datetime value.")

        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

    @classmethod
    def _claim_window(
        cls,
        claimed_date: Any,
        claimed_start: Any,
        claimed_end: Any,
    ) -> tuple[datetime, datetime]:

        if claimed_start and claimed_end:
            start_dt = cls._parse_datetime(claimed_start)
            end_dt = cls._parse_datetime(claimed_end)

        elif claimed_date:
            if isinstance(claimed_date, date):
                date_obj = claimed_date
            else:
                date_obj = date.fromisoformat(
                    str(claimed_date)[:10]
                )

            start_dt = datetime.combine(
                date_obj,
                datetime.min.time(),
            )

            end_dt = datetime.combine(
                date_obj,
                datetime.max.time().replace(microsecond=0),
            )

        else:
            raise ValueError("Missing claimed date or time window.")

        if end_dt < start_dt:
            raise ValueError("Claimed end time is before start time.")

        return start_dt, end_dt

    # =========================================================
    # Weather API + cache
    # =========================================================

    @staticmethod
    def _cached_request(
        endpoint: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:

        cache_dir = Path(config.CACHE_DIR)
        cache_dir.mkdir(parents=True, exist_ok=True)

        cache_key = hashlib.sha256(
            (
                endpoint
                + json.dumps(
                    params,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ).encode("utf-8")
        ).hexdigest()

        cache_file = cache_dir / f"weather_{cache_key}.json"

        if cache_file.exists():
            try:
                return json.loads(
                    cache_file.read_text(encoding="utf-8")
                )

            except (OSError, json.JSONDecodeError):
                logger.warning(
                    "Weather cache file is invalid: %s",
                    cache_file,
                )

        response = requests.get(
            endpoint,
            params=params,
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        cache_file.write_text(
            json.dumps(data, indent=2),
            encoding="utf-8",
        )

        return data

    # =========================================================
    # Rainfall calculations
    # =========================================================

    @staticmethod
    def _to_claim_timezone(
        timestamp: str,
        claim_timezone: Any,
    ) -> datetime:

        parsed = datetime.fromisoformat(
            timestamp.replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            if claim_timezone is not None:
                parsed = parsed.replace(
                    tzinfo=claim_timezone
                )
            else:
                parsed = parsed.astimezone()

        elif claim_timezone is not None:
            parsed = parsed.astimezone(
                claim_timezone
            )

        return parsed

    @classmethod
    def _sum_hourly(
        cls,
        data: dict[str, Any],
        start: datetime,
        end: datetime,
    ) -> tuple[float, bool]:

        hourly = data.get("hourly", {})

        times = hourly.get("time", [])
        precipitation = hourly.get("precipitation", [])

        if not times or not precipitation:
            return 0.0, False

        claim_timezone = start.tzinfo

        rainfall = 0.0
        count = 0

        for timestamp, value in zip(
            times,
            precipitation,
        ):
            try:
                weather_time = cls._to_claim_timezone(
                    timestamp,
                    claim_timezone,
                )

            except (ValueError, TypeError):
                continue

            if start <= weather_time <= end:
                if value is not None:
                    rainfall += float(value)
                    count += 1

        expected_hours = int(
            (end - start).total_seconds() // 3600
        ) + 1

        coverage = count >= expected_hours

        return round(rainfall, 2), coverage

    @classmethod
    def _sum_72h(
        cls,
        data: dict[str, Any],
        end: datetime,
    ) -> tuple[float, bool]:

        start = end - timedelta(hours=72)

        return cls._sum_hourly(
            data,
            start,
            end,
        )


if __name__ == "__main__":
    run_cli(WeatherWitness())