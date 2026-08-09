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

Each reward is a `number` whose value is its point cost, with four attributes:

| attribute | |
| --- | --- |
| `balance` | the profile's current points |
| `affordable` | whether the balance covers the cost |
| `progress` | how far towards it, 0–100, capped |
| `points_needed` | how many more, 0 once affordable |

`progress` and `points_needed` are derived in the integration rather than left to a
template, because a dashboard cannot divide one entity by another. All four read `unknown`
for a profile with no recorded balance, which is not the same as a profile with zero
points.

Redeeming is an action, so a button card with a `tap_action` is how it goes on a
dashboard:

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

### Rewards on a child's screen

What a child wants from this is not a list of prices — it is how close they are.

**Do not list the rewards.** They are created and renamed on the frame, so a card naming
four entity ids is wrong the moment somebody adds a fifth, and silently: a card pointed at
a reward that no longer exists just stops showing it. Every reward carries `profile`, so a
template can find them instead:

```yaml
type: markdown
text_only: true
content: >-
  {%- set rewards = states.number
       | selectattr('attributes.profile', 'defined')
       | selectattr('attributes.profile', 'eq', 'Jacob')
       | selectattr('attributes.points_needed', 'defined')
       | list -%}
  {%- set rows = [] -%}
  {%- for reward in rewards -%}
    {%- set _ = rows.append([
         reward.state | int(0),
         reward.attributes.reward,
         reward.attributes.points_needed,
         reward.attributes.progress ]) -%}
  {%- endfor -%}
  {% for cost, name, needed, progress in rows | sort %}
  {%- set filled = ((progress | int(0)) / 10) | round | int -%}
  **{{ name }}** — {{ cost }} ⭐
  {{ '★' * filled }}{{ '☆' * (10 - filled) }}
  {% if needed == 0 %}Ready!{% else %}{{ needed }} more{% endif %}
  {% endfor %}
```

`| sort` on a list whose first element is the cost puts the cheapest first, so the nearest
reward is at the top and the bar he is filling is the one he sees. Sorting the entities
directly would not work: `state` is a string, and `"10"` sorts before `"5"`.

Change `'Jacob'` to the profile you want. The match is on the profile's label as the frame
reports it, which follows a rename on the frame without a reload — unlike the entity id,
which never changes once assigned.

The buttons need [auto-entities](https://github.com/thomasloven/lovelace-auto-entities),
because no built-in card builds cards from data. With it the whole section stays
dynamic — a button for each affordable reward, and none at all when he cannot afford
anything:

```yaml
type: custom:auto-entities
card:
  type: grid
  columns: 2
  square: false
card_param: cards
# Nothing affordable renders no cards at all, so without this the section leaves
# an empty grid behind on the days he has not earned anything yet.
show_empty: false
filter:
  template: |
    {% for reward in states.number
         | selectattr('attributes.profile', 'defined')
         | selectattr('attributes.profile', 'eq', 'Jacob')
         | selectattr('attributes.affordable', 'eq', true) %}
      {{ { 'type': 'button',
           'name': reward.attributes.reward,
           'icon': 'mdi:gift-open-outline',
           'tap_action': { 'action': 'perform-action',
                           'perform_action': 'skylight.redeem_reward',
                           'target': { 'entity_id': reward.entity_id } } } }},
    {% endfor %}
```

auto-entities parses the template's output as YAML, so what the template emits has to be a
comma-separated list of card configs — hence the trailing comma inside the loop, which
looks like a typo and is not.

Without that dependency, one `conditional` card per reward, each appearing only when it
can succeed. Hard-coded, so a reward added on the frame will not get a button until
someone adds one:

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
  icon: mdi:gift-open-outline
  show_state: false
  tap_action:
    action: perform-action
    perform_action: skylight.redeem_reward
    target:
      entity_id: number.the_knowles_jacob_10_robux
```

A button that is absent until it works beats one that is greyed out: there is nothing to
tap hopefully, and nothing that fails with an error a child cannot act on.

Redeeming is not confirmed and cannot be undone. On a screen a child uses unsupervised
that is the point — see the automations below for how to hear about it — but on a shared
tablet, `confirmation` on the `tap_action` is worth adding.

## Hearing about rewards

Two blueprints, in
[`blueprints/automation/skylight/`](../blueprints/automation/skylight). Import either by
pasting its URL into **Settings → Automations & scenes → Blueprints → Import blueprint**:

| | |
| --- | --- |
| [Reward within reach](https://github.com/dknowles2/ha-skylight/blob/main/blueprints/automation/skylight/reward_within_reach.yaml) | someone has earned enough points to redeem something |
| [Reward redeemed](https://github.com/dknowles2/ha-skylight/blob/main/blueprints/automation/skylight/reward_redeemed.yaml) | someone redeemed one |

They are not installed with the integration. Home Assistant only discovers blueprints under
your own `blueprints/` folder — an integration cannot ship them — and the release zip
contains just the component, so importing by URL is the route.

Both guard against firing on restart, which the hand-written versions below do not. A
restart re-creates every entity with no previous state, so every reward already in reach
would announce itself as though it had just been earned.

Both also take the notification as an input rather than assuming one, so a mobile
notification, a TTS announcement on the kitchen speaker, or a light flash are all the same
blueprint. The variables they expose are listed in each input's description.

### Writing them by hand instead

The same two automations without the blueprints, if you would rather own the YAML. Neither
has the restart guard.

**When he can afford something new.** `affordable` flipping to true is the trigger, one per
reward, so each reward announces itself once as it comes into reach rather than every poll:

```yaml
alias: Jacob can afford a reward
triggers:
  - trigger: state
    entity_id:
      - number.the_knowles_jacob_10_minutes_extra_youtube_shorts
      - number.the_knowles_jacob_10_robux
      - number.the_knowles_jacob_30_minutes_extra_ipad_time
      - number.the_knowles_jacob_five_guys_dinner
    attribute: affordable
    to: true
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: Jacob has earned a reward
      message: >-
        {{ trigger.to_state.attributes.friendly_name }} is now within reach —
        {{ trigger.to_state.attributes.balance }} points.
mode: queued
```

Every reward he can already afford fires this the first time Home Assistant restarts, and
each one fires again whenever a redemption drops his balance below the price and chores
bring it back. Both are correct, and both are noisier than they sound with four rewards.
Narrowing `entity_id` to the one or two that matter is the usual fix.

**When he redeems one.** This needs no reward entity at all — the frame reports every
redemption, whoever made it and wherever it happened:

```yaml
alias: Jacob redeemed a reward
triggers:
  - trigger: state
    entity_id: event.the_knowles_reward_redeemed
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: Reward redeemed
      message: >-
        {{ state_attr('event.the_knowles_reward_redeemed', 'profile') }} redeemed
        {{ state_attr('event.the_knowles_reward_redeemed', 'reward') }}
        for {{ state_attr('event.the_knowles_reward_redeemed', 'point_value') }} points.
```

Because this watches the frame rather than the dashboard, it fires for a redemption made on
the frame itself, from the Skylight app, or from this integration — which is what makes it
the one to rely on. Filter on the `profile` attribute if more than one person has rewards.

Skylight has no notion of *requesting* a reward, only of redeeming one: there is no pending
or approval state anywhere in its API, and every endpoint that might hold one returns 404.
A request-and-approve flow would therefore be a Home Assistant invention, and one a child
could sidestep by walking to the frame.

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

## Progress meters

Three percentage sensors per profile, each carrying `completed`, `due` and `total` as
attributes:

| | covers |
| --- | --- |
| `sensor.<frame>_<profile>_chores_progress` | the whole chart |
| `sensor.<frame>_<profile>_routine_progress` | chores Skylight marks as part of a routine |
| `sensor.<frame>_<profile>_other_chores_progress` | everything else |

The split is the API's own `routine` flag, not a guess from the clock. On a real chart it
separates getting-ready chores — which all carry a time of day — from open-ended ones like
a summer reading assignment.

Gauges take one entity and a fixed maximum, which is why these are percentages rather than
counts: nothing on a dashboard can divide `chores_completed` by a total.

Their icon fills as the number climbs — an empty circle at 0%, a full one at 100% — so an
`entities` card or a badge reads at a glance without a gauge at all. A profile with nothing
on their chart shows a question mark rather than an empty circle, since an empty circle
would claim nothing had been done.

```yaml
type: horizontal-stack
cards:
  - type: gauge
    entity: sensor.the_knowles_jacob_routine_progress
    name: Getting ready
    min: 0
    max: 100
    severity:
      green: 100
      yellow: 50
      red: 0
  - type: gauge
    entity: sensor.the_knowles_jacob_other_chores_progress
    name: Chores
    min: 0
    max: 100
```

A profile with nothing on their chart reads **unknown**, not 0% or 100% — both would be
claims about a chart that does not exist. A gauge renders that as empty, so wrap it in a
conditional card if the dashboard covers people who do not always have chores:

```yaml
type: conditional
conditions:
  - condition: state
    entity: sensor.the_knowles_jacob_routine_progress
    state_not: unknown
card:
  type: gauge
  entity: sensor.the_knowles_jacob_routine_progress
  name: Getting ready
```

## A note on what not to build

Chore counts are also available as `sensor.<frame>_<profile>_chores_due` and
`_chores_completed`, which are useful in automations and templates but say less than the
to-do card does. Prefer the card where there is room for it.

**Up for Grabs has no progress sensor, and cannot have one.** A chore drops out of the
only endpoint that returns unclaimed chores the moment somebody completes it, so the
number still open is observable and the number there were is not. A percentage built on
that would climb and reset as chores were claimed, describing nothing.
