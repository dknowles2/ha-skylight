"""Data update coordinator for the Skylight integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from pyskylight import Skylight
from pyskylight.exceptions import AuthenticationError, NotAuthorizedError, SkylightError
from pyskylight.models import Category, Chore, Frame, RewardPoint

from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

type SkylightConfigEntry = ConfigEntry[SkylightDataUpdateCoordinator]


@dataclass
class FrameData:
    """Everything we poll for a single frame.

    Held as one object per frame so entities can be built from a single
    coordinator refresh without any further awaiting.
    """

    frame: Frame
    categories: list[Category] = field(default_factory=list)
    chores: list[Chore] = field(default_factory=list)
    reward_points: list[RewardPoint] = field(default_factory=list)

    @property
    def categories_by_id(self) -> dict[str, Category]:
        """Return family profiles keyed by their resource id."""
        return {category.id: category for category in self.categories}

    def chores_for(self, category_id: str) -> list[Chore]:
        """Return chores assigned to one family profile."""
        return [chore for chore in self.chores if chore.category_id == category_id]

    def points_for(self, category_id: str) -> RewardPoint | None:
        """Return the reward point balance for one family profile."""
        for point in self.reward_points:
            if str(point.category_id) == category_id:
                return point
        return None


class SkylightDataUpdateCoordinator(DataUpdateCoordinator[dict[str, FrameData]]):
    """Polls Skylight and hands entities a snapshot keyed by frame id."""

    config_entry: SkylightConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SkylightConfigEntry,
        client: Skylight,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, FrameData]:
        """Fetch the current state of every frame on the account."""
        today = dt_util.now().date()
        try:
            frames = await self.client.get_frames()
            return {frame.id: await self._fetch_frame(frame, today) for frame in frames}
        except (AuthenticationError, NotAuthorizedError) as err:
            # Raising this rather than UpdateFailed is what starts the reauth
            # flow instead of retrying forever with credentials that no longer
            # work.
            raise ConfigEntryAuthFailed("Skylight rejected the stored credentials") from err
        except SkylightError as err:
            raise UpdateFailed(f"Error talking to Skylight: {err}") from err

    async def _fetch_frame(self, frame: Frame, today: date) -> FrameData:
        """Fetch the per-frame detail entities are built from."""
        return FrameData(
            frame=frame,
            categories=await self.client.get_categories(frame.id),
            # include_late picks up anything overdue, which is what a chore
            # chart shows on the frame itself.
            chores=await self.client.get_chores(
                frame.id, after=today, before=today, include_late=True
            ),
            reward_points=await self.client.get_reward_points(frame.id),
        )
