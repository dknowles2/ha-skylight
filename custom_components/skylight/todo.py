"""Todo platform for the Skylight integration.

Two kinds of to-do list:

* Each Skylight list — grocery or to-do — becomes a to-do entity.
* Each family profile's chore chart becomes one too, since checking off a chore
  is exactly a to-do interaction.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.todo import TodoItem, TodoListEntity
from homeassistant.components.todo.const import TodoItemStatus, TodoListEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
from pyskylight.models import ApplyTo, Chore, ListItem, ListItemStatus, SkylightList

from .const import DOMAIN
from .coordinator import SkylightConfigEntry, SkylightDataUpdateCoordinator
from .entity import SkylightEntity

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
                for category in frame_data.categories
            ),
        ]
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


def _to_chore_item(chore: Chore) -> TodoItem:
    """Convert a chore occurrence to a Home Assistant to-do item."""
    return TodoItem(
        uid=chore.id,
        summary=chore.summary or "",
        status=(TodoItemStatus.COMPLETED if chore.completed else TodoItemStatus.NEEDS_ACTION),
        due=chore.start,
        description=chore.description,
    )


class SkylightChoreListEntity(SkylightEntity, TodoListEntity):
    """One family profile's chores for today, as a to-do list."""

    _attr_translation_key = "chores"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
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
        category = coordinator.data[frame_id].categories_by_id[category_id]
        self._attr_translation_placeholders = {"profile": category.label or category_id}

    @property
    def available(self) -> bool:
        """Whether the profile still exists on the frame."""
        return super().available and self._category_id in self.frame_data.categories_by_id

    @property
    def todo_items(self) -> list[TodoItem] | None:
        """Return this profile's chores."""
        if not self.available:
            return None
        return [_to_chore_item(chore) for chore in self._chores]

    @property
    def _chores(self) -> list[Chore]:
        return self.frame_data.chores_for(self._category_id)

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


def _status_of(chore: Chore) -> TodoItemStatus:
    """Return the to-do status a chore currently has."""
    return TodoItemStatus.COMPLETED if chore.completed else TodoItemStatus.NEEDS_ACTION
