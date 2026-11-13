"""SCADA layer for the PV rig (post-paper work, branch scada-prep).

Read-only on the logger's side: it reads the files the logger already writes (the day
CSV files, /nodate.csv, /markers.csv) and its serial output, and never writes to the
board. Nothing in firmware/ or analysis/ is changed by this package.
Architecture and how to run it: docs/scada/.
"""
