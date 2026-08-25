"""MCTP Control Protocol commands (DSP0236).

NOT yet run against real hardware -- see config.py's module docstring
for the full status note. These tests are built from confirmed header
layouts (see mctp.py) and the confirmed target EID/address, but the
actual round trip against real silicon is unverified until the second
I2C bus is physically wired up.

Assertions are deliberately conservative where this project doesn't
have a source-confirmed exact response body layout (e.g. the endpoint-
type byte in Get Endpoint ID's response, or the exact bit-packing of
Get Message Type Support's supported-types list) -- asserting a
completion code and printing the rest for visibility, rather than
guessing at bit-level response structure the way this project's sibling
got burned doing for IPMI once. Tighten these once real responses are
observed.
"""

import mctp_helpers
from config import TARGET_EID
from mctp import (
    CTRL_CC_SUCCESS,
    CTRL_CMD_GET_ENDPOINT_ID,
    CTRL_CMD_GET_MESSAGE_TYPE_SUPPORT,
    CTRL_CMD_GET_MCTP_VERSION_SUPPORT,
    CTRL_CMD_SET_ENDPOINT_ID,
)


def test_get_endpoint_id(bridge):
    """Get Endpoint ID (cmd 0x02). Every MCTP endpoint must support this.

    Confirmed with the peer session: this always reports the endpoint's
    live EID (currently TARGET_EID, 0x09) by calling plat_get_eid()
    fresh on every request -- so the reported EID should exactly match
    what we're already using to address it.
    """
    decoded = mctp_helpers.send_mctp_control_command(bridge, CTRL_CMD_GET_ENDPOINT_ID)
    assert decoded["completion_code"] == CTRL_CC_SUCCESS
    assert len(decoded["data"]) >= 1, "expected at least an EID byte"
    reported_eid = decoded["data"][0]
    assert reported_eid == TARGET_EID, (
        f"endpoint reported EID 0x{reported_eid:02x}, expected 0x{TARGET_EID:02x} "
        f"(our own config.py's TARGET_EID -- has it changed on the device side?)"
    )
    print(f"full response data: {decoded['data'].hex(' ')}")


def test_get_mctp_version_support(bridge):
    """Get MCTP Version Support (cmd 0x04), querying the base MCTP spec
    version (message type selector 0xFF, the DSP0236 convention for "the
    base spec itself" rather than a specific message type's version).
    Every MCTP endpoint must support this for at least the base spec.
    """
    decoded = mctp_helpers.send_mctp_control_command(
        bridge, CTRL_CMD_GET_MCTP_VERSION_SUPPORT, data=bytes([0xFF])
    )
    assert decoded["completion_code"] == CTRL_CC_SUCCESS
    print(f"version support data: {decoded['data'].hex(' ')}")


def test_get_message_type_support(bridge):
    """Get Message Type Support (cmd 0x05). Every MCTP endpoint must
    support this. Expect MCTP Control (0x00) to always be listed (an
    endpoint answering this command at all obviously supports Control),
    and per the peer's earlier report that PLDM base/OEM are loaded on
    this platform, PLDM (0x01) should show up too -- but not asserting
    that second part, since "message type support" specifically for
    this platform hasn't been independently confirmed against source
    the way the header layouts have.
    """
    decoded = mctp_helpers.send_mctp_control_command(bridge, CTRL_CMD_GET_MESSAGE_TYPE_SUPPORT)
    assert decoded["completion_code"] == CTRL_CC_SUCCESS
    assert len(decoded["data"]) >= 2, "expected at least a count byte and one message type"
    print(f"supported message types (raw): {decoded['data'].hex(' ')}")
    # data[0] is a count of message types per DSP0236; data[1:] are the
    # type values themselves.
    count = decoded["data"][0]
    types = decoded["data"][1:1 + count]
    assert 0x00 in types, f"MCTP Control (0x00) should always be listed as supported; got {types.hex(' ')}"


def test_set_endpoint_id_idempotent(bridge):
    """Set Endpoint ID (cmd 0x01) -- a genuinely state-changing command,
    unlike everything else in this file, so this deliberately sets the
    EID to the value it should ALREADY be (TARGET_EID), making this an
    idempotent exercise of the mechanism rather than a real
    reconfiguration. Confirmed with the peer session this now actually
    works (it didn't, until a same-day fix on their side) rather than
    always failing.

    Request data format (DSP0236): byte0 = operation (0x00 = "Set EID"),
    byte1 = the EID to assign.

    If this ever needs to test a REAL EID change, do it in a dedicated
    test that changes it and then changes it back before any other test
    runs, since a mid-suite EID change here would break every other
    test in this file (they all address the endpoint via TARGET_EID).
    """
    SET_EID_OPERATION = 0x00
    decoded = mctp_helpers.send_mctp_control_command(
        bridge, CTRL_CMD_SET_ENDPOINT_ID, data=bytes([SET_EID_OPERATION, TARGET_EID])
    )
    assert decoded["completion_code"] == CTRL_CC_SUCCESS
    print(f"Set Endpoint ID response data: {decoded['data'].hex(' ')}")

    # Confirm it stuck (or, since we set it to what it already was,
    # confirm it's still there) via a follow-up Get.
    follow_up = mctp_helpers.send_mctp_control_command(bridge, CTRL_CMD_GET_ENDPOINT_ID)
    assert follow_up["completion_code"] == CTRL_CC_SUCCESS
    assert follow_up["data"][0] == TARGET_EID
