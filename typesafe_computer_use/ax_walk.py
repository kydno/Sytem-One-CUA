"""The bounded accessibility-tree walk, platform-free.

Takes children/attrs/actions as plain callables, so macos.py and windows.py each supply their own
bridge to AXUIElement or UI Automation, but the pruning, de-duplication, and label-recovery rules
live here once. Role strings follow the "AX*" vocabulary macOS's accessibility API uses; a Windows
adapter maps its own control types onto the same names so this module needs no per-platform branch.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterable
from typing import NamedTuple

from .models import AxNode

AX_PRESS = "AXPress"  # sentinel an actions() callable emits when a node accepts a press/invoke

AX_ACTIONABLE_ROLES = {
    "AXButton",
    "AXCell",
    "AXCheckBox",
    "AXComboBox",
    "AXDisclosureTriangle",
    "AXImage",
    "AXIncrementor",
    "AXLink",
    "AXMenuBarItem",
    "AXMenuButton",
    "AXPopUpButton",
    "AXRadioButton",
    "AXRow",
    "AXSearchField",
    "AXSlider",
    "AXTab",
    "AXTextArea",
    "AXTextField",
}
# A bare child, usually a decorative AXImage, borrows the label of a parent that is itself a control.
AX_LABEL_PARENT_ROLES = {
    "AXButton",
    "AXCell",
    "AXCheckBox",
    "AXLink",
    "AXMenuButton",
    "AXPopUpButton",
    "AXRadioButton",
    "AXRow",
    "AXTab",
}
# List containers keep their label in a shallow AXStaticText rather than on themselves.
AX_LABEL_DESCENDANT_ROLES = {"AXCell", "AXRow"}
AX_SKIP_SUBTREE_ROLES = {"AXMenu"}  # a closed menu: thousands of zero-sized items, none on screen
AX_NODE_CAP = 4000
AX_TIME_CAP = 0.6
AX_OFFSCREEN_CAP = 120  # off-screen controls collected before the walk stops looking for more
AX_MIN_SIDE_PT = 4.0  # anything thinner is a Chromium sliver for a scrolled-out node
AX_FANOUT = 8  # children scanned per level when recovering a label
# A modal dialog on a web page, handed back among the controls under this role so every adapter keeps its shape.
# It is no control: perception takes it out again (see `walk_actionable`).
AX_MODAL = "AXModal"
DIALOG_SUBROLES = {"AXApplicationDialog", "AXApplicationAlertDialog", "AXDialog"}

Frame = tuple[float, float, float, float]  # x, y, w, h in points


class AxAttrs(NamedTuple):
    role: str
    label: str
    frame: Frame | None
    dialog: bool = False  # a dialog of a web page (ARIA role dialog); only the macOS adapter reports it


def off_display(frame: Frame | None, display_w_pt: float, display_h_pt: float) -> bool:
    """True when a real frame lies wholly outside the display: a note list thousands of screens down,
    or a web node the browser parked above the viewport. A zero-size frame claims nothing, which is
    what an application element and a closed menu report, so their subtrees are still worth a look.
    """
    if frame is None:
        return False
    x, y, w, h = frame
    if w <= 0 or h <= 0:
        return False
    return x >= display_w_pt or y >= display_h_pt or x + w <= 0 or y + h <= 0


def center_on_display(frame: Frame | None, display_w_pt: float, display_h_pt: float) -> bool:
    """Whether a frame's centre falls on the display. Chromium pages report a giant `shell` frame
    that starts tens of thousands of points above the viewport yet crosses it, so `off_display`
    alone lets it through as partially visible, and clicking it would aim far off the screen.
    A frameless or zero-size frame claims nothing (an application element, a closed menu), and its
    centre is meaningless, so those pass.
    """
    if frame is None:
        return True
    x, y, w, h = frame
    if w <= 0 or h <= 0:
        return True
    return 0 <= x + w / 2 < display_w_pt and 0 <= y + h / 2 < display_h_pt


def node_identity(node) -> object:
    """Accessibility elements hash by the element they wrap, so two fetches of one control compare
    equal; anything unhashable (a fake node in a test) falls back to object identity."""
    try:
        hash(node)
    except TypeError:
        return ("id", id(node))
    return node


def subtree_key(role: str, label: str, frame: Frame | None) -> tuple | None:
    """Identity of a node for de-duplication: same role, label and frame is the same control, whatever
    object the bridge wrapped it in. Frameless, zero-size and nameless nodes are containers and are
    never keyed: a container is no control, and the labelled controls under it carry keys of their own.

    A nameless key once dropped every web page in Brave and Chrome. The browser holds the page in a
    nameless group with the page's own frame, and the page's first container is a nameless group of
    that same frame, so it read as a copy and the walk skipped the page beneath it."""
    if not label or frame is None or frame[2] <= 0 or frame[3] <= 0:
        return None
    return (role, label, round(frame[0]), round(frame[1]), round(frame[2]), round(frame[3]))


def clickable(frame: Frame | None) -> bool:
    return frame is not None and min(frame[2], frame[3]) >= AX_MIN_SIDE_PT


def descendant_label(kids: list, children: Callable, attrs: Callable[..., AxAttrs]) -> str:
    """The first static text within two levels, which is where list rows hide their label."""
    for kid in kids[:AX_FANOUT]:
        role, label = attrs(kid)[:2]
        if role == "AXStaticText" and label:
            return label
    for kid in kids[:AX_FANOUT]:
        for grandkid in list(children(kid))[:AX_FANOUT]:
            role, label = attrs(grandkid)[:2]
            if role == "AXStaticText" and label:
                return label
    return ""


def walk_actionable(
    root,
    children: Callable[..., Iterable],
    attrs: Callable[..., AxAttrs],
    actions: Callable[..., Iterable[str]],
    display_w_pt: float,
    display_h_pt: float,
    node_cap: int = AX_NODE_CAP,
    time_cap: float = AX_TIME_CAP,
    offscreen_cap: int = AX_OFFSCREEN_CAP,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[AxNode], list[AxNode], bool]:
    """Breadth-first hunt for labelled controls: the on-screen ones, the reachable off-screen ones,
    and whether a cap cut the walk short.

    The four callables are the only way into the tree, so the pruning rules are platform-free
    and testable against a plain dict. The caps are the point: an unbounded walk of a note list
    or a long web page costs seconds and finds nothing on screen.

    A node that misses the display, or that the app clamped to a sliver, is not on screen and is
    not offered as one: its subtree stays pruned from `found`. But AXPress does not need a node to
    be visible, so a labelled one that accepts the action is collected separately, down to
    `offscreen_cap`, after which those subtrees are dropped again and the walk is the old one.

    A web page's modal dialog comes back among the controls as one `AX_MODAL` node, its page in `page`.
    Chromium leaves every element outside an aria-modal dialog out of the tree, so a dialog is taken as
    modal when no control of its page lies outside it, and only on a walk no cap cut short. The page
    outside it is then under the dialog's backdrop: text OCR still reads there, such as a badge on a
    card behind a filter dialog, is no control, and a click on it closes the dialog and drops every
    choice made in it.
    """
    found: list[AxNode] = []
    offscreen: list[AxNode] = []
    areas: list[Frame] = []  # the web pages walked, by index
    found_area: list[int | None] = []  # the page of each found control
    dialogs: list[tuple[str, Frame, int]] = []
    deadline = clock() + time_cap
    queue = deque([(root, "", False, False, None, None)])
    seen = 0
    visited: set = set()  # elements compare by identity across fetches, so a self-listing app is walked once
    visited_keys: set[tuple] = set()  # and a control handed over as several distinct objects is kept once
    while queue:
        if seen >= node_cap or clock() >= deadline:
            return found, offscreen, True
        node, parent_label, parent_emitted, hidden, parent_key, area = queue.popleft()
        identity = node_identity(node)
        if identity in visited:
            continue
        visited.add(identity)
        seen += 1
        node_attrs = attrs(node)
        role, own_label, frame = node_attrs.role, node_attrs.label, node_attrs.frame
        if role in AX_SKIP_SUBTREE_ROLES:
            continue
        key = subtree_key(role, own_label, frame)
        # A node keyed like its own parent wraps it, as AT-SPI nests nameless panels of one frame:
        # it is no copy from elsewhere, so its subtree is walked, but it is never a second item.
        wrapper = key is not None and key == parent_key
        if key is not None and not wrapper:
            if key in visited_keys:
                continue
            visited_keys.add(key)
        hidden = hidden or off_display(frame, display_w_pt, display_h_pt)
        if hidden and len(offscreen) >= offscreen_cap:
            continue  # nothing left to collect down there, and it never counted on screen
        if role == "AXWebArea" and frame is not None and not hidden:
            area = len(areas)
            areas.append(frame)
        if node_attrs.dialog and area is not None and not hidden and clickable(frame):
            dialogs.append((own_label, frame, area))
        kids = list(children(node))
        label, inherited = own_label, False
        if not label and role in AX_LABEL_DESCENDANT_ROLES:
            label = descendant_label(kids, children, attrs)
        if not label and parent_label:
            label, inherited = parent_label, True
        emitted = False
        duplicate = inherited and parent_emitted  # the parent already stands for this label
        nameless_group = role == "AXGroup" and not own_label  # a Chromium layout box, not a control
        # A click lands on the centre, so a node centred off the display is no item, though it
        # crosses the display. It stays reachable by AXPress, and its children are judged as their own.
        visible = not hidden and clickable(frame) and center_on_display(frame, display_w_pt, display_h_pt)
        if label and not duplicate and not nameless_group and not wrapper:
            if visible:
                pressable = AX_PRESS in actions(node)
                if pressable or role in AX_ACTIONABLE_ROLES:
                    x, y, w, h = frame
                    found.append(AxNode(role=role, label=label, x=x, y=y, w=w, h=h, pressable=pressable, ref=node))
                    found_area.append(area)
                    emitted = True
            elif frame is not None and len(offscreen) < offscreen_cap and AX_PRESS in actions(node):
                x, y, w, h = frame
                offscreen.append(AxNode(role=role, label=label, x=x, y=y, w=w, h=h, pressable=True, ref=node))
        child_label = own_label if role in AX_LABEL_PARENT_ROLES else ""
        queue.extend((kid, child_label, emitted, hidden, key, area) for kid in kids)
    return found + modal_dialogs(dialogs, areas, found, found_area), offscreen, False


def modal_dialogs(
    dialogs: list[tuple[str, Frame, int]], areas: list[Frame], found: list[AxNode], found_area: list[int | None]
) -> list[AxNode]:
    """The dialogs no control of their own page lies outside of, each as an `AX_MODAL` node (see `walk_actionable`)."""
    out = []
    for label, frame, area in dialogs:
        x, y, w, h = frame
        outside = any(
            page == area and not (x <= n.x + n.w / 2 < x + w and y <= n.y + n.h / 2 < y + h)
            for n, page in zip(found, found_area, strict=True)
        )
        if not outside:
            out.append(AxNode(role=AX_MODAL, label=label, x=x, y=y, w=w, h=h, pressable=False, page=areas[area]))
    return out
