"""Ověří, že spuštěný Python TickView podporuje (návratový kód 0 = ano, 1 = ne).

Spouštěcí skripty run.sh a run-windows.bat jím zkoušejí kandidáty
(python3.13, py -3.13 …) i Python ve .venv. Kód je záměrně jednoduchý,
aby se dal spustit i starým Pythonem 3.
"""

import sys

# Od 3.12 (vyžaduje pandas-ta) do 3.13 včetně – pandas-ta pinuje numba==0.61.2,
# která Python 3.14 nepodporuje. Horní mez zvednout, až pandas-ta pin uvolní
# (pip show pandas-ta); zároveň upravit seznamy kandidátů v run.sh
# a run-windows.bat a verze k automatické instalaci (PY_VERSION
# v run-windows.bat, PBS_PYTHON a PBS_RELEASE v run.sh).
MIN_PYTHON = (3, 12)
MAX_PYTHON_EXCLUSIVE = (3, 14)

sys.exit(0 if MIN_PYTHON <= sys.version_info[:2] < MAX_PYTHON_EXCLUSIVE else 1)
