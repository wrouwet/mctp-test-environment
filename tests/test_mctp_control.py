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
    MCTP_TARGET_ADDR,
    TARGET_EID,
)
from mctp import (
    CTRL_CC_ERROR,
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
    silently staying green under a now-stale xfail.

    Response body confirmed against source with the peer session,
    2026-08-25: `[count, major, minor, update, alpha]` = `01 f1 f3 ff 00`
    -- one entry, "MCTP Base Spec 1.3, patch level unspecified". Safe to
    assert byte-for-byte per that confirmation (see
    mctp.parse_mctp_version_entry()'s docstring for the exact decoding),
    unlike this file's more conservative tests where the response body
    layout hasn't been independently confirmed.
    """
    decoded = mctp_helpers.send_mctp_control_command(
        bridge, CTRL_CMD_GET_MCTP_VERSION_SUPPORT, data=bytes([0xFF])
    )
    assert decoded["completion_code"] == CTRL_CC_SUCCESS
    version = mctp.parse_mctp_version_entry(decoded["data"])
    print(f"version support: {version}")
    assert version["count"] == 1
    assert version["major"] == 1
    assert version["minor"] == 3
    assert version["update"] is None, "expected 0xFF (unspecified)"
    assert version["alpha"] is None, "expected 0x00 (no alpha character)"


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


def test_get_endpoint_uuid(bridge):
    """Get Endpoint UUID (cmd 0x03). Optional per DSP0236.

    Another confirmed-gap-turned-XPASS catch, same mechanism as Get
    MCTP Version Support: this was a documented "not in
    mctp_ctrl_cmd_tbl[]" gap until the peer session implemented it for
    real. Observed live, 2026-08-25: completion_code SUCCESS, a 16-byte
    UUID (`36 35 36 30 00 4d 44 31 00 00 00 10 00 29 00 04`). A UUID's
    only real contract is "16 bytes, unique to this endpoint, stable
    across calls" -- not a bitfield to decode -- so this asserts exactly
    that (length + success) rather than any specific byte values, and
    additionally checks it's the same UUID on a second call (an
    endpoint's UUID shouldn't change from one request to the next).
    """
    decoded1 = mctp_helpers.send_mctp_control_command(bridge, CTRL_CMD_GET_ENDPOINT_UUID)
    assert decoded1["completion_code"] == CTRL_CC_SUCCESS
    assert len(decoded1["data"]) == 16, f"expected a 16-byte UUID, got {decoded1['data'].hex(' ')}"
    print(f"UUID: {decoded1['data'].hex(' ')}")

    decoded2 = mctp_helpers.send_mctp_control_command(bridge, CTRL_CMD_GET_ENDPOINT_UUID)
    assert decoded2["data"] == decoded1["data"], (
        f"UUID changed between two calls: {decoded1['data'].hex(' ')} vs "
        f"{decoded2['data'].hex(' ')} -- should be stable for a given endpoint"
    )


def test_resolve_endpoint_id_own_eid(bridge):
    """Resolve Endpoint ID (cmd 0x07) for the endpoint's own EID.

    Another confirmed-gap-turned-real-feature, implemented honestly
    given this board has no downstream routing table: resolving the
    endpoint's OWN EID returns bridge_eid == target_eid (0x09) -- per
    DSP0236 12.10's own convention for "no bridging needed, this is
    local" -- and the real physical SMBus address (0x10), pulled live
    from the running mctp instance's medium config, not hardcoded.
    Resolving any OTHER EID is a genuine, honest error (see
    test_resolve_endpoint_id_unknown_eid below) rather than a fabricated
    resolution, since there's really nothing else to route to.
    """
    decoded = mctp_helpers.send_mctp_control_command(
        bridge, CTRL_CMD_RESOLVE_ENDPOINT_ID, data=bytes([TARGET_EID])
    )
    assert decoded["completion_code"] == CTRL_CC_SUCCESS
    assert len(decoded["data"]) == 2, f"expected [bridge_eid, phys_addr], got {decoded['data'].hex(' ')}"
    bridge_eid, phys_addr = decoded["data"]
    assert bridge_eid == TARGET_EID, (
        f"expected bridge_eid == target_eid (0x{TARGET_EID:02x}) for resolving our own EID "
        f"(DSP0236's 'no bridging needed' convention), got 0x{bridge_eid:02x}"
    )
    # phys_addr came back as 0x20, not the raw 7-bit 0x10 -- confirmed
    # live, 2026-08-25, to be the standard 8-bit left-shifted address
    # representation (addr << 1), the same convention already used
    # throughout this protocol for the SMBus wrapper's own src_addr
    # field (see mctp.build_smbus_block_wrapper()), not a bug.
    expected_phys_addr = (MCTP_TARGET_ADDR << 1) & 0xFF
    assert phys_addr == expected_phys_addr, (
        f"expected phys_addr == our real SMBus address in 8-bit form "
        f"(0x{expected_phys_addr:02x}), got 0x{phys_addr:02x}"
    )


def test_resolve_endpoint_id_unknown_eid(bridge):
    """Resolve Endpoint ID (cmd 0x07) for an EID that isn't this
    endpoint's own -- with no routing table, this board has nothing
    else to resolve to, so a genuine error is the honest, correct
    response, not a fabricated success.
    """
    unknown_eid = 0xFF
    assert unknown_eid != TARGET_EID
    decoded = mctp_helpers.send_mctp_control_command(
        bridge, CTRL_CMD_RESOLVE_ENDPOINT_ID, data=bytes([unknown_eid])
    )
    assert decoded["completion_code"] == CTRL_CC_ERROR, (
        f"expected a genuine ERROR (0x01) resolving an EID (0x{unknown_eid:02x}) this "
        f"board has no route to, got 0x{decoded['completion_code']:02x}"
    )
    assert len(decoded["data"]) == 0, f"expected no data on error, got {decoded['data'].hex(' ')}"


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
