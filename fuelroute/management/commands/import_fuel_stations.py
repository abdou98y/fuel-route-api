"""Load the OPIS fuel price CSV into the database with coordinates.

Usage:
    python manage.py import_fuel_stations                 # uses data/fuel-prices.csv
    python manage.py import_fuel_stations path/to.csv --offline

Geocoding happens here, once, so the API never geocodes stations at request
time. Cities are matched against the bundled Census gazetteer; the handful it
doesn't know are looked up on Nominatim (1 req/s) and persisted to
``data/station_geocode_overrides.json`` so subsequent imports are fully offline.
"""

import csv
import json
import time
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from fuelroute.models import FuelStation
from fuelroute.services.geocoding import (
    US_STATES,
    GeocodingServiceError,
    lookup_city,
    normalize_city,
    search_nominatim,
)
from fuelroute.services.station_index import clear_station_index

DEFAULT_CSV = Path(settings.BASE_DIR) / "data" / "fuel-prices.csv"
OVERRIDES_PATH = Path(settings.BASE_DIR) / "data" / "station_geocode_overrides.json"


class Command(BaseCommand):
    help = "Import and geocode fuel stations from the OPIS price CSV."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", nargs="?", default=str(DEFAULT_CSV))
        parser.add_argument(
            "--offline",
            action="store_true",
            help="Skip stations whose city isn't in the gazetteer/overrides instead of calling Nominatim.",
        )

    def handle(self, csv_path, offline, **options):
        path = Path(csv_path)
        if not path.exists():
            raise CommandError(f"CSV not found: {path}")

        stations = self._read_cheapest_per_station(path)
        overrides = (
            json.loads(OVERRIDES_PATH.read_text()) if OVERRIDES_PATH.exists() else {}
        )
        used_overrides: dict = {}

        objects, unresolved = [], []
        for row in stations.values():
            coords = self._coords_for(
                row["City"], row["State"], overrides, used_overrides, offline
            )
            if coords is None:
                unresolved.append(f"{row['City']}, {row['State']}")
                continue
            objects.append(
                FuelStation(
                    opis_id=int(row["OPIS Truckstop ID"]),
                    name=row["Truckstop Name"].strip(),
                    address=row["Address"].strip(),
                    city=row["City"].strip(),
                    state=row["State"],
                    rack_id=int(row["Rack ID"]) if row["Rack ID"].strip() else None,
                    retail_price=Decimal(row["Retail Price"]).quantize(
                        Decimal("0.00001")
                    ),
                    latitude=coords[0],
                    longitude=coords[1],
                )
            )

        lines = ",\n".join(
            f" {json.dumps(k)}: {json.dumps(v)}"
            for k, v in sorted(used_overrides.items())
        )
        OVERRIDES_PATH.write_text("{\n" + lines + "\n}\n")

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(objects, batch_size=1000)
        clear_station_index()

        self.stdout.write(
            self.style.SUCCESS(f"Imported {len(objects)} US fuel stations.")
        )
        if unresolved:
            self.stdout.write(
                self.style.WARNING(
                    f"Skipped {len(unresolved)} stations with unknown city: {sorted(set(unresolved))}"
                )
            )

    @staticmethod
    def _read_cheapest_per_station(path: Path) -> dict[str, dict]:
        """The CSV repeats some OPIS IDs (name variants / price updates); keep the cheapest."""
        stations: dict[str, dict] = {}
        with path.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                row["State"] = row["State"].strip().upper()
                row["City"] = " ".join(row["City"].split())
                if (
                    row["State"] not in US_STATES
                ):  # the list also contains Canadian stops
                    continue
                key = row["OPIS Truckstop ID"].strip()
                if key not in stations or float(row["Retail Price"]) < float(
                    stations[key]["Retail Price"]
                ):
                    stations[key] = row
        return stations

    def _coords_for(self, city, state, overrides, used_overrides, offline):
        if coords := lookup_city(city, state):
            return coords
        key = f"{normalize_city(city)}|{state}"
        if key in overrides:
            used_overrides[key] = overrides[key]
            return tuple(overrides[key]) if overrides[key] else None
        if offline:
            return None

        time.sleep(1.1)  # Nominatim usage policy: max 1 request/second
        try:
            result = search_nominatim({"city": city, "state": state})
        except GeocodingServiceError as exc:
            self.stderr.write(f"Nominatim failed for {city}, {state}: {exc}")
            return None
        coords = (round(result[0], 5), round(result[1], 5)) if result else None
        overrides[key] = used_overrides[key] = coords
        self.stdout.write(f"  geocoded via Nominatim: {city}, {state} -> {coords}")
        return coords
