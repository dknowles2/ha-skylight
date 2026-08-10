/**
 * A Lovelace card for one Skylight chore list.
 *
 * A chore chart is a to-do list only in the sense that both have rows and
 * checkboxes. What it is really for is one child, one day, one screen on a wall:
 *
 *   type: custom:skylight-chores
 *   entity: todo.the_knowles_jacob_chores
 *
 * The differences from the built-in `todo-list` card are all in that direction.
 * Rows are sized for a thumb rather than a mouse. The whole row is the target.
 * Reward points ride inside `description` — a to-do item has no field for them —
 * so they are pulled back out and shown as a badge instead of as body text. A
 * chore with a time of day shows it, because "Brush Teeth" appears twice on a
 * real chart and nothing else tells the two apart. There is no add field, no
 * sort menu, and no reordering, none of which belong on a child's wall. And how
 * much is left is the point of the screen, so it is drawn at the top rather than
 * living in a separate sensor.
 *
 * Items arrive over the same websocket subscription the built-in card uses:
 * they are not in the entity's state, which is only the count still to do.
 *
 * Deliberately plain JavaScript, matching the rewards card beside it: a build
 * step is the expensive part of shipping a card, and nothing here needs one.
 */

/**
 * The reward-points line the integration puts at the top of a description.
 *
 * It is written by `_points_line()` in `todo.py` as a star, a space and the
 * number, with the user's own notes below it after a blank line. Anything that
 * does not match that shape is left alone and shown as notes.
 */
const POINTS_LINE = /^⭐ (\d+)(?:\n+([\s\S]*))?$/;

/** Split a chore's description into its points badge and the notes underneath. */
function splitDescription(description) {
  if (!description) return { points: null, notes: null };
  const match = POINTS_LINE.exec(description);
  if (!match) return { points: null, notes: description };
  return { points: Number(match[1]), notes: match[2] || null };
}

/**
 * The time of day a chore is due, or null if it has none.
 *
 * `due` arrives as an ISO string; a chore with a time of day is a datetime and
 * carries a `T`, one without is a plain date. The distinction matters more than
 * it looks — it is the only thing distinguishing a morning chore from the same
 * chore at bedtime.
 */
function dueTime(due) {
  if (!due || !due.includes("T")) return null;
  const at = new Date(due);
  return Number.isNaN(at.getTime()) ? null : at;
}

function isDone(item) {
  return item.status === "completed";
}

/** Whatever a rejected call can be persuaded to say for itself. */
function message(err) {
  if (!err) return "Something went wrong.";
  return err.message || String(err);
}

/** Skylight's own to-do entities, which are the ones this card can render. */
function choreEntities(hass) {
  if (!hass || !hass.entities) return [];
  return Object.keys(hass.entities)
    .filter(
      (entityId) =>
        entityId.startsWith("todo.") && hass.entities[entityId].platform === "skylight",
    )
    .sort();
}

class SkylightChoresCard extends HTMLElement {
  static getStubConfig(hass) {
    return { entity: choreEntities(hass)[0] || "" };
  }

  static async getConfigElement() {
    // Pulls in the frontend's own form components. Nothing else on a dashboard
    // necessarily has, so without this `ha-form` may not be defined yet.
    if (window.loadCardHelpers) {
      try {
        await window.loadCardHelpers();
      } catch (err) {
        console.warn("skylight-chores: could not load card helpers", err);
      }
    }
    return document.createElement("skylight-chores-editor");
  }

  constructor() {
    super();
    this._items = null;
    /** The subscription failed, so there is no list — this replaces the card. */
    this._listError = null;
    /** A tap did not take, which is shown above a list that is still fine. */
    this._writeError = null;
    /**
     * Taps whose service call has not come back yet, as uid -> status.
     *
     * A tap goes to Skylight's servers and only reaches this card on the poll
     * after that, which is long enough for a child to conclude the screen is
     * broken and tap again. The row changes immediately and is corrected by the
     * subscription if the write turns out to have failed.
     */
    this._optimistic = new Map();
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("skylight-chores: 'entity' is required");
    }
    if (!config.entity.startsWith("todo.")) {
      throw new Error("skylight-chores: 'entity' must be a to-do list");
    }
    const changed = !this._config || this._config.entity !== config.entity;
    this._config = config;
    this._drawn = null;
    if (changed) {
      this._items = null;
      this._listError = null;
      this._writeError = null;
      this._optimistic.clear();
      this._subscribe();
    } else {
      this._render();
    }
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._subscribe();
  }

  connectedCallback() {
    this._subscribe();
  }

  disconnectedCallback() {
    this._unsubscribe();
  }

  getCardSize() {
    return 2 + (this._items ? this._items.length : 3);
  }

  // -- the item subscription -------------------------------------------------

  _subscribe() {
    this._unsubscribe();
    if (!this._hass || !this._config || !this.isConnected) return;
    if (!this._hass.connection) return;

    const request = this._hass.connection.subscribeMessage(
      (message) => this._receive(message.items || []),
      { type: "todo/item/subscribe", entity_id: this._config.entity },
    );
    this._request = request;
    request.then(
      (unsubscribe) => {
        // The card can be taken off the dashboard between asking for the
        // subscription and getting it, and an unsubscribe nobody keeps is a
        // subscription nobody ever closes.
        if (this._request !== request) {
          unsubscribe();
          return;
        }
        this._unsubscribeItems = unsubscribe;
      },
      (err) => {
        if (this._request !== request) return;
        // Fatal: without the subscription there is no list to draw at all.
        this._listError = message(err);
        this._drawn = null;
        this._render();
      },
    );
  }

  _unsubscribe() {
    this._request = null;
    if (this._unsubscribeItems) {
      this._unsubscribeItems();
      this._unsubscribeItems = null;
    }
  }

  _receive(items) {
    // Anything the server now agrees with is no longer outstanding. Anything it
    // does not is left in place: the write may still be in flight, and dropping
    // it here would flip the row back and forth under a child's finger.
    items.forEach((item) => {
      if (this._optimistic.get(item.uid) === item.status) {
        this._optimistic.delete(item.uid);
      }
    });
    this._listError = null;
    // A fresh list is the answer to whatever the last write did, so the warning
    // about it has served its purpose.
    this._writeError = null;
    this._items = items;
    this._render();
  }

  // -- checking things off ---------------------------------------------------

  _toggle(item) {
    const status = isDone(item) ? "needs_action" : "completed";
    this._optimistic.set(item.uid, status);
    this._drawn = null;
    this._render();

    // Called on `hass` rather than through the connection directly so the call
    // carries the signed-in user, which is what credits an Up for Grabs chore to
    // whoever tapped it.
    const call = this._hass.callService(
      "todo",
      "update_item",
      { item: item.uid, status },
      { entity_id: this._config.entity },
    );
    if (!call || !call.catch) return;
    call.catch((err) => {
      // Put the row back rather than leaving it showing something that did not
      // happen, and say so above the list. Not instead of the list: a failed tap
      // is one row's problem, and replacing the whole chart with an error would
      // take away the other five chores a child can still do something about.
      this._optimistic.delete(item.uid);
      this._writeError = message(err);
      this._drawn = null;
      this._render();
    });
  }

  /** The items as they should be drawn, with outstanding taps applied. */
  _visible() {
    const items = (this._items || []).map((item) => {
      const pending = this._optimistic.get(item.uid);
      return pending ? { ...item, status: pending, pending: true } : item;
    });
    if (this._config.hide_completed) return items.filter((item) => !isDone(item));
    return items;
  }

  // -- drawing ---------------------------------------------------------------

  _signature(items) {
    return [
      this._listError || "",
      this._writeError || "",
      this._config.title || "",
      String(this._scale()),
      items
        .map((item) =>
          [item.uid, item.summary, item.status, item.due, item.description, item.pending].join(
            "|",
          ),
        )
        .join("\n"),
    ].join(" ");
  }

  _render() {
    if (!this._hass || !this._config) return;

    const items = this._visible();
    const signature = this._signature(items);
    if (signature === this._drawn) return;
    this._drawn = signature;

    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = "";
    this.shadowRoot.appendChild(this._style());

    const card = document.createElement("ha-card");

    if (this._listError) {
      card.appendChild(this._message(this._listError, "error"));
      this.shadowRoot.appendChild(card);
      return;
    }
    if (this._items === null) {
      // Still waiting on the first push. Deliberately blank rather than "no
      // chores", which would be a lie for the second it takes to arrive.
      card.appendChild(this._message("Loading…"));
      this.shadowRoot.appendChild(card);
      return;
    }

    const body = document.createElement("div");
    body.className = "body";

    const header = this._header();
    if (header) body.appendChild(header);

    if (this._writeError) body.appendChild(this._message(this._writeError, "error"));

    if (items.length === 0) {
      body.appendChild(this._message("Nothing on this list."));
    } else {
      const list = document.createElement("div");
      list.className = "list";
      items.forEach((item) => list.appendChild(this._row(item)));
      body.appendChild(list);
    }

    card.appendChild(body);
    this.shadowRoot.appendChild(card);
  }

  /** Title, the count still to do, and a bar showing how much of the day is left. */
  _header() {
    // Counted from every item rather than from the visible ones, so that
    // `hide_completed` does not make the chart read "0 of 2" with five chores
    // already behind it.
    const applied = (this._items || []).map((item) => {
      const pending = this._optimistic.get(item.uid);
      return pending ? { ...item, status: pending } : item;
    });
    const complete = applied.filter(isDone).length;
    const total = applied.length;
    const finished = total > 0 && complete === total;

    const showProgress = this._config.show_progress !== false && total > 0;
    if (!this._config.title && !showProgress) return null;

    const header = document.createElement("div");
    header.className = "header";

    const line = document.createElement("div");
    line.className = "title-line";

    const title = document.createElement("span");
    title.className = "title";
    title.textContent = this._config.title || "";
    line.appendChild(title);

    if (showProgress) {
      const count = document.createElement("span");
      count.className = finished ? "count done" : "count";
      count.textContent = finished
        ? this._config.done_message || "🎉 All done!"
        : `${complete} of ${total}`;
      line.appendChild(count);
    }
    header.appendChild(line);

    if (showProgress) {
      const track = document.createElement("div");
      track.className = "track";
      const fill = document.createElement("div");
      fill.className = finished ? "fill done" : "fill";
      fill.style.width = `${Math.round((complete / total) * 100)}%`;
      track.appendChild(fill);
      header.appendChild(track);
    }

    return header;
  }

  _row(item) {
    const done = isDone(item);
    const { points, notes } = splitDescription(item.description);
    const at = dueTime(item.due);

    const row = document.createElement("button");
    row.type = "button";
    row.className = ["row", done ? "done" : "", item.pending ? "pending" : ""]
      .filter(Boolean)
      .join(" ");
    row.addEventListener("click", () => this._toggle(item));

    const box = document.createElement("span");
    box.className = "box";
    box.textContent = done ? "✓" : "";
    row.appendChild(box);

    const text = document.createElement("span");
    text.className = "text";

    const summary = document.createElement("span");
    summary.className = "summary";
    summary.textContent = item.summary || "";
    text.appendChild(summary);

    if (notes) {
      const note = document.createElement("span");
      note.className = "notes";
      note.textContent = notes;
      text.appendChild(note);
    }
    row.appendChild(text);

    const meta = document.createElement("span");
    meta.className = "meta";
    if (at) {
      const time = document.createElement("span");
      time.className = "time";
      time.textContent = at.toLocaleTimeString(...this._timeFormat());
      meta.appendChild(time);
    }
    if (points) {
      const badge = document.createElement("span");
      badge.className = "points";
      badge.textContent = `${points} ★`;
      meta.appendChild(badge);
    }
    if (meta.childElementCount) row.appendChild(meta);

    return row;
  }

  /**
   * Arguments for `toLocaleTimeString`, honouring the user's Home Assistant
   * settings rather than the browser's.
   *
   * A wall panel is not necessarily configured like the phone the dashboard was
   * built on, and someone who chose a 24-hour clock in Home Assistant chose it
   * for every screen.
   */
  _timeFormat() {
    const locale = this._hass.locale || {};
    const options = { hour: "numeric", minute: "2-digit" };
    if (locale.time_format === "twenty_four") options.hour12 = false;
    if (locale.time_format === "am_pm") options.hour12 = true;
    // "language" and "system" both mean "whatever this locale normally does",
    // which is what leaving `hour12` alone already gives.
    return [locale.language || undefined, options];
  }

  _message(text, className) {
    const div = document.createElement("div");
    div.className = className ? `message ${className}` : "message";
    div.textContent = text;
    return div;
  }

  /**
   * The one number every size on the card is derived from.
   *
   * A wall display is looked at from across a room but is often physically
   * small — an Echo Show 5 is 960x480 in about five inches — so how big the
   * text wants to be is a property of the screen it lands on, not something
   * this can pick once. Clamped rather than validated: a card that renders at
   * an absurd size is less use than one that quietly refuses to.
   */
  _scale() {
    const asked = Number(this._config.text_scale);
    if (!Number.isFinite(asked) || asked <= 0) return 1;
    return Math.max(0.6, Math.min(1.5, asked));
  }

  _style() {
    const style = document.createElement("style");
    // Everything below is in `em`, so this single declaration scales the text,
    // the checkbox, the padding and the row heights together. The exception is
    // the row's floor, which is in pixels on purpose — see below.
    style.textContent = `
      .body {
        font-size: ${this._scale()}rem;
        display: flex;
        flex-direction: column;
        gap: 0.75em;
        padding: 1em;
      }
      .message {
        color: var(--secondary-text-color);
        padding: 0.25em 0;
      }
      .message.error {
        color: var(--error-color, #db4437);
      }
      .header {
        display: flex;
        flex-direction: column;
        gap: 0.5em;
      }
      .title-line {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        gap: 0.75em;
      }
      .title {
        font-size: 1.5em;
        font-weight: 500;
        color: var(--primary-text-color);
      }
      .count {
        color: var(--secondary-text-color);
        white-space: nowrap;
      }
      .count.done {
        color: var(--success-color, #0f9d58);
        font-weight: 500;
      }
      .track {
        background: var(--divider-color);
        border-radius: 0.35em;
        height: 0.6em;
        overflow: hidden;
      }
      .fill {
        background: var(--primary-color);
        height: 100%;
        border-radius: 0.35em;
        transition: width 0.4s ease-in-out;
      }
      .fill.done {
        background: var(--success-color, #0f9d58);
      }
      .list {
        display: flex;
        flex-direction: column;
      }
      .row {
        display: flex;
        align-items: center;
        gap: 0.875em;
        width: 100%;
        /* The whole row is the target, because a checkbox is a few millimetres
           wide and this is meant to be hit with a thumb. The floor is in pixels
           and does not scale: shrinking the text is what makes a chart fit, and
           shrinking the thing a child has to hit is what makes it miss. 44px is
           the smallest touch target the accessibility guidelines allow. */
        min-height: max(44px, 3.6em);
        padding: 0.5em 0.25em;
        font: inherit;
        text-align: left;
        color: var(--primary-text-color);
        background: none;
        border: none;
        border-bottom: 1px solid var(--divider-color);
        cursor: pointer;
      }
      .row:last-child {
        border-bottom: none;
      }
      .row:active {
        background: var(--divider-color);
      }
      .row.pending {
        opacity: 0.6;
      }
      .box {
        flex: 0 0 auto;
        display: flex;
        align-items: center;
        justify-content: center;
        width: 1.9em;
        height: 1.9em;
        box-sizing: border-box;
        border: 2px solid var(--secondary-text-color);
        border-radius: 50%;
        font-size: 1.15em;
        line-height: 1;
        color: var(--text-primary-color, #fff);
      }
      .row.done .box {
        background: var(--success-color, #0f9d58);
        border-color: var(--success-color, #0f9d58);
      }
      .text {
        flex: 1 1 auto;
        display: flex;
        flex-direction: column;
        gap: 0.125em;
        min-width: 0;
      }
      .summary {
        font-size: 1.3em;
        font-weight: 500;
        overflow-wrap: anywhere;
      }
      .row.done .summary {
        color: var(--secondary-text-color);
        text-decoration: line-through;
      }
      .notes {
        font-size: 0.9em;
        color: var(--secondary-text-color);
        overflow-wrap: anywhere;
      }
      .meta {
        flex: 0 0 auto;
        display: flex;
        align-items: center;
        gap: 0.5em;
      }
      .time {
        color: var(--secondary-text-color);
        white-space: nowrap;
      }
      .points {
        padding: 0.2em 0.6em;
        border-radius: 999px;
        white-space: nowrap;
        font-weight: 500;
        color: var(--text-primary-color, #fff);
        background: var(--amber-color, #ffa600);
      }
      .row.done .points {
        opacity: 0.5;
      }
    `;
    return style;
  }
}

/**
 * The visual editor Home Assistant shows when the card is edited by hand.
 *
 * `ha-form` when it is available, so the dialog looks like every other card's,
 * and a plain fallback rather than an empty dialog when it is not.
 */
class SkylightChoresCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _emit(config) {
    this._config = config;
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config },
        bubbles: true,
        composed: true,
      }),
    );
  }

  _render() {
    if (!this._config) return;
    if (customElements.get("ha-form")) {
      this._renderHaForm();
      return;
    }
    this._renderFallback();
  }

  _renderHaForm() {
    let form = this.querySelector("ha-form");
    if (!form) {
      this.innerHTML = "";
      form = document.createElement("ha-form");
      form.computeLabel = (field) =>
        ({
          entity: "Chore list",
          title: "Title (optional)",
          show_progress: "Show how many are done",
          hide_completed: "Hide finished chores",
          text_scale: "Text size",
        })[field.name] || field.name;
      form.addEventListener("value-changed", (event) => this._emit(event.detail.value));
      this.appendChild(form);
    }
    form.hass = this._hass;
    form.data = this._config;
    form.schema = [
      {
        name: "entity",
        required: true,
        // Narrowed to this integration's own lists: the card reads a chore's
        // points and time of day, and neither means anything on someone else's
        // to-do list.
        selector: { entity: { domain: "todo", integration: "skylight" } },
      },
      { name: "title", selector: { text: {} } },
      { name: "show_progress", selector: { boolean: {} } },
      { name: "hide_completed", selector: { boolean: {} } },
      {
        name: "text_scale",
        // A slider rather than a number box: the right value is whatever looks
        // right on the display in front of you, and that is found by dragging.
        selector: { number: { min: 0.6, max: 1.5, step: 0.05, mode: "slider" } },
      },
    ];
  }

  _renderFallback() {
    if (this.querySelector("select")) return;
    this.innerHTML = "";

    const wrap = document.createElement("div");
    wrap.style.cssText = "display:flex;flex-direction:column;gap:12px;padding:8px 0";

    const select = document.createElement("select");
    select.style.cssText = "padding:8px;font:inherit";
    const choices = choreEntities(this._hass);
    if (this._config.entity && !choices.includes(this._config.entity)) {
      // A list whose registry entry has not loaded yet must not vanish from the
      // dropdown and silently rewrite the config on the next change.
      choices.unshift(this._config.entity);
    }
    choices.forEach((entityId) => {
      const option = document.createElement("option");
      option.value = entityId;
      option.textContent = entityId;
      option.selected = entityId === this._config.entity;
      select.appendChild(option);
    });
    select.addEventListener("change", () =>
      this._emit({ ...this._config, entity: select.value }),
    );

    const title = document.createElement("input");
    title.type = "text";
    title.placeholder = "Title (optional)";
    title.value = this._config.title || "";
    title.style.cssText = "padding:8px;font:inherit";
    title.addEventListener("change", () =>
      this._emit({ ...this._config, title: title.value || undefined }),
    );

    wrap.appendChild(this._labelled("Chore list", select));
    wrap.appendChild(this._labelled("Title (optional)", title));
    this.appendChild(wrap);
  }

  _labelled(text, field) {
    const row = document.createElement("label");
    row.style.cssText = "display:flex;flex-direction:column;gap:4px";
    const caption = document.createElement("span");
    caption.textContent = text;
    caption.style.cssText = "font-size:0.9em;opacity:0.7";
    row.appendChild(caption);
    row.appendChild(field);
    return row;
  }
}

// Guarded, because this file can legitimately arrive twice: the integration
// registers it with the frontend, and a display whose browser never picked that
// up can be pointed at the same URL as a Lovelace resource instead. An
// unguarded `define` throws on the second one, and the error would surface as
// the card not working at all.
if (!customElements.get("skylight-chores")) {
  customElements.define("skylight-chores", SkylightChoresCard);
}
if (!customElements.get("skylight-chores-editor")) {
  customElements.define("skylight-chores-editor", SkylightChoresCardEditor);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "skylight-chores")) {
  window.customCards.push({
    type: "skylight-chores",
    name: "Skylight chores",
    description: "A Skylight chore list, sized for a wall display.",
  });
}
