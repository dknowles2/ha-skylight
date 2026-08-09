# Building a dashboard

Worked examples for laying the integration's entities out. Everything here is plain
Lovelace YAML using built-in cards — paste it into a dashboard's raw configuration editor
and change the entity ids to yours.

**Do not trust the entity ids below — look yours up.** They are written as though the
frame's name and the profile's decide them, so `The Knowles` with a child called `Jacob`
gives `sensor.the_knowles_jacob_reward_points`. That is only where an id *starts*. Home
Assistant fixes an entity id the first time the entity is registered and never revises it,
so renaming a frame, or an entity added under an older version of this integration, leaves
an id that no longer matches the pattern — one real install has
`todo.kitchen_the_knowles_up_for_grabs` sitting alongside `todo.the_knowles_jacob_chores`.

Copy each id from **Developer tools → States**, filtering on `skylight`. A dashboard
pointed at an id that does not exist renders an *Entity not found* card rather than an
error, which is easy to read as the integration being broken.

## The family view

One column per person, plus the household calendar. This is the layout the frame itself is
closest to.

```yaml
type: sections
title: Family
sections:
  - type: grid
    cards:
      - type: heading
        heading: Jacob
      - type: todo-list
        entity: todo.the_knowles_jacob_chores
      - type: entities
        entities:
          - entity: sensor.the_knowles_jacob_chores_due
            name: Still to do
          - entity: sensor.the_knowles_jacob_reward_points
            name: Stars

  - type: grid
    cards:
      - type: heading
        heading: Everyone
      - type: todo-list
        entity: todo.the_knowles_up_for_grabs
      - type: todo-list
        entity: todo.the_knowles_grocery_list

  - type: grid
    cards:
      - type: heading
        heading: Calendar
      - type: calendar
        entities:
          - calendar.the_knowles_calendar
        initial_view: listWeek
```

Checking off an *Up for Grabs* chore credits whoever ticked the box, so that card only
works properly for someone signed in as themselves — see
[Up for Grabs chores](../README.md#up-for-grabs-chores).

## A child's chore screen

A wall display in a child's room, signed in as that child, is the case the *Up for Grabs*
list was built for: Home Assistant knows who tapped, so the chore is credited to them
without anyone choosing a name first. A frame does not do this — everyone shares one
screen, so claiming a chore there means picking yourself out of a list.

The layout below is for a small landscape display — it was written against an Echo Show 5
(960×480), and anything of roughly that shape will do. Two columns, no keyboard, and rows
big enough to hit with a thumb.

```yaml
views:
  - title: Chores
    path: chores
    type: sections
    max_columns: 2
    sections:
      - type: grid
        cards:
          - type: heading
            heading: Jacob's chores
            icon: mdi:account-child-circle
            badges:
              - type: entity
                entity: sensor.the_knowles_jacob_reward_points
                icon: mdi:star

          # Only visible once the list is empty.
          - type: conditional
            conditions:
              - condition: state
                entity: todo.the_knowles_jacob_chores
                state: "0"
            card:
              type: markdown
              text_only: true
              content: "# 🎉 All done!"

          - type: todo-list
            entity: todo.the_knowles_jacob_chores
            hide_create: true
            display_order: none
            item_tap_action: toggle

      - type: grid
        cards:
          # Note the id: this list predates a rename on the install it came
          # from, so it does not match the pattern the others follow. Yours may
          # or may not — look it up.
          - type: heading
            heading: Up for grabs
            icon: mdi:hand-back-right-outline
            badges:
              - type: entity
                entity: todo.kitchen_the_knowles_up_for_grabs
                icon: mdi:playlist-check
                show_state: true
                show_icon: true

          - type: todo-list
            entity: todo.kitchen_the_knowles_up_for_grabs
            hide_create: true
            display_order: none
            item_tap_action: toggle
```

Three of those card options are doing the work:

`item_tap_action: toggle` is the important one. By default tapping a row opens the edit
dialog and only the checkbox itself checks the item off — a target a few millimetres wide.
With `toggle`, the whole row is the button.

`hide_create: true` removes the *Add item* field. Nothing on this screen should summon a
keyboard, and a chore chart a child can add to is not a chore chart.

`display_order: none` keeps the order the chores are in on the frame, which is the order
someone arranged them in deliberately. Any other value sorts the card and quietly discards
that.

### Making the rows bigger

The built-in card sizes itself for a phone held at arm's length rather than a screen
across the room. That is styling, and styling means
[card-mod](https://github.com/thomasloven/lovelace-card-mod). Add this to each
`todo-list` card:

```yaml
    card_mod:
      style: |
        ha-check-list-item {
          min-height: 60px;
        }
        .summary {
          font-size: 22px;
          font-weight: 500;
        }
        .due {
          font-size: 16px;
        }
        /* Sorting and reordering are not this screen's job. */
        ha-dropdown {
          display: none !important;
        }
```

Leave the due line visible even though it reads "today" on most rows. A chart with the
same chore morning and night — "Brush Teeth" twice — produces two rows with the same
name on the same day, and the time is the only thing that distinguishes them.

`card_mod` is an unknown key to Home Assistant if card-mod is not installed, so the
dashboard still renders — just at the default size. Everything above works without it.

### Getting the screen out of the way

At 960px wide Home Assistant is above its narrow breakpoint, so it docks the sidebar and
draws a header, and between them they take a third of the display. On a wall panel neither
is wanted. [kiosk-mode](https://github.com/NemesisRE/kiosk-mode) removes both — put this at
the top of the same raw configuration:

```yaml
kiosk_mode:
  hide_header: true
  hide_sidebar: true
```

The rest is the device. Sign the browser in as the child's own Home Assistant user —
that login is what makes an *Up for Grabs* chore land on the right chart, and a shared or
admin login silently credits the wrong person. Then point it at
`/lovelace-chores/chores` and let it stay there.

## Rewards

Each reward is a `number` whose value is its point cost, with `balance` and `affordable`
attributes. Redeeming is an action, so a button card with a `tap_action` is how it goes on
a dashboard:

```yaml
type: entities
title: Jacob's rewards
entities:
  - entity: number.the_knowles_jacob_10_robux
    name: $10 Robux
  - type: button
    name: Redeem $10 Robux
    icon: mdi:gift-open-outline
    tap_action:
      action: perform-action
      perform_action: skylight.redeem_reward
      target:
        entity_id: number.the_knowles_jacob_10_robux
```

To grey the button out when the balance will not cover it, use a `conditional` card on the
reward's `affordable` attribute:

```yaml
type: conditional
conditions:
  - condition: state
    entity: number.the_knowles_jacob_10_robux
    attribute: affordable
    state: true
card:
  type: button
  name: Redeem $10 Robux
  tap_action:
    action: perform-action
    perform_action: skylight.redeem_reward
    target:
      entity_id: number.the_knowles_jacob_10_robux
```

## Awarding stars from a dashboard

```yaml
type: entities
title: Stars
entities:
  - entity: sensor.the_knowles_jacob_reward_points
    name: Jacob
  - type: button
    name: Give Jacob a star
    icon: mdi:star-plus-outline
    tap_action:
      action: perform-action
      perform_action: skylight.award_points
      target:
        entity_id: sensor.the_knowles_jacob_reward_points
      data:
        points: 1
```

`skylight.deduct_points` takes the same shape. Skylight does not stop at zero, so a
mis-tap can leave a negative balance — worth keeping the deduct button off a wall tablet.

## Sending a recipe to the grocery list

```yaml
type: button
name: Taco night
icon: mdi:chef-hat
tap_action:
  action: perform-action
  perform_action: skylight.add_recipe
  target:
    entity_id: todo.the_knowles_grocery_list
  data:
    recipe: Taco Night
```

The ingredients arrive a few seconds after the tap, not instantly — Skylight parses them
out of the recipe on its own servers.

## Reacting to the frame

The `event` entities carry what happened in their attributes, which a markdown card can
read directly:

```yaml
type: markdown
content: >-
  {% set e = states.event.the_knowles_chore_completed %}
  {% if e.state not in ['unknown', 'unavailable'] %}
    **{{ e.attributes.profile }}** finished *{{ e.attributes.chore }}*
    {{ relative_time(e.attributes.completed_at | as_datetime) }} ago
  {% else %}
    Nothing completed yet today.
  {% endif %}
```

For notifications rather than display, trigger an automation off the same entity — there
is an example in the [README](../README.md#reacting-to-what-happens-on-the-frame).

## A note on what not to build

Chore counts are also available as `sensor.<frame>_<profile>_chores_due` and
`_chores_completed`, which are useful in automations and templates but say less than the
to-do card does. Prefer the card where there is room for it.
