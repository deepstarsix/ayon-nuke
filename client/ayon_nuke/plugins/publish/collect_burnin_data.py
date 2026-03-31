import os
import glob

import pyblish.api

from ayon_core.lib import is_oiio_supported
from ayon_core.lib.transcoding import get_oiio_info_for_input


class CollectBurninData(pyblish.api.InstancePlugin):
    """Collect burnin data for review outputs.

    Reads EXR timecode and reel/tape ID from the rendered EXR frames and
    injects them into ``instance.data["burninDataMembers"]`` so they are
    available as ``{exr_timecode}`` and ``{exr_tape_id}`` in burnin templates.

    Also injects the shot description (from the AYON folder entity's
    ``attrib.description``) into ``instance.data["custom_burnin_data"]`` so
    it is available as ``{custom[shot_description]}``.
    """

    order = pyblish.api.CollectorOrder + 0.49
    label = "Collect Burnin Data"
    hosts = ["nuke", "nukeassist"]
    families = ["render", "prerender", "image"]

    def process(self, instance):
        self._collect_exr_metadata(instance)
        self._collect_shot_description(instance)

    # ------------------------------------------------------------------
    # EXR metadata
    # ------------------------------------------------------------------

    def _collect_exr_metadata(self, instance):
        """Read timecode and reel name from the first rendered EXR frame.

        Uses ``get_oiio_info_for_input`` directly instead of going through
        ``ayon_core.plugins.loader.export_otio.get_image_info_metadata``.
        The OTIO loader module imports Qt at module level and therefore fails
        to import in the Nuke publish context, silently swallowing all EXR
        metadata reads.
        """

        if not is_oiio_supported():
            self.log.debug(
                "OpenImageIO is not available; skipping EXR metadata read."
            )
            return

        exr_path = self._resolve_first_exr(instance)
        if not exr_path:
            self.log.debug(
                "No EXR file found on disk (farm render?); "
                "skipping EXR metadata read."
            )
            return

        self.log.debug("Reading EXR metadata from: {}".format(exr_path))

        try:
            info = get_oiio_info_for_input(exr_path, logger=self.log)
        except Exception as exc:
            self.log.warning(
                "oiiotool failed to read '{}': {}".format(exr_path, exc)
            )
            return

        # Flatten all attribs into a single dict, normalising keys to
        # lowercase and stripping the "smpte:" prefix that oiiotool adds to
        # SMPTE-standard metadata (e.g. "smpte:TimeCode" → "timecode").
        attribs = (info or {}).get("attribs") or {}
        metadata = {}
        for key, value in attribs.items():
            normalised = key
            if normalised.lower().startswith("smpte:"):
                normalised = normalised[normalised.lower().index(":") + 1:]
            metadata[normalised.lower()] = value

        self.log.debug(
            "EXR metadata keys found: {}".format(sorted(metadata.keys()))
        )

        if not metadata:
            self.log.debug(
                "No metadata returned from EXR; "
                "exr_timecode and exr_tape_id will not be set."
            )
            return

        burnin_members = instance.data.setdefault("burninDataMembers", {})
        self._inject_burnin_member(
            burnin_members, metadata, "timecode", "exr_timecode"
        )
        self._inject_burnin_member(
            burnin_members, metadata, "reelname", "exr_tape_id"
        )

    def _inject_burnin_member(self, burnin_members, metadata, src_key, dst_key):
        """Inject a single metadata value into burnin members.

        Uses ``setdefault`` so any value already present in *burnin_members*
        is preserved.

        Args:
            burnin_members (dict): The ``burninDataMembers`` dict to update.
            metadata (dict): Flattened attribs from ``get_oiio_info_for_input``.
            src_key (str): Key to look up in *metadata*.
            dst_key (str): Key to set in *burnin_members*.
        """
        value = metadata.get(src_key, "")
        if value:
            burnin_members.setdefault(dst_key, value)
            self.log.debug("Injected {}: {}".format(dst_key, value))
        else:
            self.log.debug(
                "No '{}' key in EXR metadata; "
                "{} will not be set.".format(src_key, dst_key)
            )

    def _resolve_first_exr(self, instance):
        """Return the path to the first non-slate EXR frame for this instance.

        When a slate is present the slate EXR is rendered at
        ``frameStartHandle - 1``.  That frame does not carry the real shot
        timecode/reel metadata, so we always skip it.

        Strategy (in order):
        1. Evaluate ``instance.data["path"] % frameStartHandle`` directly and
           return it if the file already exists on disk.
        2. Glob ``outputDir`` for ``*.exr``, skip the slate frame file, and
           return the first remaining match.
        """

        output_dir = instance.data.get("outputDir", "")
        file_path = instance.data.get("path", "")
        first_frame = (
            instance.data.get("frameStartHandle")
            or instance.data.get("frameStart")
        )

        # Build the slate frame basename so we can filter it out in the glob
        # fallback.  The slate is always rendered one frame before the first
        # handle frame, regardless of whether the "slate" family is present.
        slate_basename = None
        if file_path and first_frame is not None:
            try:
                slate_path = file_path % (int(first_frame) - 1)
                slate_basename = os.path.basename(slate_path)
            except (TypeError, ValueError):
                self.log.debug(
                    "Could not compute slate frame path from '{}' and "
                    "first_frame '{}'; slate filtering disabled.".format(
                        file_path, first_frame
                    )
                )

        # Primary: resolve the exact path for the first handle frame.
        if file_path and first_frame is not None:
            try:
                # file_path uses printf-style frame padding (e.g. %04d)
                candidate = file_path % int(first_frame)
                if os.path.isfile(candidate):
                    return candidate
            except (TypeError, ValueError):
                pass

        # Fallback: glob outputDir and skip the slate frame.
        if output_dir and os.path.isdir(output_dir):
            pattern = os.path.join(output_dir, "*.exr")
            for match in sorted(glob.glob(pattern)):
                if slate_basename and (
                    os.path.basename(match) == slate_basename
                ):
                    self.log.debug(
                        "Skipping slate frame in glob: {}".format(match)
                    )
                    continue
                return match

        return None

    # ------------------------------------------------------------------
    # Shot description
    # ------------------------------------------------------------------

    def _collect_shot_description(self, instance):
        """Inject shot description from the AYON folder entity."""

        folder_entity = instance.data.get("folderEntity") or {}
        attribs = folder_entity.get("attrib") or {}
        description = attribs.get("description", "")

        if not description:
            return

        instance.data.setdefault("custom_burnin_data", {})[
            "shot_description"
        ] = description
        self.log.debug(
            "Injected custom_burnin_data['shot_description']: {}".format(
                description
            )
        )
