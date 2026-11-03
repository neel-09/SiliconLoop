"""Deterministic pin allocator: intent list + board facts -> wiring JSON.

Same board + same intents always give the same wiring. No model output decides a pin.

Intent:  {"id": "led1", "part": "led", "options": {"color": "red", "dimmable": false, "use": ["do"]}}
         options.use lists optional part pins to connect (e.g. a sound sensor's digital output).
Wiring:  {"board", "parts": [{id, part, options, pins}], "extras": [resistors],
          "nets": [{"name", "nodes": ["board.D4", "r1.1", ...]}], "pin_map", "libraries"}
Unknown parts (e.g. built from a Tavily datasheet by parts.py) are passed in as extra_cards.
"""
import json
import math
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
BOARDS = {k: v for k, v in json.loads((DATA / "boards.json").read_text()).items() if not k.startswith("_")}
_raw_parts = json.loads((DATA / "parts.json").read_text())
PARTS = {k: v for k, v in _raw_parts.items() if not k.startswith("_")}
GENERIC = _raw_parts["_generic"]

E12 = [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2]
# Allocation order: most constrained roles first.
ROLE_ORDER = ["i2c_sda", "i2c_scl", "analog_in", "pwm_out", "servo", "digital_in", "digital_out"]


class AllocationError(ValueError):
    pass


def e12_ceil(ohms):
    """Smallest standard E12 value >= ohms."""
    decade = 10 ** math.floor(math.log10(ohms))
    for m in E12 + [10.0]:
        if m * decade >= ohms - 1e-9:
            return int(round(m * decade))


def series_vf(card, entry, options):
    """Forward voltage of a series entry: a number, or {"option", "table", "default"} looked up by an intent option
    (e.g. an LED's colour)."""
    vf = entry.get("vf", 0.0)
    if isinstance(vf, dict):
        vf = vf["table"].get(str(options.get(vf["option"]) or vf.get("default_option", "")), vf["default"])
    return vf


def series_ohms(card, entry, options, supply_v=5.0):
    """Resistor for one series entry: fixed ohms, or computed from the forward voltage and target current."""
    if "ohms" in entry:
        return entry["ohms"]
    return e12_ceil((supply_v - series_vf(card, entry, options)) / (entry["ma"] / 1000))


def role_of(spec, options):
    """A pin's role; 'role_if_option': {option: role} switches it when that intent option is true."""
    for opt, role in (spec.get("role_if_option") or {}).items():
        if options.get(opt):
            return role
    return spec["role"]


def conflict_pins(board, libraries):
    """PWM pins a board's timer conflicts disable for the libraries in use (boards.json 'conflicts')."""
    return [d for c in board["conflicts"] if f"{c['when_library']}.h" in libraries for d in c["disables_pwm"]]


def active_pins(card, options):
    """Part pins that get connected: all rails and required signals, plus optional signals listed in options.use."""
    use = set(options.get("use", []))
    return {p: s for p, s in card["pins"].items() if not s.get("optional") or p in use}


def i2c_pins(board):
    sda = next(p for p, c in board["pins"].items() if "i2c_sda" in c)
    scl = next(p for p, c in board["pins"].items() if "i2c_scl" in c)
    return sda, scl


def _pools(board, libraries, uses_i2c):
    pins = board["pins"]
    i2c = set(i2c_pins(board)) if uses_i2c else set()
    usable = [p for p in pins if p not in board["reserved"] and p not in i2c]
    servo_dead = conflict_pins(board, libraries)
    digital = [p for p in usable if "digital" in pins[p]]
    plain = [p for p in digital if p.startswith("D") and "pwm" not in pins[p]]
    pwm = [p for p in digital if "pwm" in pins[p] and p not in servo_dead]
    analog_as_digital = [p for p in digital if p.startswith("A")]
    analog = [p for p in usable if "analog" in pins[p]]
    no_input = set(board.get("avoid_for_input", {}))
    # Servo-role pins go first on the pins whose PWM a timer-hungry library kills anyway.
    servo_first = [p for p in servo_dead if p in digital]
    return {
        "analog_in": analog,
        "pwm_out": pwm,
        "servo": servo_first + plain + pwm + analog_as_digital,
        "digital_in": [p for p in plain + pwm + analog_as_digital if p not in no_input],
        "digital_out": plain + pwm + analog_as_digital,
    }


def allocate(board_key, intents, extra_cards=None):
    if board_key not in BOARDS:
        raise AllocationError(f"Unknown board '{board_key}'. Choose one of: {', '.join(BOARDS)}.")
    board = BOARDS[board_key]
    cards = {**PARTS, **(extra_cards or {})}

    ids = [i["id"] for i in intents]
    if len(ids) != len(set(ids)):
        raise AllocationError("Every part needs a unique id.")
    for i in intents:
        if i["part"] not in cards:
            raise AllocationError(f"'{i['part']}' is not a supported part yet.")
        unknown = set(i.get("options", {}).get("use", [])) - set(cards[i["part"]]["pins"])
        if unknown:
            raise AllocationError(f"{i['id']}: '{i['part']}' has no pin(s) {', '.join(sorted(unknown))}.")

    libraries = sorted({lib for i in intents for lib in cards[i["part"]]["libraries"]})
    uses_i2c = any(
        s.get("role", "").startswith("i2c")
        for i in intents for s in active_pins(cards[i["part"]], i.get("options", {})).values()
    )
    pools = _pools(board, libraries, uses_i2c)
    sda, scl = i2c_pins(board)

    # Collect every signal pin that needs a board pin, then assign by role order.
    wanted = []  # (role, intent_index, part_pin)
    for n, i in enumerate(intents):
        opts = i.get("options", {})
        for part_pin, spec in active_pins(cards[i["part"]], opts).items():
            if "role" in spec:
                wanted.append((role_of(spec, opts), n, part_pin))
    wanted.sort(key=lambda w: (ROLE_ORDER.index(w[0]), w[1]))

    taken = set()
    assigned = {}  # (intent_index, part_pin) -> board pin
    for role, n, part_pin in wanted:
        if role in ("i2c_sda", "i2c_scl"):
            assigned[(n, part_pin)] = sda if role == "i2c_sda" else scl
            continue
        free = [p for p in pools[role] if p not in taken]
        if not free:
            need = sum(1 for w in wanted if w[0] == role)
            note = ""
            if uses_i2c and role == "analog_in" and "analog" in board["pins"][sda]:
                note = f" ({sda} and {scl} are taken by the I2C bus)"
            raise AllocationError(
                f"Not enough pins for role '{role}' on the {board['name']}: this design needs {need}, "
                f"but only {len(pools[role])} are available{note}."
            )
        taken.add(free[0])
        assigned[(n, part_pin)] = free[0]

    # Build parts, extra components and nets.
    parts, extras, nets = [], [], {}
    rail_nodes = {"5V": ["board.5V"], "GND": ["board.GND"]}
    r_count = 0

    def add_net(name, *nodes):
        lst = nets.setdefault(name, [])
        lst.extend(n for n in nodes if n not in lst)

    for n, i in enumerate(intents):
        card, opts = cards[i["part"]], i.get("options", {})
        series = {e["on"]: e for e in card.get("extra", {}).get("series", [])}
        pulldown = {e["on"]: e for e in card.get("extra", {}).get("pulldown", [])}
        pins = {}
        for part_pin, spec in active_pins(card, opts).items():
            node = f"{i['id']}.{part_pin}"
            if "rail" in spec:
                rail_nodes[spec["rail"]].append(node)
                continue
            board_pin = assigned[(n, part_pin)]
            pins[part_pin] = board_pin
            if part_pin in series:
                r_count += 1
                rid = f"r{r_count}"
                extras.append({"id": rid, "part": "resistor", "ohms": series_ohms(card, series[part_pin], opts),
                               "for": f"{i['id']}.{part_pin}", "purpose": "series"})
                add_net(board_pin, f"board.{board_pin}", f"{rid}.1")
                add_net(f"{i['id']}_{part_pin}", f"{rid}.2", node)
            elif part_pin in pulldown:
                r_count += 1
                rid = f"r{r_count}"
                extras.append({"id": rid, "part": "resistor", "ohms": pulldown[part_pin]["ohms"],
                               "for": f"{i['id']}.{part_pin}", "purpose": "pulldown"})
                add_net(board_pin, f"board.{board_pin}", node, f"{rid}.1")
                rail_nodes["GND"].append(f"{rid}.2")
            else:
                add_net(board_pin, f"board.{board_pin}", node)
        parts.append({"id": i["id"], "part": i["part"], "options": opts, "pins": pins})

    net_list = [{"name": k, "nodes": v} for k, v in nets.items()]
    net_list += [{"name": k, "nodes": v} for k, v in rail_nodes.items() if len(v) > 1]
    pin_map = {f"{p['id']}.{pp}": bp for p in parts for pp, bp in p["pins"].items()}

    return {
        "board": board_key,
        "parts": parts,
        "extras": extras,
        "nets": net_list,
        "pin_map": pin_map,
        "libraries": libraries,
    }
