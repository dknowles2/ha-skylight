# Building a dashboard

Worked examples for laying the integration's entities out. Everything here is plain
Lovelace YAML using built-in cards — paste it into a dashboard's raw configuration editor
and change the entity ids to yours.

Entity ids follow the frame's name and the profile's, so `The Knowles` with a child called
`Jacob` gives `sensor.the_knowles_jacob_reward_points`. Check yours under **Developer
tools → States** before copying anything wholesale.

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
cards:
  - type: button
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
