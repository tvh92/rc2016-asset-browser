"""Arrange a node tree in columns, built into the pipeline so no Blender add-on is needed.

Each node's column is the length of the longest chain of links from it to a node with no outgoing
link, so the outputs sit in the rightmost column and every input is to the left of what it feeds.
Within a column, nodes keep their creation order, stacked top to bottom. The maths is plain Python
(``layout_positions``) so it can be tested outside Blender; ``arrange_tree`` applies it to a live tree.
"""
from __future__ import annotations

from collections import defaultdict

COLUMN_GAP = 90          # space between the right edge of one column and the left edge of the next
ROW_GAP = 40             # space between nodes stacked in one column
DEFAULT_WIDTH = 240.0
MIN_HEIGHT = 120.0
SOCKET_HEIGHT = 22.0     # rough height per socket row, to size nodes that report no height
HEADER_HEIGHT = 40.0
SWEEPS = 8               # passes that move nodes toward their links (see layout_positions)


def layout_positions(node_ids, links, sizes):
    """Locations for ``node_ids`` (creation order) given ``links`` as (from_id, to_id) pairs and
    ``sizes`` as {id: (width, height)}. Returns {id: (x, y)}; the rightmost column starts at x = 0 and
    columns extend to the left; y grows downwards and is the negative of the stacked height."""
    node_ids = list(node_ids)
    order = {node: index for index, node in enumerate(node_ids)}
    downstream = defaultdict(list)
    for source, target in links:
        if source in order and target in order and source != target:
            downstream[source].append(target)
    depth = {}

    def column_of(node, trail=()):
        # Longest path to a sink; a cycle (not made by the builder) is cut rather than followed forever.
        if node in depth:
            return depth[node]
        if node in trail:
            return 0
        found = [column_of(target, trail + (node,)) + 1 for target in downstream[node]]
        depth[node] = max(found) if found else 0
        return depth[node]

    for node in node_ids:
        column_of(node)
    columns = defaultdict(list)
    for node in node_ids:
        columns[depth[node]].append(node)
    width_of = {node: sizes.get(node, (DEFAULT_WIDTH, MIN_HEIGHT))[0] for node in node_ids}
    column_width = {column: max(width_of[node] for node in members) for column, members in columns.items()}
    x_right = 0.0
    x_edge = {}
    for column in sorted(columns):
        x_edge[column] = x_right - column_width[column]
        x_right = x_edge[column] - COLUMN_GAP
    heights = {node: sizes.get(node, (DEFAULT_WIDTH, MIN_HEIGHT))[1] for node in node_ids}
    # Start from a plain stack, then sweep the columns: each node moves toward the middle of the nodes
    # it is linked to, and each column is packed again in that order. Wires get shorter and fewer cross.
    tops = {}
    for members in columns.values():
        y = 0.0
        for node in members:
            tops[node] = y
            y -= heights[node] + ROW_GAP
    neighbours = defaultdict(set)
    for source, target in links:
        if source in order and target in order and source != target:
            neighbours[source].add(target)
            neighbours[target].add(source)
    ordered_columns = sorted(columns)
    for sweep in range(SWEEPS):
        for column in (ordered_columns if sweep % 2 == 0 else reversed(ordered_columns)):
            members = columns[column]
            centre = {node: tops[node] - heights[node] / 2 for node in members}
            for node in members:
                linked = [tops[other] - heights[other] / 2 for other in neighbours[node]]
                if linked:
                    centre[node] = sum(linked) / len(linked)
            bottom = None
            for node in sorted(members, key=lambda n: centre[n], reverse=True):
                top = centre[node] + heights[node] / 2
                if bottom is not None:
                    top = min(top, bottom - ROW_GAP)
                tops[node] = top
                bottom = top - heights[node]
    highest = max(tops.values())
    positions = {}
    for column, members in columns.items():
        for node in members:
            positions[node] = (x_edge[column], tops[node] - highest)
    return positions


def _size(node):
    """Width and height of a node: Blender's own values when it has them, else estimated from its sockets."""
    width = float(getattr(node, 'width', 0) or 0) or DEFAULT_WIDTH
    height = float(getattr(node, 'height', 0) or 0)
    if height <= 0:
        sockets = max(len([s for s in node.inputs if s.enabled]), len([s for s in node.outputs if s.enabled]))
        height = max(MIN_HEIGHT, HEADER_HEIGHT + SOCKET_HEIGHT * sockets)
    return width, height


def arrange_tree(tree):
    """Move the nodes of a live Blender node tree into columns (frames are left where they are)."""
    nodes = [node for node in tree.nodes if node.type != 'FRAME']
    if not nodes:
        return 0
    index = {node.as_pointer(): node for node in nodes}
    links = []
    for link in tree.links:
        source, target = link.from_node.as_pointer(), link.to_node.as_pointer()
        if source in index and target in index:
            links.append((source, target))
    sizes = {node.as_pointer(): _size(node) for node in nodes}
    positions = layout_positions([node.as_pointer() for node in nodes], links, sizes)
    for pointer, (x, y) in positions.items():
        index[pointer].location = (x, y)
    return len(positions)
