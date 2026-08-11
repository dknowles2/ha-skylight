"""Number platform for the Skylight integration.

Two kinds of number: the numeric settings on a physical display, every one of
which was verified writable against real hardware, and the point cost of a
reward, which Skylight also lets you change.

Redeeming a reward is the `skylight.redeem_reward` action, targeted at the
reward's number entity. It is an action rather than a button because a
redemption spends points and cannot be undone from a stray tap on a dashboard,
and because an automation may want to pick which reward to redeem.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyskylight.models import Device, Reward

from .const import DOMAIN, SERVICE_REDEEM_REWARD
from .coordinator import SkylightConfigEntry, SkylightDataUpdateCoordinator
from .entity import SkylightDeviceEntity, SkylightEntity, is_buddy

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class SkylightNumberEntityDescription(NumberEntityDescription):
    """Describes a numeric setting on a physical display."""

    value_fn: Callable[[Device], int | None]
    #: Only built for a Skylight Buddy; see `is_buddy`.
    buddy_only: bool = False


NUMBER_TYPES: tuple[SkylightNumberEntityDescription, ...] = (
    SkylightNumberEntityDescription(
        key="brightness",
        translation_key="brightness",
        # The device reports 0-255, not a percentage.
        native_min_value=0,
        native_max_value=255,
        native_step=1,
        mode=NumberMode.SLIDER,
        value_fn=lambda device: device.brightness,
    ),
    SkylightNumberEntityDescription(
        key="nightlight_brightness",
        translation_key="nightlight_brightness",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        buddy_only=True,
        value_fn=lambda device: device.nightlight_brightness,
    ),
    SkylightNumberEntityDescription(
        key="sleep_sound_volume",
        translation_key="sleep_sound_volume",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        buddy_only=True,
        value_fn=lambda device: device.sleep_sound_volume,
    ),
    SkylightNumberEntityDescription(
        key="slideshow_speed",
        translation_key="slideshow_speed",
        native_min_value=1,
        native_max_value=60,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda device: device.slideshow_speed,
    ),
)


def _reward_key(frame_id: str, reward: Reward) -> str:
    """Return the unique id a reward's entity will have.

    Shared with `SkylightRewardNumber` rather than recomputed, so that "have I
    already built this one" cannot answer differently from "what is this one
    called".
    """
    name = reward.name or reward.id or ""
    slug = name.strip().lower().replace(" ", "_")
    return f"{frame_id}_{reward.category_id}_reward_{slug}"


class _RewardReconciler:
    """Keeps reward entities in step with rewards as they change on the frame.

    **Why the unique id is the name and not the reward id.**

    It reads as an obvious bug that a reward's entity is not keyed on the
    reward's own id, and the obvious fix is wrong. Verified against the live
    API: redeeming a reward whose `respawn_on_redemption` is set does not mark
    that reward spent. Skylight consumes the resource and mints a replacement
    with a new id and the same name — one probe went in as id 12809772 and came
    back, after a redemption, as 12809773.

    So the reward id does not name a reward. It names one instance of an offer,
    and a household redeeming weekly burns through fifty of them a year. Keyed
    on the id, the entity a child can actually act on would be a different
    entity after every redemption: a new row in the registry each time, and —
    since a disabled or removed entity keeps its entity_id reserved — the live
    one sliding to `..._2`, `..._3`, `..._4`, breaking any automation aimed at
    it. Measured, not assumed.

    What a parent means by "$10 Robux" survives all of that, and it is
    `(profile, name)`. That is what the entity is keyed on.

    **What the reward id is good for.**

    The same experiment gave a clean way to tell the two events apart, because
    they move opposite fields:

        a rename   same id, different name
        a respawn  different id, same name

    So a rename is detectable, and it is worth detecting: keyed on the name, a
    rename would otherwise strand the old entity and mint a new one, losing the
    entity id, its history, and any dashboard pointing at it. Instead the
    registry entry's unique id is migrated and the entity is rebuilt onto it,
    which keeps the entity id it already had.

    The map from reward id to unique id lives only as long as this object, so a
    rename that happens while Home Assistant is stopped is not detected — that
    case still produces a new entity and leaves the old one unavailable. Storing
    it would mean persisting state for a rare case, and the common one is a
    parent editing the chart on a frame that is sitting in the same room as a
    running Home Assistant.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: SkylightDataUpdateCoordinator,
        async_add_entities: AddConfigEntryEntitiesCallback,
    ) -> None:
        """Start with nothing known."""
        self._hass = hass
        self._coordinator = coordinator
        self._async_add_entities = async_add_entities
        #: unique id -> the entity built for it.
        self._entities: dict[str, SkylightRewardNumber] = {}
        #: reward id -> the unique id it currently maps to, which is how a
        #: rename is spotted.
        self._keys: dict[str, str] = {}
        self._pending: asyncio.Task[None] | None = None

    @callback
    def schedule(self) -> None:
        """Reconcile after a refresh, off the coordinator's callback.

        A rename has to remove an entity before adding its replacement, and
        removing one is awaitable, so this cannot all happen inline in a
        `@callback`. One task at a time: a poll that lands mid-rename would
        otherwise see a half-migrated registry.
        """
        if self._pending and not self._pending.done():
            return
        self._pending = self._hass.async_create_task(self._async_reconcile())

    def stop(self) -> None:
        """Drop any reconcile still in flight when the entry unloads."""
        if self._pending and not self._pending.done():
            self._pending.cancel()
        self._pending = None

    @callback
    def reconcile(self) -> None:
        """Add entities for rewards that do not have one, without renames.

        Used at setup, where nothing can have been renamed yet because nothing
        has been seen before.
        """
        self._add(self._missing())

    async def _async_reconcile(self) -> None:
        """Handle renames, then add whatever is genuinely new."""
        for reward_id, frame_id, reward in self._current():
            key = _reward_key(frame_id, reward)
            previous = self._keys.get(reward_id)
            if previous is not None and previous != key:
                await self._async_rename(previous, key)
            self._keys[reward_id] = key
        self._add(self._missing())

    def _current(self) -> list[tuple[str, str, Reward]]:
        """Every reward that can still be redeemed, with its frame."""
        return [
            (reward.id, frame_id, reward)
            for frame_id, frame_data in self._coordinator.data.items()
            for reward in frame_data.available_rewards
            if reward.id
        ]

    def _missing(self) -> list[SkylightRewardNumber]:
        """Entities for rewards that do not have one yet."""
        fresh = []
        for reward_id, frame_id, reward in self._current():
            key = _reward_key(frame_id, reward)
            self._keys.setdefault(reward_id, key)
            if key not in self._entities:
                entity = SkylightRewardNumber(self._coordinator, frame_id, reward)
                self._entities[key] = entity
                fresh.append(entity)
        return fresh

    def _add(self, fresh: list[SkylightRewardNumber]) -> None:
        """Hand new entities to Home Assistant."""
        if fresh:
            self._async_add_entities(fresh)

    async def _async_rename(self, old_key: str, new_key: str) -> None:
        """Move the entity built for `old_key` onto `new_key`.

        The registry entry is repointed rather than replaced, so the entity id
        survives and everything aimed at it keeps working. The entity object
        itself is torn down here and rebuilt by `_missing()` on the new key —
        rebuilding is cleaner than reaching into a live entity to change the
        name it identifies itself by, and it lands on the same entity id because
        the registry already says so.
        """
        registry = er.async_get(self._hass)
        entity_id = registry.async_get_entity_id(Platform.NUMBER, DOMAIN, old_key)

        if entity_id is None or registry.async_get_entity_id(Platform.NUMBER, DOMAIN, new_key):
            # Either there is nothing to move, or the new name already has an
            # entity: a parent can rename one reward onto another's name, and
            # taking over that entity would leave two rewards fighting over it.
            # Nothing is moved and nothing is forgotten — the entity is left
            # tracked, so a later rename onto a free name can still migrate it.
            _LOGGER.debug("Not migrating %s to %s: the destination is taken", old_key, new_key)
            return

        if (entity := self._entities.pop(old_key, None)) is not None:
            await entity.async_remove()
        registry.async_update_entity(entity_id, new_unique_id=new_key)
        _LOGGER.debug("Reward renamed: %s is now %s", old_key, new_key)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkylightConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Skylight numbers from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        SkylightDeviceNumber(coordinator, frame_id, device.id, description)
        for frame_id, frame_data in coordinator.data.items()
        for device in frame_data.devices
        for description in NUMBER_TYPES
        if is_buddy(device) or not description.buddy_only
    )

    # Rewards are added, renamed and redeemed on the frame by a parent while
    # Home Assistant is running, so this platform reconciles on every refresh
    # rather than building once at setup. `_RewardReconciler` explains what
    # identity means here, which is the part that is not obvious.
    reconciler = _RewardReconciler(hass, coordinator, async_add_entities)
    reconciler.reconcile()
    entry.async_on_unload(coordinator.async_add_listener(reconciler.schedule))
    entry.async_on_unload(reconciler.stop)

    entity_platform.async_get_current_platform().async_register_entity_service(
        SERVICE_REDEEM_REWARD,
        None,
        "async_redeem",
        required_features=None,
    )


class SkylightDeviceNumber(SkylightDeviceEntity, NumberEntity):
    """A numeric setting on a physical display."""

    entity_description: SkylightNumberEntityDescription

    def __init__(
        self,
        coordinator: SkylightDataUpdateCoordinator,
        frame_id: str,
        device_id: str,
        description: SkylightNumberEntityDescription,
    ) -> None:
        """Initialize the number."""
        super().__init__(coordinator, frame_id, device_id)
        self.entity_description = description
        self._attr_unique_id = f"device_{device_id}_{description.key}"

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        return self.entity_description.value_fn(self.device)

    async def async_set_native_value(self, value: float) -> None:
        """Set the value."""
        await self.async_set_device(**{self.entity_description.key: int(value)})


class SkylightRewardNumber(SkylightEntity, NumberEntity):
    """What a reward costs, and the thing `skylight.redeem_reward` targets.

    The value is the point price, which Skylight accepts changes to. Redemption
    is a separate action rather than this entity being a button: it spends
    points irreversibly, and an automation may want to choose the reward.
    """

    _attr_translation_key = "reward"
    _attr_native_min_value = 0
    _attr_native_max_value = 1000
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: SkylightDataUpdateCoordinator,
        frame_id: str,
        reward: Reward,
    ) -> None:
        """Initialize the reward."""
        super().__init__(coordinator, frame_id)
        self._category_id = reward.category_id
        self._name = reward.name or reward.id
        # Keyed on the profile and the name, not the reward id. Redeeming a
        # respawning reward mints a new resource, so an id-based key would give
        # a brand new entity after every redemption.
        self._attr_unique_id = _reward_key(frame_id, reward)
        profile = self.frame_data.profiles_by_id.get(self._category_id or "")
        self._attr_translation_placeholders = {
            "profile": (profile.label if profile else None) or "",
            "reward": self._name,
        }

    @property
    def _reward(self) -> Reward | None:
        """Return the live resource behind this reward, if there is one."""
        if self._frame_id not in self.coordinator.data:
            return None
        for reward in self.frame_data.available_rewards:
            if reward.category_id == self._category_id and (reward.name or "") == self._name:
                return reward
        return None

    @property
    def available(self) -> bool:
        """Whether the reward is still offered on the frame."""
        return super().available and self._reward is not None

    @property
    def native_value(self) -> float | None:
        """Return what the reward costs."""
        reward = self._reward
        return reward.point_value if reward else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Say how close the profile is to affording this reward.

        `affordable` alone answers a yes/no question, which is the wrong shape
        for a chore chart: most of the time the interesting fact is how much
        further there is to go. `progress` and `points_needed` are the two ways
        a dashboard usually wants to say that — a bar and a sentence — and both
        are derived here so no template has to divide one entity by another.

        `progress` is capped at 100. Points keep accruing past the price of a
        reward, and a bar that overfills says nothing useful.

        `profile` and `reward` are here so a dashboard can find these entities
        rather than list them. Rewards are created and renamed on the frame, and
        a card naming four entity ids is stale the moment somebody adds a fifth;
        the profile is otherwise only in the entity's name, which leaves a
        template matching on a string that changes when anyone is renamed.
        """
        reward = self._reward
        if reward is None:
            return None
        profile = self.frame_data.profiles_by_id.get(self._category_id or "")
        # Read live rather than reusing the name placeholder set at startup, so
        # renaming a profile on the frame is reflected without a reload.
        identity: dict[str, Any] = {
            "profile": profile.label if profile else None,
            "category_id": self._category_id,
            "reward": self._name,
        }
        points = self.frame_data.points_for(self._category_id or "")
        balance = points.current_point_balance if points else None
        cost = reward.point_value
        if balance is None or cost is None:
            # A profile with no recorded balance is not one with zero points,
            # and every derived answer is unknown rather than pessimistic.
            return identity | {
                "balance": balance,
                "affordable": None,
                "progress": None,
                "points_needed": None,
            }
        return identity | {
            "balance": balance,
            "affordable": balance >= cost,
            # A free reward is already earned rather than a division by zero.
            "progress": 100 if cost <= 0 else round(min(balance / cost, 1) * 100),
            "points_needed": max(cost - balance, 0),
        }

    async def async_set_native_value(self, value: float) -> None:
        """Change what the reward costs."""
        reward = self._require_reward()
        await self.async_write(
            "set_reward_failed",
            self.coordinator.client.update_reward(
                self._frame_id, reward.id, point_value=int(value)
            ),
        )

    async def async_redeem(self) -> None:
        """Redeem the reward.

        No profile is named: a reward belongs to one, and Skylight rejects a
        `category_id` here. It also enforces the balance and refuses a second
        redemption, so nothing is checked locally — a check here would race the
        balance and refuse a redemption moments after a chore was completed.
        """
        reward = self._require_reward()
        await self.async_write(
            "redeem_reward_failed",
            self.coordinator.client.redeem_reward(self._frame_id, reward.id),
        )

    def _require_reward(self) -> Reward:
        reward = self._reward
        if reward is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="reward_gone",
                translation_placeholders={"reward": self._name},
            )
        return reward
