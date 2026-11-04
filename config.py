"""All parameters of the synthetic pipeline, in one place.

Every value carries its source tag:
  [CLAUDE.md]  team simulation parameter recorded in CLAUDE.md (illustrative)
  [LIT]        published literature (source given)
  [DS]         datasheet / seller listing
  [EST]        first-pass engineering estimate, to be replaced by measurement
  [ASSUMPTION] chosen for the synthetic data only; not a property of our hardware

Nothing here is a measured property of the BANINUAN 100 W module.
"""

# --- Sampling -------------------------------------------------------------
DT_S = 10.0                 # [CLAUDE.md] one CSV row every 10 s

# --- Thermal model (sim/thermal.py) ---------------------------------------
AREA_M2 = 0.648             # [DS] listing 1200 x 540 mm -> 0.648 m2
HEAT_CAPACITY_J_K = 8700.0  # [EST] C ~ tau*U*A ~ 8.7 kJ/K (CLAUDE.md, Key physics)
FAIMAN_U0 = 25.0            # [LIT] pvlib Faiman default, W/(m2 K)
FAIMAN_U1 = 6.84            # [LIT] pvlib Faiman default, W s/(m3 K)
