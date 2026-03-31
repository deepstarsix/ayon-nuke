import os
import glob

import pyblish.api

from ayon_core.lib import is_oiio_supported


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
        """Read timecode and reel name from the first rendered EXR frame."""

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

        try:
            from ayon_core.plugins.loader.export_otio import (
                get_image_info_metadata,
            )

            metadata = get_image_info_metadata(exr_path)

            burnin_members = instance.data.setdefault(
                "burninDataMembers", {}
            )

            timecode = metadata.get("timecode", "")
            if timecode:
                # Use setdefault so a value already present (e.g. set by
                # another collector) is not overwritten.
                burnin_members.setdefault("exr_timecode", timecode)
                self.log.debug(
                    "Injected exr_timecode: {}".format(timecode)
                )

            reel = metadata.get("reelname", "")
            if reel:
                # Use setdefault so a value already present is not overwritten.
                burnin_members.setdefault("exr_tape_id", reel)
                self.log.debug(
                    "Injected exr_tape_id: {}".format(reel)
                )

        except Exception as exc:
            self.log.warning(
                "Could not read EXR metadata from '{}': {}".format(
                    exr_path, exc
                )
            )

    def _resolve_first_exr(self, instance):
        """Return the path to the first EXR frame for this instance.

        Tries to construct the path from ``instance.data["path"]`` and
        ``instance.data["frameStartHandle"]`` / ``instance.data["frameStart"]``
        first, then falls back to a glob over ``outputDir``.
        """

        output_dir = instance.data.get("outputDir", "")

        # Try to evaluate the write node path for the first frame
        file_path = instance.data.get("path", "")
        first_frame = (
            instance.data.get("frameStartHandle")
            or instance.data.get("frameStart")
        )

        if file_path and first_frame is not None:
            try:
                # file_path uses printf-style frame padding (e.g. %04d)
                candidate = file_path % int(first_frame)
                if os.path.isfile(candidate):
                    return candidate
            except (TypeError, ValueError):
                pass

        # Fall back: find the first .exr in outputDir
        if output_dir and os.path.isdir(output_dir):
            pattern = os.path.join(output_dir, "*.exr")
            matches = sorted(glob.glob(pattern))
            if matches:
                return matches[0]

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
