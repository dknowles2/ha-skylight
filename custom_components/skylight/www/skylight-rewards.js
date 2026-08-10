/**
 * A Lovelace card for one family profile's Skylight rewards.
 *
 * Every reward, with how close the profile is to it, and a redeem button on the
 * ones they can afford. Rewards are discovered from the state machine rather
 * than listed, because they are created and renamed on the frame — a card that
 * named them would be wrong the moment somebody added one.
 *
 *   type: custom:skylight-rewards
 *   profile: Jacob
 *
 * Deliberately plain JavaScript. A build step is the expensive part of shipping
 * a card, and nothing here needs one; this way the card can be read the same way
 * as the Python beside it.
 */

const REWARD_ATTRIBUTES = ["profile", "reward", "points_needed", "progress"];

/** Every reward entity in the state machine, whoever it belongs to. */
function rewardStates(hass) {
  if (!hass || !hass.states) return [];
  return Object.values(hass.states).filter(
    (state) =>
      state.entity_id.startsWith("number.") &&
      // Every attribute has to be present: a device's brightness number is also
      // a number, and a half-populated reward would render as holes.
      REWARD_ATTRIBUTES.every((name) => state.attributes[name] !== undefined),
  );
}

/** Profile names that have at least one reward, in alphabetical order. */
function profilesWithRewards(hass) {
  return [...new Set(rewardStates(hass).map((state) => state.attributes.profile))].sort();
}

class SkylightRewardsCard extends HTMLElement {
  /** The card the picker adds, pre-filled with a profile that has rewards. */
  static getStubConfig(hass) {
    return { profile: profilesWithRewards(hass)[0] || "" };
  }

  static async getConfigElement() {
    // Loads the frontend's own form components. Without it `ha-form` may not
    // be defined yet, since nothing else on a dashboard has necessarily pulled
    // it in — the editor falls back to plain inputs in that case, but the
    // native form is what belongs in Home Assistant's own dialog.
    if (window.loadCardHelpers) {
      try {
        await window.loadCardHelpers();
      } catch (err) {
        console.warn("skylight-rewards: could not load card helpers", err);
      }
    }
    return document.createElement("skylight-rewards-editor");
  }

  setConfig(config) {
    if (!config || !config.profile) {
      throw new Error("skylight-rewards: 'profile' is required");
    }
    this._config = config;
    this._rendered = null;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 1 + this._rewards().length;
  }

  /** Rewards belonging to the configured profile, nearest first. */
  _rewards() {
    const profile = this._config.profile;
    return rewardStates(this._hass)
      .filter((state) => state.attributes.profile === profile)
      .sort((a, b) => a.attributes.points_needed - b.attributes.points_needed);
  }

  _redeem(entityId) {
    this._hass.callService("skylight", "redeem_reward", {}, { entity_id: entityId });
  }

  /**
   * A signature of everything drawn, so an unrelated state change does not
   * rebuild the DOM. `hass` is set on every state change in the whole system,
   * which is many times a second on a busy install.
   */
  _signature(rewards) {
    return rewards
      .map((state) =>
        [
          state.entity_id,
          state.state,
          state.attributes.reward,
          state.attributes.points_needed,
          state.attributes.progress,
        ].join("|"),
      )
      .join("\n");
  }

  _render() {
    if (!this._hass || !this._config) return;

    const rewards = this._rewards();
    const signature = this._signature(rewards);
    if (signature === this._rendered) return;
    this._rendered = signature;

    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = "";
    this.shadowRoot.appendChild(this._style());

    const card = document.createElement("ha-card");
    if (this._config.title) card.setAttribute("header", this._config.title);

    if (rewards.length === 0) {
      card.appendChild(
        this._message(`No rewards found for ${this._config.profile}.`),
      );
      this.shadowRoot.appendChild(card);
      return;
    }

    const body = document.createElement("div");
    body.className = "body";
    rewards.forEach((state) => body.appendChild(this._reward(state)));
    card.appendChild(body);
    this.shadowRoot.appendChild(card);
  }

  _message(text) {
    const div = document.createElement("div");
    div.className = "body empty";
    div.textContent = text;
    return div;
  }

  _reward(state) {
    const needed = state.attributes.points_needed;
    const affordable = needed === 0;

    const row = document.createElement("div");
    row.className = "reward";

    const heading = document.createElement("div");
    heading.className = "heading";

    const name = document.createElement("span");
    name.className = "name";
    name.textContent = state.attributes.reward;
    heading.appendChild(name);

    const status = document.createElement("span");
    status.className = affordable ? "status ready" : "status";
    status.textContent = affordable ? "Ready" : `${needed} more`;
    heading.appendChild(status);

    const track = document.createElement("div");
    track.className = "track";
    const fill = document.createElement("div");
    fill.className = affordable ? "fill ready" : "fill";
    // Clamped: points keep accruing past a reward's price, and a bar wider than
    // its track would overflow the card.
    const progress = Math.max(0, Math.min(100, Number(state.attributes.progress) || 0));
    fill.style.width = `${progress}%`;
    track.appendChild(fill);

    row.appendChild(heading);
    row.appendChild(track);

    if (affordable) {
      const button = document.createElement("button");
      button.className = "redeem";
      button.type = "button";
      button.textContent = `Redeem for ${state.state} ★`;
      button.addEventListener("click", () => this._redeem(state.entity_id));
      row.appendChild(button);
    }

    return row;
  }

  _style() {
    const style = document.createElement("style");
    style.textContent = `
      .body {
        display: flex;
        flex-direction: column;
        gap: 16px;
        padding: 16px;
      }
      .empty {
        color: var(--secondary-text-color);
      }
      .reward {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .heading {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        gap: 12px;
      }
      .name {
        font-size: 1.1em;
        font-weight: 500;
        color: var(--primary-text-color);
      }
      .status {
        color: var(--secondary-text-color);
        white-space: nowrap;
      }
      .status.ready {
        color: var(--success-color, #0f9d58);
        font-weight: 500;
      }
      .track {
        background: var(--divider-color);
        border-radius: 6px;
        height: 12px;
        overflow: hidden;
      }
      .fill {
        background: var(--primary-color);
        height: 100%;
        border-radius: 6px;
        transition: width 0.4s ease-in-out;
      }
      .fill.ready {
        background: var(--success-color, #0f9d58);
      }
      .redeem {
        align-self: flex-start;
        margin-top: 4px;
        padding: 10px 18px;
        font: inherit;
        font-weight: 500;
        color: var(--text-primary-color, #fff);
        background: var(--success-color, #0f9d58);
        border: none;
        border-radius: 8px;
        cursor: pointer;
        /* A wall display is touched, not clicked. */
        min-height: 44px;
      }
      .redeem:active {
        opacity: 0.8;
      }
    `;
    return style;
  }
}

/**
 * The visual editor Home Assistant shows when the card is edited by hand.
 *
 * Home Assistant's own `ha-form` is used when it is available, so the dialog
 * looks like every other card's. It is not guaranteed to be defined — nothing
 * else on a dashboard necessarily pulls it in — so there is a plain fallback
 * rather than an empty dialog.
 */
class SkylightRewardsCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  /** The profile choices, plus whatever is configured even if it has no rewards. */
  _choices() {
    const profiles = profilesWithRewards(this._hass);
    const configured = this._config && this._config.profile;
    // A profile whose rewards have not loaded yet must not vanish from the
    // dropdown and silently rewrite the config on the next change.
    if (configured && !profiles.includes(configured)) profiles.unshift(configured);
    return profiles;
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
    const choices = this._choices();

    if (customElements.get("ha-form")) {
      this._renderHaForm(choices);
      return;
    }
    this._renderFallback(choices);
  }

  _renderHaForm(choices) {
    let form = this.querySelector("ha-form");
    if (!form) {
      this.innerHTML = "";
      form = document.createElement("ha-form");
      form.computeLabel = (field) =>
        field.name === "profile" ? "Profile" : "Title (optional)";
      form.addEventListener("value-changed", (event) => this._emit(event.detail.value));
      this.appendChild(form);
    }
    form.hass = this._hass;
    form.data = this._config;
    form.schema = [
      {
        name: "profile",
        required: true,
        selector: { select: { options: choices, mode: "dropdown", custom_value: true } },
      },
      { name: "title", selector: { text: {} } },
    ];
  }

  _renderFallback(choices) {
    if (this.querySelector("select")) return;
    this.innerHTML = "";

    const wrap = document.createElement("div");
    wrap.style.cssText = "display:flex;flex-direction:column;gap:12px;padding:8px 0";

    const select = document.createElement("select");
    select.style.cssText = "padding:8px;font:inherit";
    choices.forEach((profile) => {
      const option = document.createElement("option");
      option.value = profile;
      option.textContent = profile;
      option.selected = profile === this._config.profile;
      select.appendChild(option);
    });
    select.addEventListener("change", () =>
      this._emit({ ...this._config, profile: select.value }),
    );

    const title = document.createElement("input");
    title.type = "text";
    title.placeholder = "Title (optional)";
    title.value = this._config.title || "";
    title.style.cssText = "padding:8px;font:inherit";
    title.addEventListener("change", () =>
      this._emit({ ...this._config, title: title.value || undefined }),
    );

    wrap.appendChild(this._labelled("Profile", select));
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

customElements.define("skylight-rewards", SkylightRewardsCard);
customElements.define("skylight-rewards-editor", SkylightRewardsCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "skylight-rewards",
  name: "Skylight rewards",
  description: "A profile's Skylight rewards, with progress and a redeem button.",
});
