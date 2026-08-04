import json

import nuke

from ayon_nuke.api import plugin


class LoadEffects(plugin.NukeGroupLoader):
    """Loading colorspace soft effect exported from nukestudio"""

    product_base_types = {"effect"}
    product_types = product_base_types
    representations = {"*"}
    extensions = {"json"}

    label = "Load Effects - nodes"
    order = 0
    icon = "cc"
    color = "white"

    # Loaded from settings: ayon+settings://nuke/load/LoadEffects
    # Resolution wrap (Reformat sandwich) is only for Input Process —
    # nodes loader inserts into the plate graph at root format.
    enable_resolution_wrap = False
    default_editorial_resolution_width = 1920
    default_editorial_resolution_height = 1080
    spatial_effect_classes = [
        "Transform",
        "Crop",
        "CornerPin",
        "GridWarp",
        "Text",
        "Roto",
        "SplineWarp",
        "AdjustBBox",
    ]

    def on_load(self, group_node, namespace, context):
        assign_to = self._load_effects_to_group(context, group_node=group_node)
        self.connect_read_node(group_node, namespace, assign_to)

    def on_update(self, group_node, namespace, context):
        # Do the exact same os on load
        self.on_load(group_node, namespace, context)
        return group_node

    def connect_read_node(self, group_node, namespace, product_name):
        """
        Finds read node and selects it

        Arguments:
            group_node (nuke.Node): Group node to connect to.
            namespace (str): namespace name to search read node for.
            product_name (str): product name to search read node for.

        Returns:
            nuke node: node is selected
            None: if nothing found
        """
        search_name = "{0}_{1}".format(namespace, product_name)

        read_node = next(
            (
                n for n in nuke.allNodes(filter="Read")
                if search_name in n["file"].value()
            ),
            None
        )

        # Parent read node has been found
        # solving connections
        if read_node:
            dep_nodes = read_node.dependent()

            if len(dep_nodes) > 0:
                for dn in dep_nodes:
                    dn.setInput(0, group_node)

            group_node.setInput(0, read_node)
            group_node.autoplace()

    def _load_effects_to_group(
            self, context: dict, group_node: nuke.Node) -> str:
        """Load the json file and create nodes inside the group node"""

        file = self.filepath_from_context(context).replace("\\", "/")
        with open(file, "r") as f:
            json_f = json.load(f)

        # get correct order of nodes by positions on track and subtrack
        nodes_order = self._reorder_nodes(json_f)
        editorial_resolution = self._get_editorial_resolution(context, json_f)
        wrap_resolution = (
            self.enable_resolution_wrap
            and self._needs_resolution_wrap(nodes_order)
            and not self._resolution_matches_root(editorial_resolution)
        )

        if wrap_resolution:
            self.log.info(
                "Wrapping spatial effects with Reformat nodes: "
                "plate/root -> {width}x{height} -> effects -> root.format".format(
                    **editorial_resolution
                )
            )

        # adding content to the group node
        nuke.endGroup()  # jump out of group if we happen to be in one
        with group_node:
            # first remove all nodes if any in the group
            for node in group_node.nodes():
                nuke.delete(node)
            self._create_nodes_order(
                nodes_order,
                editorial_resolution=editorial_resolution,
                wrap_resolution=wrap_resolution,
            )

        return json_f["assignTo"]

    def _get_editorial_resolution(self, context: dict, json_f: dict) -> dict:
        version_attrs = context.get("version", {}).get("attrib", {})
        width = (
            version_attrs.get("editorialResolutionWidth")
            or json_f.get("editorialResolutionWidth")
            or self.default_editorial_resolution_width
        )
        height = (
            version_attrs.get("editorialResolutionHeight")
            or json_f.get("editorialResolutionHeight")
            or self.default_editorial_resolution_height
        )
        pixel_aspect = (
            version_attrs.get("editorialPixelAspect")
            or json_f.get("editorialPixelAspect")
            or 1.0
        )
        return {
            "width": int(width),
            "height": int(height),
            "pixel_aspect": float(pixel_aspect),
        }

    def _needs_resolution_wrap(self, nodes_order: dict) -> bool:
        spatial_classes = set(self.spatial_effect_classes)
        for effect_data in nodes_order.values():
            effect_class = effect_data.get("class", "")
            for spatial_class in spatial_classes:
                if spatial_class in effect_class:
                    return True
        return False

    def _resolution_matches_root(self, editorial_resolution: dict) -> bool:
        root_format = nuke.root().format()
        return (
            root_format.width() == editorial_resolution["width"]
            and root_format.height() == editorial_resolution["height"]
        )

    def _ensure_nuke_format(
            self, width: int, height: int, pixel_aspect: float) -> str:
        format_name = "ayon_editorial_{width}x{height}".format(
            width=width, height=height
        )
        for existing_format in nuke.formats():
            if existing_format.name() == format_name:
                return format_name

        format_string = "{width} {height} {pixel_aspect:.2f} {name}".format(
            width=width,
            height=height,
            pixel_aspect=pixel_aspect,
            name=format_name,
        )
        nuke.addFormat(format_string)
        return format_name

    def _set_preserve_bbox(self, reformat: nuke.Node) -> None:
        """Enable preserve bounding box on a Reformat node.

        The UI label is "preserve bounding box"; the script knob is ``pbb``
        in most Nuke versions (``preserve_bbox`` in some docs/builds).
        """
        for knob_name in ("pbb", "preserve_bbox"):
            if knob_name in reformat.knobs():
                reformat[knob_name].setValue(True)
                return
        self.log.warning(
            "Reformat node has no preserve-bbox knob; skipping."
        )

    def _create_reformat_to_editorial(
            self, editorial_resolution: dict) -> nuke.Node:
        format_name = self._ensure_nuke_format(
            editorial_resolution["width"],
            editorial_resolution["height"],
            editorial_resolution["pixel_aspect"],
        )
        reformat = nuke.createNode("Reformat", "name Reformat_to_editorial")
        reformat["type"].setValue("format")
        reformat["format"].setValue(format_name)
        reformat["resize"].setValue("width")
        reformat["center"].setValue(True)
        self._set_preserve_bbox(reformat)
        return reformat

    def _create_reformat_to_root(self) -> nuke.Node:
        reformat = nuke.createNode("Reformat", "name Reformat_to_root")
        reformat["type"].setValue("format")
        reformat["format"].setValue(nuke.root()["format"].value())
        self._set_preserve_bbox(reformat)
        return reformat

    def _create_nodes_order(
            self,
            nodes_order: dict,
            editorial_resolution=None,
            wrap_resolution=False):
        workfile_first_frame = int(nuke.root()["first_frame"].getValue())

        # create input node
        pre_node = nuke.createNode("Input")
        pre_node["name"].setValue("rgb")

        if wrap_resolution and editorial_resolution:
            reformat_in = self._create_reformat_to_editorial(
                editorial_resolution
            )
            reformat_in.setInput(0, pre_node)
            pre_node = reformat_in

        for ef_val in nodes_order.values():
            node = nuke.createNode(ef_val["class"])
            for k, v in ef_val["node"].items():
                if k in self.ignore_attr:
                    continue

                # Check if attribute is available
                try:
                    node[k].value()
                except NameError as e:
                    self.log.warning(e)
                    continue

                # Set node attribute values
                if isinstance(v, list) and len(v) > 4:
                    node[k].setAnimated()
                    for i, value in enumerate(v):
                        if isinstance(value, list):
                            for ci, cv in enumerate(value):
                                node[k].setValueAt(
                                    cv,
                                    (workfile_first_frame + i),
                                    ci)
                        else:
                            node[k].setValueAt(
                                value,
                                (workfile_first_frame + i))
                else:
                    node[k].setValue(v)
            node.setInput(0, pre_node)
            pre_node = node

        if wrap_resolution and editorial_resolution:
            reformat_out = self._create_reformat_to_root()
            reformat_out.setInput(0, pre_node)
            pre_node = reformat_out

        # create output node
        output = nuke.createNode("Output")
        output.setInput(0, pre_node)

        return pre_node

    def _reorder_nodes(self, data: dict) -> dict:
        effect_items = {
            key: value for key, value in data.items()
            if isinstance(value, dict) and "trackIndex" in value
        }
        if not effect_items:
            return {}

        track_nums = [v["trackIndex"] for v in effect_items.values()]
        sub_track_nums = [v["subTrackIndex"] for v in effect_items.values()]

        new_order = {}
        for track_index in range(min(track_nums), max(track_nums) + 1):
            for sub_track_index in range(
                    min(sub_track_nums), max(sub_track_nums) + 1):
                item = self._get_item(effect_items, track_index, sub_track_index)
                if item:
                    new_order.update(item)
        return new_order

    def _get_item(
            self, data: dict, track_index: int, sub_track_index: int) -> dict:
        return {key: val for key, val in data.items()
                if isinstance(val, dict)
                if sub_track_index == val["subTrackIndex"]
                if track_index == val["trackIndex"]}



class LoadEffectsInputProcess(LoadEffects):
    """Loading colorspace soft effect exported from nukestudio"""

    label = "Load Effects - Input Process"
    icon = "eye"
    color = "#cc0000"

    # Viewer Input Process runs against the full plate/root format, so
    # spatial effects authored in editorial res need the Reformat wrap.
    enable_resolution_wrap = True

    def on_load(self, group_node, namespace, context):
        # try to place it under Viewer1
        self._load_effects_to_group(context, group_node=group_node)
        if not self.connect_active_viewer(group_node):
            nuke.delete(group_node)
            return

    def on_update(self, group_node, namespace, context):
        # No post-process on update
        # Only overridden to avoid behavior of LoadEffects
        return group_node
