"""Sensor platform for the Skylight integration.

One set of sensors per family profile: what the chore chart on the frame would
show for that person today, plus their reward point balance.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import (
    FrameData,
    SkylightConfigEntry,
    SkylightDataUpdateCoordinator,
)
from .entity import SkylightEntity


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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkylightConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Skylight sensors from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        SkylightSensor(coordinator, frame_id, category.id, description)
        for frame_id, frame_data in coordinator.data.items()
        for category in frame_data.categories
        for description in SENSOR_TYPES
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
