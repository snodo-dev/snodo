"""Versions of the engine-to-cloud wire interface."""

CLOUD_INTERFACE_V5 = 5
CLOUD_INTERFACE_V6 = 6
CLOUD_INTERFACE_V7 = 7
CLOUD_INTERFACE_V8 = 8

# The current interface contract. Sending still uses the v5 models until the
# cloud's version-negotiation rollout is implemented.
CLOUD_INTERFACE_VERSION = CLOUD_INTERFACE_V8
