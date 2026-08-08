"""Todo platform for the Skylight integration.

Two kinds of to-do list:

* Each Skylight list — grocery or to-do — becomes a to-do entity.
* Each family profile's chore chart becomes one too, since checking off a chore
  is exactly a to-do interaction.
* Each frame's unclaimed chores — "Up for Grabs" — become one more, shared.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import voluptuous as vol
from homeassistant.components.todo import TodoItem, TodoListEntity
from homeassistant.components.todo.const import TodoItemStatus, TodoListEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util
from pyskylight.models import ApplyTo, Chore, ListItem, ListItemStatus, SkylightList

from .const import (
    ATTR_RECIPE,
    DOMAIN,
    RECIPE_INGREDIENT_DELAY,
    SERVICE_ADD_RECIPE,
)
from .coordinator import SkylightConfigEntry, SkylightDataUpdateCoordinator
from .entity import SkylightEntity
from .profiles import category_for_user

STATUS_TO_HA = {
    ListItemStatus.COMPLETED: TodoItemStatus.COMPLETED,
    ListItemStatus.PENDING: TodoItemStatus.NEEDS_ACTION,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkylightConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Skylight to-do lists from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            *(
                SkylightTodoListEntity(coordinator, frame_id, skylight_list.id)
                for frame_id, frame_data in coordinator.data.items()
                for skylight_list in frame_data.lists
            ),
            *(
                SkylightChoreListEntity(coordinator, frame_id, category.id)
                for frame_id, frame_data in coordinator.data.items()
                for category in frame_data.profiles
            ),
            *(SkylightUpForGrabsEntity(coordinator, frame_id) for frame_id in coordinator.data),
        ]
    )

    # Registered on the grocery list rather than as a plain service: the
    # ingredients land on one specific list, and targeting it is how a user
    # says which frame they mean.
    entity_platform.async_get_current_platform().async_register_entity_service(
        SERVICE_ADD_RECIPE,
        {vol.Required(ATTR_RECIPE): cv.string},
        "async_add_recipe",
    )


def _to_todo_item(item: ListItem) -> TodoItem:
    """Convert a Skylight list item to a Home Assistant to-do item."""
    return TodoItem(
        uid=item.id,
        summary=item.label or "",
        status=STATUS_TO_HA.get(item.status or "", TodoItemStatus.NEEDS_ACTION),
    )


class SkylightTodoListEntity(SkylightEntity, TodoListEntity):
    """A Skylight grocery or to-do list."""

    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.MOVE_TODO_ITEM
    )

    def __init__(
        self,
        coordinator: SkylightDataUpdateCoordinator,
        frame_id: str,
        list_id: str,
    ) -> None:
        """Initialize the to-do list."""
        super().__init__(coordinator, frame_id)
        self._list_id = list_id
        self._attr_unique_id = f"{frame_id}_{list_id}"
        # Lists are named by the user, so the name is data rather than a
        # translatable string.
        self._attr_name = self._skylight_list.label

    @property
    def _skylight_list(self) -> SkylightList:
        return self.frame_data.lists_by_id[self._list_id]

    @property
    def available(self) -> bool:
        """Whether the list still exists on the frame."""
        return super().available and self._list_id in self.frame_data.lists_by_id

    @property
    def todo_items(self) -> list[TodoItem] | None:
        """Return the items on this list."""
        if not self.available:
            return None
        return [_to_todo_item(item) for item in self._skylight_list.items]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Add an item to the list."""
        await self.async_write(
            "create_item_failed",
            self.coordinator.client.create_list_item(
                self._frame_id, self._list_id, item.summary or ""
            ),
        )

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Rename an item or check it off."""
        fields: dict[str, str] = {}
        if item.summary is not None:
            fields["label"] = item.summary
        if item.status is not None:
            fields["status"] = (
                ListItemStatus.COMPLETED
                if item.status == TodoItemStatus.COMPLETED
                else ListItemStatus.PENDING
            )
        await self.async_write(
            "update_item_failed",
            self.coordinator.client.update_list_item(
                self._frame_id, self._list_id, item.uid or "", **fields
            ),
        )

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete items from the list.

        Deleted one at a time rather than through the bulk endpoint: the
        single-item delete is the one verified against the live API.
        """
        for uid in uids:
            await self.async_write(
                "delete_item_failed",
                self.coordinator.client.delete_list_item(self._frame_id, self._list_id, uid),
            )

    async def async_add_recipe(self, recipe: str) -> None:
        """Push a recipe's ingredients onto the grocery list.

        Skylight parses the ingredients out of the recipe's free-text
        description on its own servers, so nothing is read or assembled here.
        Two consequences shape this method.

        First, the destination is not a choice: the ingredients always land on
        the list flagged `default_grocery_list`, verified against a frame with
        two shopping lists. Targeting any other list is refused rather than
        silently redirected — filling a different list than the one named would
        be the worst kind of surprise.

        Second, the items do not exist when the call returns. A second refresh
        is scheduled to pick them up once Skylight has finished.
        """
        frame_data = self.frame_data
        if self._skylight_list is not frame_data.default_grocery_list:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_the_default_grocery_list",
                translation_placeholders={
                    "entity_id": self.entity_id,
                    "grocery_list": (
                        default.label or default.id
                        if (default := frame_data.default_grocery_list)
                        else "none"
                    ),
                },
            )

        matches = frame_data.recipes_named(recipe)
        if not matches:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unknown_recipe",
                translation_placeholders={"recipe": recipe},
            )
        if len(matches) > 1:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="ambiguous_recipe",
                translation_placeholders={"recipe": recipe, "count": str(len(matches))},
            )

        await self.async_write(
            "add_recipe_failed",
            self.coordinator.client.add_recipe_to_grocery_list(self._frame_id, matches[0].id),
        )
        # Cancelled if the entity goes away first: a timer outliving its entity
        # is a leak, and Home Assistant's own test harness fails on one.
        self.async_on_remove(
            async_call_later(
                self.hass, RECIPE_INGREDIENT_DELAY, self._async_refresh_for_ingredients
            )
        )

    async def _async_refresh_for_ingredients(self, _now: datetime) -> None:
        """Poll again once Skylight has had time to add the ingredients."""
        await self.coordinator.async_request_refresh()

    async def async_move_todo_item(self, uid: str, previous_uid: str | None = None) -> None:
        """Reorder an item.

        Home Assistant expresses a move as "put this after that one"; Skylight
        wants a position index, so translate between the two using the ordering
        we last polled.
        """
        order = [item.id for item in self._skylight_list.items]
        if uid not in order:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="move_item_failed",
                translation_placeholders={"error": f"unknown item {uid}"},
            )
        order.remove(uid)
        position = 0 if previous_uid is None else order.index(previous_uid) + 1
        await self.async_write(
            "move_item_failed",
            self.coordinator.client.move_list_item(
                self._frame_id, self._list_id, uid, position=position
            ),
        )


class NoRecipes(SkylightEntity):
    """Refuses `add_recipe` with an explanation rather than an AttributeError.

    The action is registered against the whole to-do platform, so Home Assistant
    will happily aim it at a chore list. Without this, that fails deep inside
    the service call with no useful message.
    """

    async def async_add_recipe(self, recipe: str) -> None:
        """Explain that ingredients only go on a grocery list."""
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="not_a_grocery_list",
            translation_placeholders={"entity_id": self.entity_id},
        )


def _to_chore_item(chore: Chore) -> TodoItem:
    """Convert a chore occurrence to a Home Assistant to-do item."""
    return TodoItem(
        uid=chore.id,
        summary=chore.summary or "",
        status=(TodoItemStatus.COMPLETED if chore.completed else TodoItemStatus.NEEDS_ACTION),
        due=chore.start,
        description=chore.description,
    )


class SkylightChoreListEntity(NoRecipes, TodoListEntity):
    """One family profile's chores for today, as a to-do list."""

    _attr_translation_key = "chores"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.MOVE_TODO_ITEM
    )

    def __init__(
        self,
        coordinator: SkylightDataUpdateCoordinator,
        frame_id: str,
        category_id: str,
    ) -> None:
        """Initialize the chore list."""
        super().__init__(coordinator, frame_id)
        self._category_id = category_id
        self._attr_unique_id = f"{frame_id}_{category_id}_chores"
        category = coordinator.data[frame_id].profiles_by_id[category_id]
        self._attr_translation_placeholders = {"profile": category.label or category_id}

    @property
    def available(self) -> bool:
        """Whether the profile still exists on the frame."""
        return super().available and self._category_id in self.frame_data.profiles_by_id

    @property
    def todo_items(self) -> list[TodoItem] | None:
        """Return this profile's chores."""
        if not self.available:
            return None
        return [_to_chore_item(chore) for chore in self._chores]

    @property
    def _chores(self) -> list[Chore]:
        # Sorted, because the response is not: reordering a chore changes its
        # `position` and leaves the response order alone, so anything reading
        # the list as it arrives shows the old order forever.
        return sorted(
            self.frame_data.chores_for(self._category_id),
            key=lambda chore: (chore.position is None, chore.position or 0),
        )

    def _find(self, uid: str) -> Chore:
        """Look up a chore occurrence by its uid."""
        for chore in self._chores:
            if chore.id == uid:
                return chore
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="update_chore_failed",
            translation_placeholders={"error": f"unknown chore {uid}"},
        )

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Add a chore for this profile.

        Home Assistant only offers a summary and a due date, so the chore is
        one-off and unassigned beyond this profile. Recurrence is set up on the
        frame itself.
        """
        await self.async_write(
            "create_chore_failed",
            self.coordinator.client.create_chore(
                self._frame_id,
                item.summary or "",
                self._category_id,
                start=item.due or dt_util.now().date(),
            ),
        )

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Complete, reopen, rename, or reschedule a chore."""
        chore = self._find(item.uid or "")
        if chore.chore_id is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="update_chore_failed",
                translation_placeholders={"error": f"chore {item.uid} has no id"},
            )

        # Recurring chores are addressed per occurrence; one-off chores must not
        # carry an instance date at all.
        instance_date = chore.start if chore.recurring else None

        if item.status is not None and item.status != _status_of(chore):
            action = (
                self.coordinator.client.complete_chore
                if item.status == TodoItemStatus.COMPLETED
                else self.coordinator.client.uncomplete_chore
            )
            await self.async_write(
                "update_chore_failed",
                action(self._frame_id, chore.chore_id, instance_date=instance_date),
            )

        fields: dict[str, Any] = {}
        if item.summary is not None and item.summary != chore.summary:
            fields["summary"] = item.summary
        if item.due is not None and item.due != chore.start:
            fields["start"] = item.due.isoformat()
        if fields:
            await self.async_write(
                "update_chore_failed",
                self.coordinator.client.update_chore(self._frame_id, chore.chore_id, **fields),
            )

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete chores."""
        for uid in uids:
            chore = self._find(uid)
            if chore.chore_id is None:
                continue
            # apply_to is required for a recurring chore and rejected for a
            # one-off one.
            apply_to = ApplyTo.ALL if chore.recurring else None
            await self.async_write(
                "delete_chore_failed",
                self.coordinator.client.delete_chore(
                    self._frame_id, chore.chore_id, apply_to=apply_to
                ),
            )

    async def async_move_todo_item(self, uid: str, previous_uid: str | None = None) -> None:
        """Reorder a chore on this profile's chart.

        Skylight takes a neighbour rather than an index — `{"position":
        {"before": id}}` or `{"after": id}`, and every scalar form is rejected
        with `422 Position is required`. Home Assistant says "put this after
        that one", which maps straight onto `after`; moving to the top has no
        `previous_uid`, so it becomes `before` the chore currently at the front.

        Both ids are chore ids, not occurrence ids: the occurrence is a
        particular day of a recurring chore, and the order belongs to the chore.
        """
        chore = self._find(uid)
        if chore.chore_id is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="move_chore_failed",
                translation_placeholders={"error": f"chore {uid} has no addressable id"},
            )

        if previous_uid is not None:
            neighbour = self._find(previous_uid).chore_id
            position = {"after": neighbour}
        else:
            others = [other for other in self._chores if other.id != uid]
            if not others:
                # Nothing to sit in front of, so nothing to do.
                return
            position = {"before": others[0].chore_id}

        if position["after" if previous_uid is not None else "before"] is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="move_chore_failed",
                translation_placeholders={"error": "the neighbouring chore has no addressable id"},
            )

        await self.async_write(
            "move_chore_failed",
            self.coordinator.client.move_chore(self._frame_id, chore.chore_id, **position),
        )


def _status_of(chore: Chore) -> TodoItemStatus:
    """Return the to-do status a chore currently has."""
    return TodoItemStatus.COMPLETED if chore.completed else TodoItemStatus.NEEDS_ACTION


class SkylightUpForGrabsEntity(NoRecipes, TodoListEntity):
    """A frame's unclaimed chores — what the Skylight app calls "Up for Grabs".

    These belong to nobody, so completing one has to say who claimed it. Home
    Assistant knows which of its own users pressed the button, and the options
    flow maps that user's person entity onto a Skylight profile.
    """

    _attr_translation_key = "up_for_grabs"
    # No CREATE: the API refuses to create a chore without a category
    # (`422 Category is required.`), so one cannot be born up for grabs.
    _attr_supported_features = (
        TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
    )

    def __init__(self, coordinator: SkylightDataUpdateCoordinator, frame_id: str) -> None:
        """Initialize the list."""
        super().__init__(coordinator, frame_id)
        self._attr_unique_id = f"{frame_id}_up_for_grabs"

    @property
    def todo_items(self) -> list[TodoItem] | None:
        """Return the frame's unclaimed chores."""
        if not self.available:
            return None
        return [_to_chore_item(chore) for chore in self._chores]

    @property
    def _chores(self) -> list[Chore]:
        return self.frame_data.unassigned_chores

    def _find(self, uid: str) -> Chore:
        """Look up an unclaimed chore by its uid."""
        for chore in self._chores:
            if chore.id == uid:
                return chore
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="update_chore_failed",
            translation_placeholders={"error": f"unknown chore {uid}"},
        )

    def _claiming_category(self) -> str:
        """Return the Skylight profile for whoever is acting, or refuse.

        Refusing is deliberate. The alternative — falling back to some default
        profile — would quietly credit one child for another's chore, and a
        chore chart nobody trusts is worse than one that occasionally says no.
        """
        user_id = self._context.user_id if self._context else None
        category_id = category_for_user(self.hass, self.coordinator.config_entry, user_id)
        if category_id is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="no_profile_mapped",
            )
        return category_id

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Claim, release, rename, or reschedule an unclaimed chore."""
        chore = self._find(item.uid or "")
        if chore.chore_id is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="update_chore_failed",
                translation_placeholders={"error": f"chore {item.uid} has no id"},
            )

        instance_date = chore.start if chore.recurring else None

        if item.status is not None and item.status != _status_of(chore):
            if item.status == TodoItemStatus.COMPLETED:
                # category_id is what the API calls the claimant, and it is
                # required here — exactly the opposite of an assigned chore,
                # which rejects it.
                await self.async_write(
                    "update_chore_failed",
                    self.coordinator.client.complete_chore(
                        self._frame_id,
                        chore.chore_id,
                        instance_date=instance_date,
                        category_id=self._claiming_category(),
                    ),
                )
            else:
                await self.async_write(
                    "update_chore_failed",
                    self.coordinator.client.uncomplete_chore(
                        self._frame_id, chore.chore_id, instance_date=instance_date
                    ),
                )

        fields: dict[str, Any] = {}
        if item.summary is not None and item.summary != chore.summary:
            fields["summary"] = item.summary
        if item.due is not None and item.due != chore.start:
            fields["start"] = item.due.isoformat()
        if fields:
            await self.async_write(
                "update_chore_failed",
                self.coordinator.client.update_chore(self._frame_id, chore.chore_id, **fields),
            )

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete unclaimed chores."""
        for uid in uids:
            chore = self._find(uid)
            if chore.chore_id is None:
                continue
            apply_to = ApplyTo.ALL if chore.recurring else None
            await self.async_write(
                "delete_chore_failed",
                self.coordinator.client.delete_chore(
                    self._frame_id, chore.chore_id, apply_to=apply_to
                ),
            )
