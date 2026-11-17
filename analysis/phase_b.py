"""Phase B: the randomized spray trial, from the logger's rows to the comparison of the
two arms. NOT ADOPTED: prepared 2026-11-17, before any pump exists, and tested on
SYNTHETIC data only (analysis/fake_phase_b.py). Design and limits: docs/phase-b/02.

What the logger gives (firmware built with PHASE_B_ENABLED 1; data/schema.py):
  spike_id, spray_assign                      which rows belong to which event and arm
  pump_duty, flow_Lpm, water_L_cum, P_pump_W  10 s means / the running litre count
  /spray_log.csv                              boot lines (seed, counters, dry run), every
                                              event, every alarm that opened none

What this module computes:
  allocation()       the arm of event n, the same function as the firmware's spray_alloc()
  event_table()      one row per event: arm, water, pump energy, spray time, module energy
                     over the spike window per unit of irradiation, the same over the event's
                     own baseline, validity
  arm_comparison()   spray against no-spray, side by side; difference with its interval and a
                     permutation test; gain, pump cost, net, litres per kWh
  cleaning_check()   I_sc / G before and after each event from the nearest sweeps, per arm:
                     the part of a power change that is cleaner glass, not a cooler module
  audit()            every logged arm against the seed

Why the comparison is between ARMS and not "before and after a spray". A spray happens
during a spike: the module would have kept heating, the sun may be changing, and a
re-heating follows every cooling. The no-spray arm is the same kind of event left alone,
chosen by chance, so the difference between the arms is the effect of spraying and of
nothing else. Every quantity is therefore computed in the same way for both arms.
"""
import math
import re

import numpy as np
import pandas as pd

import config

MASK = 0xFFFFFFFF


# --- allocation: the firmware's spray_alloc(), bit for bit --------------------------------

def _mix32(x):
    """Integer hash "lowbias32" (pv_spray_logic.c, mix32)."""
    x &= MASK
    x ^= x >> 16
    x = (x * 0x7FEB352D) & MASK
    x ^= x >> 15
    x = (x * 0x846CA68B) & MASK
    x ^= x >> 16
    return x


def allocation(seed, n, block=config.PHASE_B_BLOCK_SIZE):
    """Arm (1 = spray, 0 = no-spray) of event number n (0, 1, 2 ...) of the sequence `seed`.
    Block n // block holds block/2 ones and block/2 zeros in an order drawn by a
    Fisher-Yates shuffle whose random numbers depend only on (seed, block number)."""
    if block < 2 or block % 2:
        raise ValueError("the block size must be even and at least 2")
    b, k = divmod(int(n), block)
    arm = [1 if i < block // 2 else 0 for i in range(block)]
    s = _mix32((seed & MASK) ^ _mix32((b + 0x9E3779B9) & MASK))
    for i in range(block - 1, 0, -1):
        bound = i + 1
        reject = (2 ** 32 - bound) % bound          # these r would bias j
        while True:
            s = (s + 0x9E3779B9) & MASK
            r = _mix32(s)
            if r >= reject:
                break
        j = r % bound
        arm[i], arm[j] = arm[j], arm[i]
    return arm[k]


def dry_seed(seed):
    """Seed of the dry-run sequence (pv_spray_logic.c, dry_seed)."""
    return _mix32((seed & MASK) ^ 0xD5A1D5A1)


def expected_assign(spike_id, seed, block=config.PHASE_B_BLOCK_SIZE):
    """Arm the firmware must have drawn for a logged event. Wet events are numbered
    1, 2, 3 ...; dry-run events -1, -2, -3 ... from their own sequence."""
    spike_id = int(spike_id)
    if spike_id == 0:
        raise ValueError("spike_id 0 means no event")
    n = abs(spike_id) - 1
    return allocation(dry_seed(seed) if spike_id < 0 else seed, n, block)
