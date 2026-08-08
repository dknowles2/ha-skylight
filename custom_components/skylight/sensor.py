"""Sensor platform for the Skylight integration.

One set of sensors per family profile: what the chore chart on the frame would
show for that person today, plus their reward point balance.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyskylight.models import Device

from .coordinator import (
    FrameData,
    SkylightConfigEntry,
    SkylightDataUpdateCoordinator,
)
from .entity import SkylightDeviceEntity, SkylightEntity


@dataclass(frozen=True, kw_only=True)
class SkylightSensorEntityDescription(SensorEntityDescription):
    """Describes a Skylight sensor measured per family profile."""

    value_fn: Callable[[FrameData, str], int | None]


def _chores_due(data: FrameData, category_id: str) -> int:
    return sum(1 for chore in data.chores_for(category_id) if not chore.completed)


def _chores_completed(data: FrameData, category_id: str) -> int:
    return sum(1 for chore in data.chores_for(category_id) if chore.completed)


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
    SkylightSensorEntityDescription(
        key="reward_points",
        translation_key="reward_points",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_reward_points,
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


DEVICE_SENSOR_TYPES: tuple[SkylightDeviceSensorEntityDescription, ...] = (
    SkylightDeviceSensorEntityDescription(
        key="sleep_mode",
        translation_key="sleep_mode",
        device_class=SensorDeviceClass.ENUM,
        options=["screen_off", "clock", "nightlight"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.sleep_mode,
    ),
    SkylightDeviceSensorEntityDescription(
        key="nightlight_brightness",
        translation_key="nightlight_brightness",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.nightlight_brightness,
    ),
    SkylightDeviceSensorEntityDescription(
        key="nightlight_color",
        translation_key="nightlight_color",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.nightlight_color,
    ),
    SkylightDeviceSensorEntityDescription(
        key="sleep_sound",
        translation_key="sleep_sound",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.sleep_sound,
    ),
    SkylightDeviceSensorEntityDescription(
        key="sleep_sound_volume",
        translation_key="sleep_sound_volume",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.sleep_sound_volume,
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
                for category in frame_data.categories
                for description in SENSOR_TYPES
            ),
            *(
                SkylightDeviceSensor(coordinator, frame_id, device.id, description)
                for frame_id, frame_data in coordinator.data.items()
                for device in frame_data.devices
                for description in DEVICE_SENSOR_TYPES
            ),
        ]
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
        category = coordinator.data[frame_id].categories_by_id[category_id]
        self._attr_translation_placeholders = {"profile": category.label or category_id}

    @property
    def available(self) -> bool:
        """Whether the profile still exists on the frame."""
        return super().available and self._category_id in self.frame_data.categories_by_id

    @property
    def native_value(self) -> int | None:
        """Return the current value."""
        return self.entity_description.value_fn(self.frame_data, self._category_id)


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
