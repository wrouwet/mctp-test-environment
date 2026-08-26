"""MCTP Control Protocol commands (DSP0236).

Hardware-verified as of 2026-08-25 (see README's "Current status") --
this file's tests run against the real OpenBIC MCTP endpoint over a
physically wired second I2C bus. Assertions are deliberately
conservative where this project doesn't have a source-confirmed exact
response body layout -- asserting a completion code and printing the
rest for visibility, rather than guessing at bit-level response
structure the way this project's sibling got burned doing for IPMI once.
"""

import mctp
import mctp_helpers
from config import (
    EXPECTED_EID_TYPE_ON_THIS_PLATFORM,
    EXPECTED_ENDPOINT_TYPE_ON_THIS_PLATFORM,
    EXPECTED_SUPPORTED_MESSAGE_TYPES_ON_THIS_PLATFORM,
    TARGET_EID,
)
from mctp import (
    CTRL_CC_SUCCESS,
    CTRL_CMD_GET_ENDPOINT_ID,
    CTRL_CMD_GET_ENDPOINT_UUID,
    CTRL_CMD_GET_MESSAGE_TYPE_SUPPORT,
    CTRL_CMD_GET_MCTP_VERSION_SUPPORT,
    CTRL_CMD_RESOLVE_ENDPOINT_ID,
    CTRL_CMD_SET_ENDPOINT_ID,
)


def test_get_endpoint_id(bridge):
    """Get Endpoint ID (cmd 0x02). Every MCTP endpoint must support this.

    Confirmed with the peer session: this always reports the endpoint's
    live EID (currently TARGET_EID, 0x09) by calling plat_get_eid()
    fresh on every request -- so the reported EID should exactly match
    what we're already using to address it.

    The response's third byte (_get_eid_resp's endpoint-type byte,
    DSP0236) is also confirmed, not just printed: this platform's
    handler sets eid_type=STATIC_EID and endpoint_type=BRIDGE (see
    config.py's EXPECTED_*_ON_THIS_PLATFORM constants for the "reasoned
    through the code, not wire-verified yet" caveat that applies to
    this specific claim).
    """
    decoded = mctp_helpers.send_mctp_control_command(bridge, CTRL_CMD_GET_ENDPOINT_ID)
    assert decoded["completion_code"] == CTRL_CC_SUCCESS
    assert len(decoded["data"]) >= 2, "expected at least an EID byte and an endpoint-type byte"
    reported_eid = decoded["data"][0]
    assert reported_eid == TARGET_EID, (
        f"endpoint reported EID 0x{reported_eid:02x}, expected 0x{TARGET_EID:02x} "
        f"(our own config.py's TARGET_EID -- has it changed on the device side?)"
    )
    endpoint_type_fields = mctp.parse_endpoint_type_byte(decoded["data"][1])
    print(f"endpoint-type byte: 0x{decoded['data'][1]:02x} -> {endpoint_type_fields}")
    assert endpoint_type_fields["eid_type"] == EXPECTED_EID_TYPE_ON_THIS_PLATFORM
    assert endpoint_type_fields["endpoint_type"] == EXPECTED_ENDPOINT_TYPE_ON_THIS_PLATFORM
    print(f"full response data: {decoded['data'].hex(' ')}")


def test_get_mctp_version_support(bridge):
    """Get MCTP Version Support (cmd 0x04), querying the base MCTP spec
    version (message type selector 0xFF, the DSP0236 convention for "the
    base spec itself" rather than a specific message type's version).
    Every MCTP endpoint must support this for at least the base spec.

    Previously a confirmed real gap on this platform (not in OpenBIC's
    shared MCTP Control dispatch table at all, always returned
    ERROR_UNSUPPORTED_CMD) -- caught turning back into a real, passing
    test exactly the way this suite's not_implemented() mechanism is
    meant to: this test flipped to a failing XPASS the moment the peer
    session implemented it for real, forcing this update rather than
    silently staying green under a now-stale xfail. Observed live,
    2026-08-25: completion_code SUCCESS, data `01 f1 f3 ff 00` --
    consistent with DSP0236's version-entry encoding (a count byte, 1,
    followed by one 4-byte BCD-ish version entry) for something in the
    neighborhood of "MCTP Base Specification 1.3.x", though the exact
    nibble-level meaning of every byte isn't independently confirmed
    here -- printed for visibility rather than asserted byte-for-byte,
    same conservative-response-body-structure approach used elsewhere
    in this file.
    """
    decoded = mctp_helpers.send_mctp_control_command(
        bridge, CTRL_CMD_GET_MCTP_VERSION_SUPPORT, data=bytes([0xFF])
    )
    assert decoded["completion_code"] == CTRL_CC_SUCCESS
    print(f"version support data: {decoded['data'].hex(' ')}")


def test_get_message_type_support(bridge):
    """Get Message Type Support (cmd 0x05). Every MCTP endpoint must
    support this.

    This command had a real, previously-unknown gap on this platform:
    load_mctp_support_types() was an unimplemented __weak default
    (returning a bare error, not a real type list) until the peer
    session implemented it for real, 2026-08-25 -- confirmed with them
    that this platform's dispatch genuinely handles both MCTP Control
    (0x00) and PLDM (0x01), and the handler now reports exactly those
    two. Same "reasoned through the code, not wire-verified yet" caveat
    as everything else in this repo -- see EXPECTED_SUPPORTED_MESSAGE_
    TYPES_ON_THIS_PLATFORM's comment in config.py.
    """
    decoded = mctp_helpers.send_mctp_control_command(bridge, CTRL_CMD_GET_MESSAGE_TYPE_SUPPORT)
    assert decoded["completion_code"] == CTRL_CC_SUCCESS
    assert len(decoded["data"]) >= 2, "expected at least a count byte and one message type"
    print(f"supported message types (raw): {decoded['data'].hex(' ')}")
    # data[0] is a count of message types per DSP0236; data[1:] are the
    # type values themselves.
    count = decoded["data"][0]
    types = decoded["data"][1:1 + count]
    assert set(types) == set(EXPECTED_SUPPORTED_MESSAGE_TYPES_ON_THIS_PLATFORM), (
        f"expected exactly {[hex(t) for t in EXPECTED_SUPPORTED_MESSAGE_TYPES_ON_THIS_PLATFORM]}, "
        f"got {[hex(t) for t in types]}"
    )


@mctp_helpers.not_implemented(
    "Get Endpoint UUID isn't in mctp_ctrl_cmd_tbl[] either -- same shared dispatch "
    "table gap as Get MCTP Version Support (see that test), confirmed against "
    "source: only 3 commands are wired in (Set/Get Endpoint ID, Get Message Type "
    "Support), so any other command code falls through to ERROR_UNSUPPORTED_CMD "
    "(0x05) regardless of what's sent."
)
def test_get_endpoint_uuid(bridge):
    """Get Endpoint UUID (cmd 0x03). Optional per DSP0236, but this
    platform doesn't even have a stub for it -- it hits the same
    generic dispatch-table fallthrough as any other unimplemented
    command, not a specific "optional, not supported" completion code."""
    decoded = mctp_helpers.send_mctp_control_command(bridge, CTRL_CMD_GET_ENDPOINT_UUID)
    assert decoded["completion_code"] == CTRL_CC_SUCCESS


@mctp_helpers.not_implemented(
    "Resolve Endpoint ID isn't in mctp_ctrl_cmd_tbl[] either -- same shared "
    "dispatch table gap as Get MCTP Version Support/Get Endpoint UUID (see those "
    "tests). Expected: this platform has no downstream routing at all (single "
    "local endpoint, per the peer session's platform inventory), so Resolve "
    "Endpoint ID wouldn't have anything meaningful to do here even if it were "
    "wired up -- but the actual observed failure mode is the generic dispatch "
    "fallthrough (0x05), not a routing-specific rejection, so that's what this "
    "documents rather than assuming."
)
def test_resolve_endpoint_id(bridge):
    """Resolve Endpoint ID (cmd 0x07)."""
    decoded = mctp_helpers.send_mctp_control_command(
        bridge, CTRL_CMD_RESOLVE_ENDPOINT_ID, data=bytes([TARGET_EID])
    )
    assert decoded["completion_code"] == CTRL_CC_SUCCESS


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
