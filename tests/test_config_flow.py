"""Tests for the Skylight config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pyskylight.exceptions import ApiError, AuthenticationError
from pyskylight.models import User
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skylight.const import DOMAIN

from .conftest import EMAIL, PASSWORD, USER_ID

pytestmark = pytest.mark.usefixtures("mock_setup_entry")

USER_INPUT = {CONF_USERNAME: EMAIL, CONF_PASSWORD: PASSWORD}


async def test_full_flow(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """A valid account creates an entry keyed on the account id."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert not result["errors"]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL
    assert result["data"] == USER_INPUT
    assert result["result"].unique_id == USER_ID


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthenticationError("nope"), "invalid_auth"),
        (ApiError(500, "boom"), "cannot_connect"),
        (RuntimeError("surprise"), "unknown"),
    ],
)
async def test_errors_are_recoverable(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    error: Exception,
    expected: str,
) -> None:
    """A failure shows on the form, and the user can correct it in place."""
    mock_client.get_user.side_effect = error
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    # Recover without restarting the flow.
    mock_client.get_user.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_duplicate_account_aborts(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The same Skylight account cannot be added twice."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_password(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Re-authenticating rewrites the stored password in place."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    # Home Assistant adds a "name" placeholder of its own alongside ours.
    assert result["description_placeholders"][CONF_USERNAME] == EMAIL

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-password"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_PASSWORD] == "new-password"
    assert mock_config_entry.data[CONF_USERNAME] == EMAIL


async def test_reauth_shows_errors(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A wrong password during reauth is reported, not stored."""
    mock_config_entry.add_to_hass(hass)
    mock_client.get_user.side_effect = AuthenticationError("nope")
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert mock_config_entry.data[CONF_PASSWORD] == PASSWORD


async def test_reauth_rejects_a_different_account(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Signing in as someone else would silently repoint every entity."""
    mock_config_entry.add_to_hass(hass)
    mock_client.get_user.return_value = User.from_response(
        {"id": "99999", "email": "someone.else@example.com"}
    )
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "another-password"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert mock_config_entry.data[CONF_PASSWORD] == PASSWORD
