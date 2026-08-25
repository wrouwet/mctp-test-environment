"""Shared constants for the MCTP test suite.

Facts here are confirmed against the peer session developing this
OpenBIC port (meta-facebook/mcx-n9xx-evk, full-board-port branch),
2026-08-25 -- see each constant's comment for what's confirmed vs.
chosen arbitrarily on our own side.

IMPORTANT STATUS NOTE, as of repo creation (2026-08-25): this project
has NOT yet been run against real hardware. The physical second I2C bus
this needs hasn't been wired up yet (MCTP lives on a different bus than
the sibling openbic-test-environment project's IPMB bridge -- the
MCXA153 bridge chip only has one hardware I2C peripheral, so this is a
genuinely separate physical connection, not just a config change). Every
test here is built from confirmed source facts and spec-compliant
framing, not guesses -- but "structurally correct" and "verified against
real hardware" are different claims, and only the former is true right
now. See the README for the full story.
"""

CC_SUCCESS = 0x00

# The MCTP endpoint's I2C/SMBus slave address on the target board's
# flexcomm3_lpi2c3 bus (PLAT_MCTP_I2C_TARGET_ADDR in plat_mctp.h) --
# a DIFFERENT physical bus than the sibling openbic-test-environment
# project's IPMB connection.
MCTP_TARGET_ADDR = 0x10

# The MCTP endpoint's own EID (PLAT_MCTP_EID in plat_mctp.h). This
# changed once already during development (was briefly 0x0A, the
# generic library fallback, before the peer implemented real EID
# support) -- confirm this is still current if anything here doesn't
# behave as expected.
TARGET_EID = 0x09

# Our own I2C address (arbitrary, just needs to not collide with
# anything real on this bus -- chosen independently of, and coincidentally
# identical to, the sibling IPMB project's OUR_IPMB_ADDR; the two are on
# physically separate buses so there's no actual namespace to collide in)
# and our own EID (also arbitrary; 0x08 is the first non-reserved,
# non-broadcast EID per DSP0236 -- EIDs 0x00-0x07 and 0xFF are reserved/
# special).
OUR_I2C_ADDR = 0x08
OUR_EID = 0x08
