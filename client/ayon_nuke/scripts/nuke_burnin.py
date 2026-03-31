"""Nuke-specific burnin wrapper script.

Extends the standard ayon-core ``otio_burnin.py`` by pre-processing burnin
template values so that ``{exr_timecode}`` behaves exactly like
``{source_timecode}``: an auto-incrementing timecode rendered via ffmpeg's
``drawtext=timecode=`` filter.

How it works
------------
``CollectBurninData`` reads the timecode from the rendered EXR file and
stores it in two places inside ``instance.data["burninDataMembers"]``:

* ``exr_timecode``  – the raw SMPTE string (e.g. "01:00:00:00"), kept for
  static / reference use.
* ``frame_start_tc`` – the same SMPTE string.  ayon-core's
  ``burnins_from_data`` reads this key and uses it as the starting value for
  the ``{timecode}`` drawtext filter, which ffmpeg auto-increments per frame.

This script pre-processes the burnin *values* dict (the per-position template
strings) before passing them to ``burnins_from_data``: any occurrence of
``{exr_timecode}`` is replaced with ``{timecode}``.  Because ``frame_start_tc``
is already present in the data dict, ``burnins_from_data`` will automatically
drive the replaced ``{timecode}`` through the ``drawtext=timecode=`` ffmpeg
filter, giving per-frame auto-increment – identical to how ``{source_timecode}``
works.

``{source_timecode}`` is left completely untouched.
"""

import importlib.util
import json
import os
import sys

EXR_TIMECODE_KEY = "{exr_timecode}"
TIMECODE_KEY = "{timecode}"


def _load_burnins_from_data():
    """Import ``burnins_from_data`` from ayon-core's ``otio_burnin.py``."""
    from ayon_core import AYON_CORE_ROOT

    script_path = os.path.normpath(
        os.path.join(AYON_CORE_ROOT, "scripts", "otio_burnin.py")
    )
    spec = importlib.util.spec_from_file_location("otio_burnin", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.burnins_from_data


def _preprocess_exr_timecode(in_data):
    """Replace ``{exr_timecode}`` with ``{timecode}`` in burnin template values.

    The replacement is only performed when ``frame_start_tc`` is present in
    ``burnin_data`` (injected by ``CollectBurninData`` from the EXR metadata).
    If that key is absent there is nothing to drive the timecode filter with,
    so the original values are returned unchanged.
    """
    burnin_data = in_data.get("burnin_data") or {}
    burnin_values = in_data.get("values") or {}

    if not burnin_data.get("frame_start_tc"):
        return in_data

    if not any(
        isinstance(v, str) and EXR_TIMECODE_KEY in v
        for v in burnin_values.values()
    ):
        return in_data

    new_values = {
        pos: (
            val.replace(EXR_TIMECODE_KEY, TIMECODE_KEY)
            if isinstance(val, str)
            else val
        )
        for pos, val in burnin_values.items()
    }

    new_data = dict(in_data)
    new_data["values"] = new_values
    return new_data


if __name__ == "__main__":
    print("* Nuke burnin script started")

    in_data_json_path = sys.argv[-1]
    with open(in_data_json_path, "r") as fh:
        in_data = json.load(fh)

    in_data = _preprocess_exr_timecode(in_data)

    burnins_from_data = _load_burnins_from_data()
    burnins_from_data(
        in_data["input"],
        in_data["output"],
        in_data["burnin_data"],
        codec_data=in_data.get("codec"),
        options=in_data.get("options"),
        burnin_values=in_data.get("values"),
        full_input_path=in_data.get("full_input_path"),
        first_frame=in_data.get("first_frame"),
        source_ffmpeg_cmd=in_data.get("ffmpeg_cmd"),
    )

    print("* Nuke burnin script has finished")
