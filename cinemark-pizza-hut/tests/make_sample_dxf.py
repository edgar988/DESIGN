"""Build a synthetic store layout DXF that follows docs/DXF_STANDARD.md (used by the tests)."""
import ezdxf


def rect_block(doc, name, w, d, attribs=("ITEM",)):
    b = doc.blocks.new(name)
    b.add_lwpolyline([(0, 0), (w, 0), (w, d), (0, d)], close=True)
    for i, tag in enumerate(attribs):
        b.add_attdef(tag, (2, 2 + 4 * i), dxfattribs={"height": 3})
    return b


def wall(msp, layer, x1, y1, x2, y2, t=4.875):
    import math
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy)
    nx, ny = -dy / L * t / 2, dx / L * t / 2
    msp.add_line((x1 + nx, y1 + ny), (x2 + nx, y2 + ny), dxfattribs={"layer": layer})
    msp.add_line((x1 - nx, y1 - ny), (x2 - nx, y2 - ny), dxfattribs={"layer": layer})


def build(path):
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    for ly in ("A-WALL", "A-WALL-DEMO", "A-WALL-NEW", "A-AREA", "Q-EQPM", "Q-EQPM-DEMO",
               "E-PANEL", "P-SOURCE", "A-DOOR-NEW"):
        doc.layers.add(ly)
    # 25'-0" x 12'-0" room = 300 SF
    W, D = 300, 144
    wall(msp, "A-WALL", 0, 0, W, 0)
    wall(msp, "A-WALL", W, 0, W, D)
    wall(msp, "A-WALL", W, D, 0, D)
    wall(msp, "A-WALL", 0, D, 0, 0)
    wall(msp, "A-WALL-DEMO", 120, 0, 120, 60)          # 5' stub to demo
    wall(msp, "A-WALL-NEW", 200, 144, 200, 84)          # 5' new partition
    msp.add_lwpolyline([(0, 0), (W, 0), (W, D), (0, D)], close=True, dxfattribs={"layer": "A-AREA"})

    blocks = {
        "KCL_OVENTION_C2000": (46, 40), "AQ-ACP-MXP22TLT": (22, 27), "HOBART LXnR": (24, 26),
        "FC-3-2030-20RL": (96, 30), "7-PS-65": (17, 15), "G22010": (52, 36), "UR48B": (48, 30),
        "FSS-308": (96, 30), "MYSTERY_BLOCK_X": (20, 20), "OLD_POPPER": (36, 24), "DOOR36": (36, 4),
    }
    for n, (w, d) in blocks.items():
        rect_block(doc, n, w, d)
    place = [("KCL_OVENTION_C2000", 10, 100, "7", "Q-EQPM"), ("AQ-ACP-MXP22TLT", 60, 110, "6", "Q-EQPM"),
             ("HOBART LXnR", 140, 110, "13", "Q-EQPM"), ("FC-3-2030-20RL", 170, 110, "1", "Q-EQPM"),
             ("7-PS-65", 270, 120, "5", "Q-EQPM"), ("G22010", 240, 10, "12", "Q-EQPM"),
             ("UR48B", 10, 10, "9", "Q-EQPM"), ("FSS-308", 60, 10, "10", "Q-EQPM"),
             ("MYSTERY_BLOCK_X", 180, 40, "", "Q-EQPM"), ("OLD_POPPER", 150, 20, "", "Q-EQPM-DEMO"),
             ("DOOR36", 280, 0, None, "A-DOOR-NEW")]
    for n, x, y, item, ly in place:
        ref = msp.add_blockref(n, (x, y), dxfattribs={"layer": ly})
        if item is not None:
            ref.add_auto_attribs({"ITEM": item})
    msp.add_point((150, 140), dxfattribs={"layer": "E-PANEL"})
    msp.add_point((290, 140), dxfattribs={"layer": "P-SOURCE"})
    doc.saveas(path)
    return path


if __name__ == "__main__":
    import sys
    build(sys.argv[1] if len(sys.argv) > 1 else "sample_store.dxf")
