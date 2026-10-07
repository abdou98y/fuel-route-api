import threading

from django.apps import AppConfig


class FuelrouteConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fuelroute"

    def ready(self):
        # Build the offline city index in the background so the first request
        # doesn't pay ~0.5 s for it. (The station index needs the DB, so it is
        # loaded lazily on first use instead.)
        from .services.geocoding import city_index

        threading.Thread(target=city_index, name="warm-city-index", daemon=True).start()
