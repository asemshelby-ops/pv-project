"""Digital twin of the PV module, scaled to a SIMULATED rooftop (post-paper work).

Everything this package computes is a SIMULATION. No number it prints is a measurement of
our module or of any real plant. The paper (paper/, analysis/), the firmware (firmware/)
and the detectors (detect/) are not changed by it; it only imports from them.

  params.py       every parameter, with its source and tag -- the ONE parameter file
  electrical.py   single-diode module model (De Soto), series strings with bypass diodes
  thermal.py      lumped Faiman thermal model with the spray term (vectorised)
  spray.py        spray cooling, evaporation, pump curve, tank
  rooftop.py      the plant: 120 modules, 6 strings, zones, valves, shared pump
  controllers.py  the five strategies
  compare.py      all strategies on the same weather and rooftop -> report
  fit.py          replaces placeholders by fitted values (params_measured.json)
  validate.py     replay of logger files: twin against measured temperature and power

Documentation: docs/twin/.
"""

SIM_LABEL = "SIMULATION"
SIM_NOTICE = ("SIMULATION - digital twin with placeholder parameters. "
              "Not a measurement; no claim about a real plant.")
