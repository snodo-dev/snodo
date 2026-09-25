"""Versions of the engine-to-cloud wire interface."""

CLOUD_INTERFACE_V5 = 5
CLOUD_INTERFACE_V6 = 6

# The current interface contract. Sending still uses the v5 models until the
# cloud's version-negotiation rollout is implemented.
CLOUD_INTERFACE_VERSION = CLOUD_INTERFACE_V6
