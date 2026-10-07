from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RoutePlanRequestSerializer
from .services.configuration import get_planning_config
from .services.geocoding import GeocodingError, GeocodingServiceError
from .services.optimizer import InfeasibleRouteError
from .services.planner import build_plan
from .services.routing import NoRouteError, RoutingError


def _plan_or_error(data):
    """Validate input and build a plan; returns (plan, error_response)."""
    config = get_planning_config()
    serializer = RoutePlanRequestSerializer(
        data=data, context={"vehicle": config.vehicle}
    )
    if not serializer.is_valid():
        return None, Response(
            {"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST
        )
    try:
        data = serializer.validated_data
        return build_plan(
            data["start"],
            data["finish"],
            data.get("start_fuel_gallons"),
            data.get("start_fuel_price_per_gallon"),
            config=config,
        ), None
    except GeocodingServiceError as exc:
        return None, Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
    except GeocodingError as exc:
        return None, Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except (InfeasibleRouteError, NoRouteError) as exc:
        return None, Response(
            {"error": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY
        )
    except RoutingError as exc:
        return None, Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)


class RoutePlanView(APIView):
    """Plan the cheapest fuel stops between two US locations.

    GET  /api/route/?start=Chicago, IL&finish=Denver, CO[&start_fuel_gallons=20]
    POST /api/route/  {"start": "Chicago, IL", "finish": "Denver, CO", "start_fuel_gallons": 20}
    """

    def get(self, request):
        return self._respond(request, request.query_params)

    def post(self, request):
        return self._respond(request, request.data)

    def _respond(self, request, data):
        plan, error = _plan_or_error(data)
        if error:
            return error
        params = {"start": data["start"], "finish": data["finish"]}
        for field in ("start_fuel_gallons", "start_fuel_price_per_gallon"):
            if data.get(field) not in (None, ""):
                params[field] = data[field]
        query = urlencode(params)
        plan["map_url"] = request.build_absolute_uri(f"{reverse('route-map')}?{query}")
        return Response(plan)


class RouteMapView(APIView):
    """Same plan rendered as an interactive Leaflet map (route served from cache)."""

    def get(self, request):
        plan, error = _plan_or_error(request.query_params)
        if error:
            return error
        return render(request, "fuelroute/map.html", {"plan": plan})
