"""Todo platform for the Skylight integration.

Two kinds of to-do list:

* Each Skylight list — grocery or to-do — becomes a to-do entity.
* Each family profile's chore chart becomes one too, since checking off a chore
  is exactly a to-do interaction.
* Each frame's unclaimed chores — "Up for Grabs" — become one more, shared.
"""

from __future__ import annotations

from datetime import date, datetime, time
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


def _chore_due(chore: Chore) -> date | datetime | None:
    """Return when a chore occurrence is due, carrying its time of day.

    A chart with "Brush Teeth" in the morning and again at bedtime produces two
    occurrences with the same summary on the same date. Dropping the time made
    them identical rows in Home Assistant with no way to tell which was which —
    and, worse, hid the field the completion call needs.
    """
    if chore.start is None or not chore.start_time:
        return chore.start
    if (start_time := dt_util.parse_time(chore.start_time)) is None:
        return chore.start
    return datetime.combine(chore.start, start_time, tzinfo=dt_util.get_default_time_zone())


def _instance(chore: Chore) -> dict[str, Any]:
    """Return the fields that name one occurrence of a chore.

    Both are required for a recurring chore and rejected for a one-off one, and
    `instance_time` is required whenever the chore has a time of day — without
    it the API answers `422 instance_time can't be blank`, which is what made
    every timed chore impossible to check off.
    """
    if not chore.recurring:
        return {}
    return {"instance_date": chore.start, "instance_time": chore.start_time}


def _chore_order(chore: Chore) -> tuple[bool, time, bool, int]:
    """Sort key putting a chore chart into the order a day happens in.

    `position` alone is not enough, and on a real chart it is actively wrong.
    Skylight numbers chores from 1 within each group rather than across a
    profile's whole list, so a chart with ten daily chores and four one-off
    assignments carries two sequences that both start at 1 — sorting on the
    number alone interleaves them, and a child reads "Brush Teeth", "Finish
    Summer Math Packet", "Shower".

    Time of day comes first for the same reason: `position` runs across the
    whole day, so morning and bedtime chores alternate. Timed chores lead,
    in clock order, then whatever has no time — an open-ended assignment
    belongs after everything due at a particular moment, not in the middle of
    getting ready for bed.

    `position` still decides the order within a group, which is what makes
    reordering on the frame mean something here.
    """
    start_time = dt_util.parse_time(chore.start_time) if chore.start_time else None
    return (
        start_time is None,
        start_time or time.min,
        chore.position is None,
        chore.position or 0,
    )


def _points_line(chore: Chore) -> str | None:
    """Render a chore's reward points, or nothing if it earns none.

    Deliberately wordless. `⭐ 2` needs no translating and takes almost no width
    on the small wall display these lists tend to end up on, and the star is
    already how the integration names points elsewhere.
    """
    if not chore.reward_points:
        return None
    return f"⭐ {chore.reward_points}"


def _chore_description(chore: Chore) -> str | None:
    """Return the description shown for a chore, points included.

    `TodoItem` has exactly six fields and the websocket serializes that tuple
    and nothing else, so there is no attribute to hang points off — they have to
    ride inside a field that already exists. `description` is the safer of the
    two candidates: the alternative is `summary`, which is the chore's name on
    the frame as well, and generated text has no business in it.

    The points go above whatever the chore already said rather than replacing
    it, and `_user_description()` takes them back off on the way in.
    """
    points = _points_line(chore)
    if points is None:
        return chore.description
    if chore.description:
        return f"{points}\n\n{chore.description}"
    return points


def _user_description(text: str, chore: Chore) -> str | None:
    """Return the part of a description the user actually wrote.

    The frontend hands the whole item back on every edit, so the points line
    returns with it. Without this it would be written into the chore's notes on
    Skylight, and re-prefixed on the next poll, and written again — the star
    would breed.

    Returns None when nothing is left, which the caller treats as "no change".
    Emptying a description is not supported for the same reason nothing else
    here can be cleared: Home Assistant cannot tell an intentional blank from an
    omitted field.
    """
    if (points := _points_line(chore)) and text.startswith(points):
        text = text[len(points) :].lstrip("\n")
    return text or None


def _to_chore_item(chore: Chore) -> TodoItem:
    """Convert a chore occurrence to a Home Assistant to-do item."""
    return TodoItem(
        uid=chore.id,
        summary=chore.summary or "",
        status=(TodoItemStatus.COMPLETED if chore.completed else TodoItemStatus.NEEDS_ACTION),
        due=_chore_due(chore),
        description=_chore_description(chore),
    )


def _changed_fields(item: TodoItem, chore: Chore) -> dict[str, Any]:
    """Return the chore fields an edited to-do item asks to change.

    Every field is guarded on `is not None`, because Home Assistant cannot
    distinguish "left alone" from "cleared": the frontend sends the whole item
    back on every edit, and an absent field arrives as None either way. Erring
    towards leaving a value alone means a description cannot be emptied from
    here, which is the better failure.
    """
    fields: dict[str, Any] = {}
    if item.summary is not None and item.summary != chore.summary:
        fields["summary"] = item.summary
    # Compared against the rendered description, not the stored one, so that a
    # tap carrying the points line straight back is not read as an edit.
    if (
        item.description is not None
        and item.description != _chore_description(chore)
        and (described := _user_description(item.description, chore)) is not None
    ):
        fields["description"] = described
    if item.due is not None and item.due != _chore_due(chore):
        if isinstance(item.due, datetime):
            due = dt_util.as_local(item.due)
            fields["start"] = due.date().isoformat()
            fields["start_time"] = due.strftime("%H:%M")
        else:
            fields["start"] = item.due.isoformat()
    return fields


class SkylightChoreListEntity(NoRecipes, TodoListEntity):
    """One family profile's chores for today, as a to-do list."""

    _attr_translation_key = "chores"
    # SET_DUE_DATETIME_ON_ITEM and SET_DESCRIPTION_ON_ITEM are not optional
    # extras: the frontend sends the whole item back on every edit, so a chore
    # with a time of day arrives as `due_datetime` and one with notes as
    # `description`. Home Assistant rejects a field the entity has not declared
    # before the call reaches this platform, which would make those chores
    # uneditable rather than merely unschedulable.
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
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
        return sorted(self.frame_data.chores_for(self._category_id), key=_chore_order)

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

        if item.status is not None and item.status != _status_of(chore):
            action = (
                self.coordinator.client.complete_chore
                if item.status == TodoItemStatus.COMPLETED
                else self.coordinator.client.uncomplete_chore
            )
            await self.async_write(
                "update_chore_failed",
                action(self._frame_id, chore.chore_id, **_instance(chore)),
            )

        if fields := _changed_fields(item, chore):
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
        | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
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
        # Sorted the same way as a profile's chart. Unsorted, this list came out
        # in `/chores/all` bucket order — late, today, today_timed, any_day —
        # which ignores `position` entirely and puts the one chore with a time
        # of day after everything that has none.
        return sorted(self.frame_data.unassigned_chores, key=_chore_order)

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
                        category_id=self._claiming_category(),
                        **_instance(chore),
                    ),
                )
            else:
                await self.async_write(
                    "update_chore_failed",
                    self.coordinator.client.uncomplete_chore(
                        self._frame_id, chore.chore_id, **_instance(chore)
                    ),
                )

        if fields := _changed_fields(item, chore):
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
