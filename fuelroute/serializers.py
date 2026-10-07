import math

from rest_framework import serializers

from .services.configuration import get_vehicle_config

LOCATION_HELP = 'US location: "City, ST", "lat,lng", or a free-form address.'


class RoutePlanRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=200, help_text=LOCATION_HELP)
    finish = serializers.CharField(max_length=200, help_text=LOCATION_HELP)
    start_fuel_gallons = serializers.FloatField(
        required=False,
        allow_null=True,
        min_value=0,
        help_text="Fuel in the tank at the start. Defaults to a full tank.",
    )

    start_fuel_price_per_gallon = serializers.FloatField(
        required=False,
        allow_null=True,
        min_value=0,
        help_text="USD per gallon of starting fuel. Defaults to the lowest imported station price.",
    )

    def validate_start_fuel_gallons(self, value):
        if value is not None:
            vehicle = self.context.get("vehicle") or get_vehicle_config()
            if value > vehicle.tank_gallons:
                raise serializers.ValidationError(
                    f"Ensure this value is less than or equal to {vehicle.tank_gallons:g}."
                )
        return value

    def validate(self, attrs):
        for field in ("start_fuel_gallons", "start_fuel_price_per_gallon"):
            value = attrs.get(field)
            if value is not None and not math.isfinite(value):
                raise serializers.ValidationError({field: "Must be a finite number."})
        return attrs
