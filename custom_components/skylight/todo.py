"""Todo platform for the Skylight integration.

Each Skylight list — grocery or to-do — becomes a Home Assistant to-do list,
with items editable from either side.
"""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

from homeassistant.components.todo import TodoItem, TodoListEntity
from homeassistant.components.todo.const import TodoItemStatus, TodoListEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyskylight.exceptions import SkylightError
from pyskylight.models import ListItem, ListItemStatus, SkylightList

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
        SkylightTodoListEntity(coordinator, frame_id, skylight_list.id)
        for frame_id, frame_data in coordinator.data.items()
        for skylight_list in frame_data.lists
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

    async def _write(self, action: str, coro: Coroutine[Any, Any, object]) -> None:
        """Run a write and refresh, turning API errors into HA errors."""
        try:
            await coro
        except SkylightError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=action,
                translation_placeholders={"error": str(err)},
            ) from err
        await self.coordinator.async_request_refresh()

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Add an item to the list."""
        await self._write(
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
        await self._write(
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
            await self._write(
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
        await self._write(
            "move_item_failed",
            self.coordinator.client.move_list_item(
                self._frame_id, self._list_id, uid, position=position
            ),
        )
