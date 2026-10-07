"""Django settings for the fuel route planner API."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "fuelroute",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Route plans are cached so that repeated requests (and the HTML map view)
# never hit the external routing API again for the same trip.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": BASE_DIR / ".cache",
        "TIMEOUT": 60 * 60 * 24,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
}

# --- Fuel route planner -------------------------------------------------------
FUEL_ROUTE = {
    # Free, key-less OSRM server. Swap for a self-hosted OSRM in production.
    "OSRM_BASE_URL": os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org"),
    # Only used when an input isn't "lat,lng" or a "City, ST" we know offline.
    "NOMINATIM_BASE_URL": os.getenv("NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org"),
    "HTTP_USER_AGENT": os.getenv("HTTP_USER_AGENT", "fuel-route-api/1.0 (assessment project)"),
    "HTTP_TIMEOUT_SECONDS": float(os.getenv("HTTP_TIMEOUT_SECONDS", "20")),
    "VEHICLE_RANGE_MILES": 500.0,
    "VEHICLE_MPG": 10.0,
    # Stations are geocoded to their city centroid, so allow some slack when
    # deciding whether a station is "on" the route.
    "MAX_STATION_DISTANCE_FROM_ROUTE_MILES": float(os.getenv("MAX_STATION_DISTANCE_MILES", "10")),
    # Dollar value of the time a fuel stop costs. Keeps the plan from making two
    # stops 10 miles apart to save cents; 0 gives the strict cheapest plan.
    "STOP_PENALTY_USD": float(os.getenv("STOP_PENALTY_USD", "5")),
    # Number of alternatives requested, in addition to the primary route.
    "ROUTE_ALTERNATIVES": int(os.getenv("ROUTE_ALTERNATIVES", "3")),
    # Driver + truck operating cost, excluding fuel (already counted separately).
    "DRIVER_TRUCK_COST_PER_HOUR": float(os.getenv("DRIVER_TRUCK_COST_PER_HOUR", "50")),
    # Resolution used when snapping stations to the route polyline.
    "ROUTE_SAMPLE_SPACING_MILES": 0.5,
    # Spacing of the simplified polyline returned in the JSON response.
    "RESPONSE_GEOMETRY_SPACING_MILES": 2.0,
}
