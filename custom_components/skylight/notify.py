"""Notify platform for the Skylight integration.

Skylight calls these "nudges": a short message that the frame speaks out loud to
one family member. Sending one from Home Assistant is what lets an automation
talk to the room — "the bus is in five minutes" — instead of only writing to a
list nobody is looking at.

One entity per family profile, because a nudge is addressed to a person rather
than to the frame. Sending to several people is a matter of targeting several
entities.
"""

from __future__ import annotations

from homeassistant.components.notify import NotifyEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
from pyskylight.exceptions import SkylightError

from .const import DOMAIN
from .coordinator import SkylightConfigEntry, SkylightDataUpdateCoordinator
from .entity import SkylightEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkylightConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a nudge target for every family profile."""
    coordinator = entry.runtime_data
    async_add_entities(
        SkylightNudgeEntity(coordinator, frame_id, category.id)
        for frame_id, frame_data in coordinator.data.items()
        for category in frame_data.profiles
    )


class SkylightNudgeEntity(SkylightEntity, NotifyEntity):
    """Speaks a message on the frame, addressed to one family profile."""

    _attr_translation_key = "nudge"

    def __init__(
        self,
        coordinator: SkylightDataUpdateCoordinator,
        frame_id: str,
        category_id: str,
    ) -> None:
        """Initialize the notify entity."""
        super().__init__(coordinator, frame_id)
        self._category_id = category_id
        self._attr_unique_id = f"{frame_id}_{category_id}_nudge"
        # The device is the frame, so the name has to carry the profile to tell
        # one person's nudges from another's.
        category = coordinator.data[frame_id].profiles_by_id[category_id]
        self._attr_translation_placeholders = {"profile": category.label or category_id}

    @property
    def available(self) -> bool:
        """Whether the profile still exists on the frame."""
        return super().available and self._category_id in self.frame_data.profiles_by_id

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Create a nudge that plays now.

        `title` is ignored: a nudge is spoken, and a heading has nowhere to go.

        Skylight renders the speech server-side — `audio_url` on the created
        nudge fills in with an MP3 within about ten seconds — and the frame
        plays it at `deliver_at`. The API accepts a `deliver_at` in the past
        without complaint, so "now" is the closest thing to an immediate
        announcement it offers. Scheduling a nudge for later is left to Home
        Assistant, which already knows how to run an automation at a time.

        No refresh follows: nudges are not part of the polled snapshot, so there
        would be nothing for one to update.
        """
        try:
            await self.coordinator.client.create_nudge(
                self._frame_id,
                body=message,
                deliver_at=dt_util.utcnow(),
                category_ids=[self._category_id],
            )
        except SkylightError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="send_nudge_failed",
                translation_placeholders={"error": str(err)},
            ) from err
