"""Helper scripts run in a subprocess against one Zephyr tree.

They are executed as files (not imported as part of zmig) so that each run loads
that tree's own kconfiglib / edtlib from its scripts/ directory. Two versions of
those libraries cannot coexist in one process, and edt.pickle can only be loaded
by the edtlib that wrote it.
"""
