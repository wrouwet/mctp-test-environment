"""Queue/timing behavior under back-to-back requests -- not tied to any
one MCTP Control command.

Mirrors the sibling openbic-test-environment project's IPMB queue-depth
stress test, which found a real, documented 1-deep outbound TX queue
constraint on that transport. This was a fresh, independent probe for
MCTP rather than an assumption that the IPMB result would carry over --
and it turned out NOT to: confirmed against source with the peer
session, 2026-08-25, that MCTP's own TX queue (MCTP_TX_QUEUE_SIZE = 16
in common/service/mctp/mctp.c) is plenty deep; queue starvation isn't
the mechanism here at all. The real bug is a missing retry/re-queue in
mctp_tx_task(): unlike IPMB's TX task, which explicitly re-enqueues a
failed response send up to IPMB_TX_RETRY_TIME times, MCTP's version just
logs a warning and drops the message permanently the moment
write_data() fails even once past its own low-level retries -- so two
back-to-back requests landing close enough together that one response's
write hits transient bus contention (plausibly caused by the OTHER
request/response overlapping it) means that one response is just gone,
regardless of how much queue depth was available. A real, distinct gap
from IPMB's, not a variant of the same one.
"""

import mctp
import mctp_helpers
from bridge import BridgeError
from config import MCTP_TARGET_ADDR, OUR_EID, OUR_I2C_ADDR, TARGET_EID
from mctp import CTRL_CMD_GET_ENDPOINT_ID


def test_back_to_back_requests(bridge):
    """Fire two Get Endpoint ID requests back-to-back (distinct inst_id
    and msg_tag each), with no wait in between, before listening for
    either response.

    Only asserts what should be a safe minimum regardless of whatever
    queue depth this platform turns out to have: at least one of the
    two responses should make it back. Whether both do, and if not
    which one wins, is reported rather than asserted either way --
    same reasoning as the sibling IPMB test: asserting a specific
    outcome here would make this test flaky against real queue/retry
    timing instead of describing whatever the actual constraint is.
    """
    inst_id1, msg_tag1 = mctp_helpers.next_inst_id(), mctp_helpers.next_msg_tag()
    inst_id2, msg_tag2 = mctp_helpers.next_inst_id(), mctp_helpers.next_msg_tag()

    payload1 = mctp.build_control_request(
        dest_eid=TARGET_EID, src_eid=OUR_EID, cmd=CTRL_CMD_GET_ENDPOINT_ID,
        inst_id=inst_id1, msg_tag=msg_tag1,
    )
    payload2 = mctp.build_control_request(
        dest_eid=TARGET_EID, src_eid=OUR_EID, cmd=CTRL_CMD_GET_ENDPOINT_ID,
        inst_id=inst_id2, msg_tag=msg_tag2,
    )
    request1 = mctp.build_smbus_block_wrapper(OUR_I2C_ADDR, payload1) + payload1
    request2 = mctp.build_smbus_block_wrapper(OUR_I2C_ADDR, payload2) + payload2
    print(f"request 1 (inst_id={inst_id1}, msg_tag={msg_tag1}): {request1.hex(' ')}")
    print(f"request 2 (inst_id={inst_id2}, msg_tag={msg_tag2}): {request2.hex(' ')}")

    bridge.smbus_write(MCTP_TARGET_ADDR, request1)
    try:
        bridge.smbus_write(MCTP_TARGET_ADDR, request2)
        request2_sent = True
    except BridgeError as exc:
        # Seen on the IPMB side (arbitration lost against the target's own
        # in-flight response write) -- plausible here too, same physical
        # bus/driver stack underneath. Not asserted as required, just
        # handled gracefully if it happens.
        print(f"request 2's write itself lost the bus race against the target's own "
              f"outbound response for request 1 ({exc}) -- treating as if only "
              f"request 1 was actually sent.")
        request2_sent = False

    expected = {inst_id1}
    if request2_sent:
        expected.add(inst_id2)

    seen = {}
    for attempt in range(4):
        try:
            raw = bridge.listen(OUR_I2C_ADDR)
        except BridgeError as exc:
            print(f"listen attempt {attempt + 1}: no (more) responses arrived ({exc})")
            break
        try:
            after_pec = mctp_helpers._verify_and_strip_pec(raw)
            _, mctp_response = mctp.parse_smbus_block_wrapper(after_pec)
            decoded = mctp.parse_control_response(mctp_response)
        except ValueError as exc:
            print(f"listen attempt {attempt + 1}: malformed capture, discarding ({exc})")
            continue
        print(f"listen attempt {attempt + 1}: got {decoded}")
        if decoded["cmd"] == CTRL_CMD_GET_ENDPOINT_ID and decoded["inst_id"] in expected:
            seen[decoded["inst_id"]] = decoded
        if len(seen) == len(expected):
            break

    assert len(seen) >= 1, (
        "neither request's response arrived at all -- unlike the \"at least one "
        "wins the race\" outcome expected here, this would suggest something more "
        "seriously wrong than ordinary queue contention"
    )
    if len(seen) == len(expected):
        print(f"all {len(expected)} expected response(s) arrived -- "
              f"no queue/arbitration collision occurred this run")
    else:
        missing = expected - seen.keys()
        print(f"response(s) for inst_id={sorted(missing)} never arrived -- consistent "
              f"with the confirmed root cause (see this file's module docstring): "
              f"mctp_tx_task() has no retry/re-queue on a failed response write, so "
              f"transient contention from the overlapping request permanently drops "
              f"whichever response loses the race, regardless of the 16-deep TX queue "
              f"having plenty of room")
