"""Sensor platform for the Skylight integration.

One set of sensors per family profile: what the chore chart on the frame would
show for that person today, plus their reward point balance.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyskylight.models import Chore, Device

from .const import ATTR_POINTS, DOMAIN, SERVICE_AWARD_POINTS, SERVICE_DEDUCT_POINTS
from .coordinator import (
    FrameData,
    SkylightConfigEntry,
    SkylightDataUpdateCoordinator,
)
from .entity import SkylightDeviceEntity, SkylightEntity, is_buddy


@dataclass(frozen=True, kw_only=True)
class SkylightSensorEntityDescription(SensorEntityDescription):
    """Describes a Skylight sensor measured per family profile."""

    value_fn: Callable[[FrameData, str], float | None]
    #: Extra state attributes, for sensors whose number is worth showing its
    #: working — a percentage on its own does not say out of how many.
    attributes_fn: Callable[[FrameData, str], dict[str, Any]] | None = None


def _chores_due(data: FrameData, category_id: str) -> int:
    return sum(1 for chore in data.chores_for(category_id) if not chore.completed)


def _chores_completed(data: FrameData, category_id: str) -> int:
    return sum(1 for chore in data.chores_for(category_id) if chore.completed)


def _routine(data: FrameData, category_id: str) -> list[Chore]:
    """Return the chores Skylight treats as part of a routine.

    `routine` is the API's own flag, not something inferred from the clock. On a
    real chart it separates getting-ready chores — which all carry a time of day
    — from open-ended ones like a summer reading assignment.
    """
    return [chore for chore in data.chores_for(category_id) if chore.routine]


def _other(data: FrameData, category_id: str) -> list[Chore]:
    """Return everything that is not part of a routine."""
    return [chore for chore in data.chores_for(category_id) if not chore.routine]


def _progress(chores: list[Chore]) -> float | None:
    """Return what share of these chores is done, as a percentage.

    `None` for an empty list, deliberately. A profile with nothing on their
    chart today has no ratio: 0% reads as "nothing done" and 100% as "all done",
    and both are claims about a chart that does not exist. Unknown is the honest
    answer, and a conditional card can hide the gauge on it.
    """
    if not chores:
        return None
    return round(sum(1 for chore in chores if chore.completed) / len(chores) * 100, 1)


def _counts(chores: list[Chore]) -> dict[str, Any]:
    """Return the counts behind a percentage."""
    completed = sum(1 for chore in chores if chore.completed)
    return {"completed": completed, "due": len(chores) - completed, "total": len(chores)}


def _lifetime_points(data: FrameData, category_id: str) -> int | None:
    points = data.points_for(category_id)
    return points.lifetime_points_earned if points else None


def _reward_points(data: FrameData, category_id: str) -> int | None:
    points = data.points_for(category_id)
    return points.current_point_balance if points else None


SENSOR_TYPES: tuple[SkylightSensorEntityDescription, ...] = (
    SkylightSensorEntityDescription(
        key="chores_due",
        translation_key="chores_due",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_chores_due,
    ),
    SkylightSensorEntityDescription(
        key="chores_completed",
        translation_key="chores_completed",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_chores_completed,
    ),
    # Percentages rather than counts, because a gauge card takes one entity and
    # a static maximum — it cannot divide `chores_completed` by the total.
    SkylightSensorEntityDescription(
        key="chores_progress",
        translation_key="chores_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data, category_id: _progress(data.chores_for(category_id)),
        attributes_fn=lambda data, category_id: _counts(data.chores_for(category_id)),
    ),
    SkylightSensorEntityDescription(
        key="routine_progress",
        translation_key="routine_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data, category_id: _progress(_routine(data, category_id)),
        attributes_fn=lambda data, category_id: _counts(_routine(data, category_id)),
    ),
    SkylightSensorEntityDescription(
        key="other_chores_progress",
        translation_key="other_chores_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data, category_id: _progress(_other(data, category_id)),
        attributes_fn=lambda data, category_id: _counts(_other(data, category_id)),
    ),
    SkylightSensorEntityDescription(
        key="reward_points",
        translation_key="reward_points",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_reward_points,
    ),
    SkylightSensorEntityDescription(
        key="lifetime_points",
        translation_key="lifetime_points",
        # Not TOTAL_INCREASING: deducting points lowers the lifetime figure too,
        # verified on a test frame, and Home Assistant would read that as a
        # counter reset and corrupt the statistics.
        state_class=SensorStateClass.TOTAL,
        entity_registry_enabled_default=False,
        value_fn=_lifetime_points,
    ),
)


@dataclass(frozen=True, kw_only=True)
class SkylightDeviceSensorEntityDescription(SensorEntityDescription):
    """Describes a sensor read from a physical device.

    Only attributes the device alone carries appear here. Brightness, sleep
    schedule, and slideshow settings are reported by the frame too, and live
    there so the two are not duplicated.
    """

    value_fn: Callable[[Device], str | int | None]
    #: Only built for a Skylight Buddy; see `is_buddy`.
    buddy_only: bool = False


DEVICE_SENSOR_TYPES: tuple[SkylightDeviceSensorEntityDescription, ...] = (
    # Read-only on purpose. The API accepts only the current value for
    # sleep_mode, returning a 500 for anything else, and sleep_sound has no
    # known set of valid values. Every other display attribute is writable and
    # lives on the switch, number, select, and time platforms instead of being
    # duplicated here.
    SkylightDeviceSensorEntityDescription(
        key="sleep_mode",
        translation_key="sleep_mode",
        device_class=SensorDeviceClass.ENUM,
        # The set Skylight's own client uses (`buddyConstants.sleepModes`).
        # `clock` and `nightlight` were guesses and are not values the API ever
        # returns; a device reporting `dim_clock` had no option to match.
        options=["screen_off", "dim_clock"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.sleep_mode,
    ),
    SkylightDeviceSensorEntityDescription(
        key="sleep_sound",
        translation_key="sleep_sound",
        entity_category=EntityCategory.DIAGNOSTIC,
        buddy_only=True,
        value_fn=lambda device: device.sleep_sound,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkylightConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Skylight sensors from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            *(
                SkylightSensor(coordinator, frame_id, category.id, description)
                for frame_id, frame_data in coordinator.data.items()
                for category in frame_data.profiles
                for description in SENSOR_TYPES
            ),
            *(
                SkylightDeviceSensor(coordinator, frame_id, device.id, description)
                for frame_id, frame_data in coordinator.data.items()
                for device in frame_data.devices
                for description in DEVICE_SENSOR_TYPES
                if is_buddy(device) or not description.buddy_only
            ),
        ]
    )

    # Both are registered against the reward point sensor, which is the only
    # entity that already knows the frame and the profile being credited.
    platform = entity_platform.async_get_current_platform()
    points_schema: dict[str | vol.Marker, Any] = {
        vol.Required(ATTR_POINTS): vol.All(cv.positive_int, vol.Range(min=1))
    }
    platform.async_register_entity_service(
        SERVICE_AWARD_POINTS, points_schema, "async_award_points"
    )
    platform.async_register_entity_service(
        SERVICE_DEDUCT_POINTS, points_schema, "async_deduct_points"
    )


class SkylightSensor(SkylightEntity, SensorEntity):
    """A sensor reporting one measure for one family profile."""

    entity_description: SkylightSensorEntityDescription

    def __init__(
        self,
        coordinator: SkylightDataUpdateCoordinator,
        frame_id: str,
        category_id: str,
        description: SkylightSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, frame_id)
        self.entity_description = description
        self._category_id = category_id
        self._attr_unique_id = f"{frame_id}_{category_id}_{description.key}"
        # The device is the frame, so the entity name has to carry the profile
        # to tell one person's chore count from another's. A placeholder keeps
        # the name translatable, unlike prefixing the string here.
        category = coordinator.data[frame_id].profiles_by_id[category_id]
        self._attr_translation_placeholders = {"profile": category.label or category_id}

    @property
    def available(self) -> bool:
        """Whether the profile still exists on the frame."""
        return super().available and self._category_id in self.frame_data.profiles_by_id

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        return self.entity_description.value_fn(self.frame_data, self._category_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the counts behind a percentage, for sensors that have them."""
        if (attributes_fn := self.entity_description.attributes_fn) is None:
            return None
        return attributes_fn(self.frame_data, self._category_id)

    async def async_award_points(self, points: int) -> None:
        """Give this profile points — stars, on the frame."""
        await self._async_change_points(points)

    async def async_deduct_points(self, points: int) -> None:
        """Take points away from this profile.

        Skylight does not clamp at zero: deducting more than the balance leaves
        it negative, and lowers the lifetime figure too. That is the frame's
        behaviour, not something to correct here.
        """
        await self._async_change_points(-points)

    async def _async_change_points(self, points: int) -> None:
        if self.entity_description.key != "reward_points":
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_a_points_sensor",
                translation_placeholders={"entity_id": self.entity_id},
            )
        await self.async_write(
            "change_points_failed",
            self.coordinator.client.update_reward_points(
                self._frame_id, [self._category_id], points
            ),
        )


class SkylightDeviceSensor(SkylightDeviceEntity, SensorEntity):
    """A sensor reading one attribute of a physical device."""

    entity_description: SkylightDeviceSensorEntityDescription

    def __init__(
        self,
        coordinator: SkylightDataUpdateCoordinator,
        frame_id: str,
        device_id: str,
        description: SkylightDeviceSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, frame_id, device_id)
        self.entity_description = description
        self._attr_unique_id = f"device_{device_id}_{description.key}"

    @property
    def native_value(self) -> str | int | None:
        """Return the current value."""
        return self.entity_description.value_fn(self.device)
