"""Malformed multi-packet sequences: mis-ordering, truncation, duplication
-- and, critically, confirming NONE of them break the link for
subsequent, normal traffic.

Scope note, important for reading these tests correctly: as of
2026-08-25, even a CORRECTLY-FORMED multi-packet message doesn't
produce a response at all yet (test_fragmentation.py::
test_multi_packet_message_reassembles_correctly is an open, unresolved
finding, under active investigation with the peer session). That means
these tests can't yet distinguish "malformed input correctly rejected"
from "multi-packet messages don't work at all for any reason right
now" by response behavior alone -- both look identical (no response).
So the assertions here are deliberately scoped to what IS meaningful
regardless of that open issue: does sending something malformed ever
crash/hang/wedge the bus, and does normal, single-packet traffic still
work cleanly immediately afterward. Once the base multi-packet issue is
resolved, revisit these to also assert the CORRECT rejection behavior
specifically (e.g. a real error completion code for a mis-ordered
pkt_seq, once that's meaningful to distinguish from "nothing works").

Confirmed against source with the peer session, 2026-08-25: pkt_seq
validation and a reassembly-buffer timeout (for a SOM that never gets a
matching EOM) were both real, confirmed gaps that the peer session has
since fixed -- these tests exercise exactly those two paths, now that
it should be safe to (repeatedly triggering the timeout path before the
fix landed risked leaking one of only 16 reassembly slots with no
recovery; that's no longer a concern now that a timeout exists).
"""

import mctp
import mctp_helpers
from bridge import BridgeError
from config import MCTP_TARGET_ADDR, OUR_EID, OUR_I2C_ADDR, TARGET_EID
from mctp import CTRL_CC_SUCCESS, CTRL_CMD_GET_ENDPOINT_ID


def _confirm_link_still_healthy(bridge):
    """Send one ordinary, well-formed, single-packet request and confirm
    a normal, correct response comes back. This is the actual "did that
    malformed thing break anything" check every test below relies on --
    a clean pass here is the real assertion; a real single-packet round
    trip is a far more meaningful health check than just "the bus scan
    still lists an address" would be.
    """
    decoded = mctp_helpers.send_mctp_control_command(bridge, CTRL_CMD_GET_ENDPOINT_ID)
    assert decoded["completion_code"] == CTRL_CC_SUCCESS
    print("link health check passed: a normal request/response round trip still works")


def _send_raw_packet(bridge, packet):
    """Send one already-built MCTP packet (transport header + chunk) as
    its own wrapped, PEC'd wire transaction. Returns True if the write
    itself succeeded, False if it was rejected/lost at the I2C level
    (which is itself useful information, not a hard failure -- see each
    test's handling)."""
    wrapper = mctp.build_smbus_block_wrapper(OUR_I2C_ADDR, packet)
    wire_frame = wrapper + packet
    try:
        bridge.smbus_write(MCTP_TARGET_ADDR, wire_frame)
        return True
    except BridgeError as exc:
        print(f"write failed ({exc}) -- not necessarily a problem, could be "
              f"expected bus contention")
        return False


def test_mis_ordered_pkt_seq_does_not_break_link(bridge):
    """Send SOM (pkt_seq=0), then a middle fragment with pkt_seq JUMPED
    to 3 instead of the expected 1, then EOM (pkt_seq back to 2, the
    "correct" continuation) -- a real, out-of-order pkt_seq sequence.

    Whatever happens to this specific malformed message (rejected,
    silently accepted in arrival order, or -- like every other
    multi-packet message right now -- no response either way), the
    thing this test actually asserts is that it doesn't wedge the bus
    or crash the target: a normal request right after must still work.
    """
    inst_id = mctp_helpers.next_inst_id()
    msg_tag = mctp_helpers.next_msg_tag()
    padding = bytes(range(150))  # forces >1 fragment at MTU 64
    packets = mctp.fragment_control_request(
        dest_eid=TARGET_EID, src_eid=OUR_EID, cmd=CTRL_CMD_GET_ENDPOINT_ID,
        data=padding, inst_id=inst_id, msg_tag=msg_tag,
    )
    assert len(packets) >= 3, f"need at least 3 packets to have a real 'middle' one to reorder, got {len(packets)}"

    # Corrupt the middle packet's pkt_seq field (byte index 3, bits 5-4)
    # to a wrong value (3) instead of its correct sequence position.
    corrupted = bytearray(packets[1])
    corrupted[3] = (corrupted[3] & ~0x30) | (0x3 << 4)
    packets[1] = bytes(corrupted)
    print(f"packet 2's pkt_seq corrupted to 3 (was meant to be 1): {packets[1].hex(' ')}")

    for i, packet in enumerate(packets):
        sent = _send_raw_packet(bridge, packet)
        print(f"fragment {i + 1}/{len(packets)}: {'sent' if sent else 'write failed'}")

    try:
        raw = bridge.listen(OUR_I2C_ADDR)
        print(f"got a response: {raw.hex(' ')} -- whatever this means for "
              f"correctness, at least the link is clearly still alive")
    except BridgeError as exc:
        print(f"no response ({exc}) -- consistent with either correct rejection "
              f"or the still-open base multi-packet issue; not distinguishable "
              f"from here, see this file's module docstring")

    _confirm_link_still_healthy(bridge)


def test_truncated_message_does_not_break_link(bridge):
    """Send SOM only (a real fragment, pkt_seq=0, eom=0) and never send
    the rest of the message -- deliberately abandoning it mid-stream.

    Confirmed with the peer session that a reassembly-buffer timeout now
    exists for exactly this case (previously a real, confirmed leak with
    no recovery). This test doesn't wait out that timeout itself (that
    would make this suite slow for a state check that doesn't need
    real-time patience) -- it just confirms the abandoned SOM doesn't
    immediately break anything, and that normal traffic still works
    right away, without needing to wait for the target's own timeout to
    expire first. A separate, dedicated test would be needed to verify
    the timeout's actual duration/cleanup -- not attempted here.
    """
    inst_id = mctp_helpers.next_inst_id()
    msg_tag = mctp_helpers.next_msg_tag()
    padding = bytes(range(150))
    packets = mctp.fragment_control_request(
        dest_eid=TARGET_EID, src_eid=OUR_EID, cmd=CTRL_CMD_GET_ENDPOINT_ID,
        data=padding, inst_id=inst_id, msg_tag=msg_tag,
    )
    assert len(packets) >= 2

    sent = _send_raw_packet(bridge, packets[0])
    print(f"sent SOM only (fragment 1/{len(packets)}), abandoning the rest: "
          f"{'sent' if sent else 'write failed'}")

    _confirm_link_still_healthy(bridge)


def test_duplicate_pkt_seq_does_not_break_link(bridge):
    """Send SOM (pkt_seq=0), then the SAME middle fragment (pkt_seq=1)
    twice in a row, then EOM.

    Same framing as the mis-ordering test above: the meaningful
    assertion is link health afterward, not correctness of whatever
    happens to the duplicated-pkt_seq message itself (see this file's
    module docstring for why that's not distinguishable yet).
    """
    inst_id = mctp_helpers.next_inst_id()
    msg_tag = mctp_helpers.next_msg_tag()
    padding = bytes(range(150))
    packets = mctp.fragment_control_request(
        dest_eid=TARGET_EID, src_eid=OUR_EID, cmd=CTRL_CMD_GET_ENDPOINT_ID,
        data=padding, inst_id=inst_id, msg_tag=msg_tag,
    )
    assert len(packets) >= 3

    duplicated = [packets[0], packets[1], packets[1], packets[2]]
    for i, packet in enumerate(duplicated):
        sent = _send_raw_packet(bridge, packet)
        print(f"fragment {i + 1}/{len(duplicated)} (packet index "
              f"{[0, 1, 1, 2][i]}): {'sent' if sent else 'write failed'}")

    try:
        raw = bridge.listen(OUR_I2C_ADDR)
        print(f"got a response: {raw.hex(' ')}")
    except BridgeError as exc:
        print(f"no response ({exc}) -- see this file's module docstring")

    _confirm_link_still_healthy(bridge)
