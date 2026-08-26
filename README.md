# MCTP Test Environment

A pytest-based test suite, run from a host PC, for exercising an
[OpenBIC](https://github.com/facebook/OpenBIC) controller's MCTP
(Management Component Transport Protocol) endpoint over SMBus/I2C.
Sibling project to
[openbic-test-environment](https://github.com/wrouwet/openbic-test-environment)
(which tests the same kind of controller over IPMB) -- same test style
and philosophy, different transport/protocol.

**Scope is deliberately MCTP-only**: MCTP transport framing (DSP0236)
and the MCTP Control Protocol. No PLDM or other higher-layer message
type is tested here; that would be a different project built on top of
this one's foundation, not an extension of it.

## Current status: hardware-verified as of 2026-08-25 (5/6 real, 1 open question)

This repo's first draft was built entirely *before* the physical I2C
bus it needs was wired up, at the user's explicit direction -- see git
history if you want the details of that phase. **That phase is over.**
The bus is now wired (`flexcomm3_lpi2c3`, see "What you need" below) and
the suite has run against real hardware for the first time.

Result: **first contact found a real, non-obvious bug**, exactly as
this README used to warn it would -- the initial frame-building code
was missing DSP0237's 3-byte SMBus block-write wrapper
(`mctp.build_smbus_block_wrapper()`) that has to precede the MCTP
transport header on every single frame. Without it, OpenBIC's
`mctp_smbus_read()` silently discarded every request before MCTP-level
parsing ever ran -- no log output at all, which is why "the write
succeeds but nothing ever comes back" was the only symptom visible from
this side; finding the actual cause took reading the target's console
log on the peer's side. Once added, 5 of 6 tests started passing with
genuine, real, bidirectional MCTP traffic:

- Get Endpoint ID: reports EID 0x09, endpoint-type byte 0x11 (STATIC/
  BRIDGE) -- exactly the values source-confirmed with the peer session,
  now also wire-confirmed.
- Get Message Type Support: reports {Control, PLDM} -- also matches
  the source-confirmed implementation, now wire-confirmed.
- Set Endpoint ID: round-trips successfully.
- The PEC-corruption framing test passes as designed.

**One real, confirmed gap**: Get MCTP Version Support (cmd 0x04) isn't
in OpenBIC's shared MCTP Control dispatch table at all (confirmed
against source with the peer session) -- the dispatch loop's fallthrough
always returns `ERROR_UNSUPPORTED_CMD` for this command, regardless of
what selector byte is sent, on every OpenBIC board on mainline. Not a
framing bug (the response is well-formed) and not fixable by trying a
different request. Marked `not_implemented()`; see that test's docstring
in `tests/test_mctp_control.py` for the full trace.

What's still NOT verified, going forward: response body formats beyond
what's been directly observed above, behavior of any command not yet
exercised, and everything about fragmentation/multi-packet messages
(this suite only sends single-packet, unfragmented requests so far).
Treat every new test added here the same way this repo's first real run
went -- expect genuine first-contact issues, not a clean pass.

## What you need before you start

**Hardware:**

1. A FRDM-MCXA153 bridge board flashed with the firmware from
   [frdm-mcxa153-usb-i2c-hub](https://github.com/wrouwet/frdm-mcxa153-usb-i2c-hub)
   (the same one the sibling IPMB project uses) -- specifically a build
   including its SMBus (`WS`/`RS`/`XS`) command support, which this
   project depends on.
2. **A second, separate physical I2C bus connection** to the OpenBIC
   target's MCTP endpoint -- this is NOT the same wiring as the sibling
   IPMB project uses. The bridge chip (MCXA153) has only one hardware
   I2C peripheral, already committed to IPMB if that's also wired up, so
   this requires either a second bridge board dedicated to this bus, or
   a deliberate rewiring plan -- see this project's originating
   conversation for the options considered. MCTP lives on the target's
   `flexcomm3_lpi2c3`, I2C address `0x10`.
3. The OpenBIC target powered on.

**Software**, on the host PC: same as the sibling project -- Linux,
Python 3.9+ with `venv`, and `dialout` group membership (see the sibling
project's README for the full explanation of that gotcha, identical
here).

## Quick start

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest tests/
```

Until the hardware from above is connected, expect every test to fail
at the `bridge` fixture with a clear "no bridge found" or "endpoint not
found on bus" error -- that's expected, not a code bug (see "Current
status" above).

### Reading the output

Same conventions as the sibling project: `-v -s` output by default, with
verbose wire-level `print()` diagnostics from `bridge.py` on every
command. `xfailed` (via `mctp_helpers.not_implemented()`) means a
confirmed, currently-real gap on the OpenBIC side, not a problem with
this suite -- see the sibling project's README for the full explanation
of why that's tracked as a permanent, visible test rather than skipped.

## Layout

```
bridge.py                Python client for the bridge's text command protocol
                          (near-identical copy of the sibling project's,
                          plus smbus_write()/smbus_read()/smbus_write_read())
mctp.py                  MCTP transport + Control Protocol framing
                          (build/parse), and the SMBus PEC engine
conftest.py               pytest fixture that connects the bridge once per session
tests/config.py           shared constants (target EID/address, our own)
tests/mctp_helpers.py     shared request/response round-trip logic,
                          PEC verification for slave-mode-captured
                          responses, and the not_implemented() marker
tests/test_bus.py         bus presence (everything else assumes this passes)
tests/test_mctp_control.py
                          MCTP Control Protocol commands
tests/test_framing_edge_cases.py
                          checksum/PEC corruption handling
```

## Tests that document unimplemented features

Same mechanism as the sibling project: `mctp_helpers.not_implemented()`
is `pytest.mark.xfail(strict=True)`. A gap gets written as a real test
that's expected to fail, with a `reason` explaining what's missing and
how it's known -- and the moment real support lands and the test starts
passing, the run fails loudly (XPASS) instead of quietly staying green.
See the sibling project's README for the full rationale; it applies here
unchanged.

## Adding tests

Follow the sibling project's conventions: one file per protocol area,
`mctp_helpers.send_mctp_control_command()` for the request/response
round trip, prefer confirming behavior against source (via whoever's
developing the OpenBIC port) or live hardware observation over guessing
at spec-typical behavior -- especially important here given how much of
this repo was necessarily built without hardware access at all (see
"Current status" above). The first real hardware run of any new test
here should be treated with extra scrutiny, not assumed correct just
because it was carefully written.
