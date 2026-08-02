"""Image recovery pipeline for the StackOverflow ZIM rebuild.

Stage 1: recovery library + core modules (inventory, classify, dump scanners,
IA manifest builder). All network-capable code is gated behind an explicit
``--fetch`` flag; dry-run (zero network, zero payload writes) is the default.
"""

__version__ = "0.1.0"