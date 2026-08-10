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

class SkylightRewardsCard extends HTMLElement {
  static getStubConfig() {
    return { profile: "" };
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
    if (!this._hass) return [];
    const profile = this._config.profile;
    return Object.values(this._hass.states)
      .filter((state) => {
        if (!state.entity_id.startsWith("number.")) return false;
        // Every attribute has to be present: a device's brightness number is
        // also a number, and a half-populated reward would render as holes.
        if (!REWARD_ATTRIBUTES.every((name) => state.attributes[name] !== undefined)) {
          return false;
        }
        return state.attributes.profile === profile;
      })
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

customElements.define("skylight-rewards", SkylightRewardsCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "skylight-rewards",
  name: "Skylight rewards",
  description: "A profile's Skylight rewards, with progress and a redeem button.",
});
