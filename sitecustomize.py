"""Project-local Python startup adjustments."""

import os

if os.name == "nt":
    import platform

    def _disable_wmi_query(*_args):
        raise OSError("WMI disabled for this project")

    platform._wmi_query = _disable_wmi_query
