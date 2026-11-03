"""Electrical rules check on wiring JSON -> "Safe to build" or refusal with reasons.

Deterministic. Runs on every design (and every tweak) before anything is shown to the user.
Result: {"safe": bool, "errors": [...], "warnings": [...], "budget": {...}}
"""
import re

from .allocator import BOARDS, PARTS, active_pins, conflict_pins, role_of, series_vf

MAINS = re.compile(
    r"\b(mains|230\s?v|220\s?v|240\s?v|110\s?v|120\s?v|ac power|wall (socket|outlet|plug)|household (power|supply))\b",
    re.I,
)
ROLE_CAPS = {
    "digital_out": "digital", "digital_in": "digital", "servo": "digital",
    "pwm_out": "pwm", "analog_in": "analog", "i2c_sda": "i2c_sda", "i2c_scl": "i2c_scl",
}


def check(wiring, wish="", extra_cards=None):
    cards = {**PARTS, **(extra_cards or {})}
    errors, warnings = [], []
    board = BOARDS[wiring["board"]]
    limits = board["limits"]
    vcc = board["supply_rails"]["5V"]

    if wish and MAINS.search(wish):
        errors.append(
            "This sounds like it involves mains electricity. SiliconLoop never designs anything that touches "
            "mains voltage, because a wiring mistake there can kill. Everything here runs from the Arduino's USB power."
        )

    known = [p for p in wiring["parts"] if p["part"] in cards]
    for p in wiring["parts"]:
        if p["part"] not in cards:
            errors.append(f"{p['id']}: '{p['part']}' is not a supported part.")

    # Pins: exist, not reserved, right capability, not shared (except the I2C bus).
    used = {}
    for p in known:
        card, opts = cards[p["part"]], p.get("options", {})
        for part_pin, bp in p["pins"].items():
            spec = card["pins"].get(part_pin)
            if not spec or "role" not in spec:
                errors.append(f"{p['id']}: '{part_pin}' is not a signal pin of {card['name']}.")
                continue
            role = role_of(spec, opts)
            if bp not in board["pins"]:
                errors.append(f"{p['id']}.{part_pin}: pin {bp} does not exist on the {board['name']}.")
                continue
            if bp in board["reserved"]:
                errors.append(f"{p['id']}.{part_pin}: pin {bp} is reserved ({board['reserved'][bp]}).")
            if ROLE_CAPS[role] not in board["pins"][bp]:
                errors.append(f"{p['id']}.{part_pin}: pin {bp} cannot do {role}.")
            if role.startswith("i2c"):
                continue
            if bp in used:
                errors.append(f"Pin {bp} is used by both {used[bp]} and {p['id']}.{part_pin}.")
            used[bp] = f"{p['id']}.{part_pin}"
        missing = [pp for pp, s in active_pins(card, opts).items() if "role" in s and pp not in p["pins"]]
        if missing:
            errors.append(f"{p['id']}: pin(s) {', '.join(missing)} are not connected.")

    # PWM pins knocked out by a library's timer use (e.g. Servo), from boards.json conflicts.
    dead = set(conflict_pins(board, wiring.get("libraries", [])))
    if dead:
        for p in known:
            card = cards[p["part"]]
            for part_pin, bp in p["pins"].items():
                spec = card["pins"].get(part_pin, {})
                if "role" in spec and role_of(spec, p.get("options", {})) == "pwm_out" and bp in dead:
                    errors.append(f"{p['id']}: PWM on {bp} does not work with the libraries this design uses ({', '.join(wiring['libraries'])}).")

    # Series resistors present, and GPIO current per pin and in total.
    resistors = {e["for"]: e["ohms"] for e in wiring.get("extras", []) if e.get("purpose") == "series"}
    gpio_total = 0.0
    for p in known:
        card, opts = cards[p["part"]], p.get("options", {})
        series = {e["on"]: e for e in card.get("extra", {}).get("series", [])}
        for part_pin in p["pins"]:
            if part_pin in series:
                key = f"{p['id']}.{part_pin}"
                if key not in resistors:
                    errors.append(f"{key}: missing its series resistor.")
                    ma = 1000.0
                elif "vf" in series[part_pin]:
                    ma = (vcc - series_vf(card, series[part_pin], opts)) / resistors[key] * 1000
                else:
                    ma = series[part_pin]["ma"]  # fixed resistor on a non-LED load: card states the current
                gpio_total += ma
                if ma > limits["gpio_ma_recommended"]:
                    errors.append(f"{key}: would draw {ma:.0f} mA from one pin; the safe limit is "
                                  f"{limits['gpio_ma_recommended']} mA.")
        ma = card.get("pin_ma", 0)
        gpio_total += ma
        if ma > limits["gpio_ma_recommended"]:
            errors.append(f"{p['id']}: would draw {ma} mA from a pin; the safe limit is {limits['gpio_ma_recommended']} mA. "
                          "It needs a transistor or driver module.")
    if gpio_total > limits["mcu_total_ma"]:
        errors.append(f"All pins together would draw {gpio_total:.0f} mA; the chip's limit is {limits['mcu_total_ma']} mA.")

    # 5 V rail budget from USB (parts moved to an external supply by plan_power() don't count).
    power = wiring.get("power", {"source": "usb"})
    ext_of = externally_powered(power)
    external = set(ext_of)
    rail = limits["board_self_ma"] + sum(cards[p["part"]].get("rail_ma", 0) for p in known if p["id"] not in external)
    if power.get("notice"):
        warnings.insert(0, power["notice"])
    # Card-level safety notes: 'on_usb' applies while the part is powered from USB, 'always' whenever it is used.
    for kind in ("on_usb", "always"):
        for part in dict.fromkeys(p["part"] for p in known if kind == "always" or p["id"] not in external):
            for note in cards[part].get("safety_notes", {}).get(kind, []):
                if note not in warnings:
                    warnings.append(note)
    usb_peak = power.get("usb_peak_ma")
    if usb_peak is not None and usb_peak > limits["usb_peak_ma"]:
        errors.append(f"The parts left on USB can draw about {usb_peak} mA in brief peaks; the board's USB fuse trips at "
                      f"about {limits['usb_peak_ma']} mA. Use fewer parts.")
    parts_on_usb = rail - limits["board_self_ma"]
    if parts_on_usb > USB_PARTS_MA:
        errors.append(f"The parts powered from the Arduino would draw about {parts_on_usb} mA; the limit is "
                      f"{USB_PARTS_MA} mA. Use fewer parts.")
    if rail > limits["usb_5v_ma"]:
        lead = "Even with a separate supply for the high-current parts, the rest" if external else "The parts"
        errors.append(f"{lead} need about {rail} mA from the Arduino's 5 V pin; USB can safely give "
                      f"{limits['usb_5v_ma']} mA. Use fewer parts.")

    # Supply voltage inside each part's range.
    for p in known:
        rng = cards[p["part"]].get("vcc_range")
        supply_v = ext_of[p["id"]]["volts"] if p["id"] in ext_of else vcc
        if rng and not (rng[0] <= supply_v <= rng[1]):
            errors.append(f"{p['id']}: needs {rng[0]}-{rng[1]} V, but its supply gives {supply_v:g} V.")
        if supply_v > BOARD_VOLTS:
            warnings.append(f"{p['id']} runs from {supply_v:g} V. Check its datasheet that its signal pins work at the "
                            f"Arduino's 5 V logic level before connecting them.")

    # I2C addresses must be unique on the bus.
    addrs = {}
    for p in known:
        a = cards[p["part"]].get("i2c_address")
        if a:
            if a in addrs:
                errors.append(f"{p['id']} and {addrs[a]} both use I2C address {a}.")
            addrs[a] = p["id"]

    return {
        "safe": not errors,
        "errors": errors,
        "warnings": warnings,
        "budget": {"gpio_ma": round(gpio_total, 1), "rail_5v_ma": rail, "usb_limit_ma": limits["usb_5v_ma"]},
        "power": power,
    }


USB_PARTS_MA = 300      # the parts may draw at most this much, together, from the Arduino's 5 V pin
BOARD_VOLTS = 5.0
STANDARD_VOLTS = [5.0, 6.0, 9.0, 12.0, 24.0]


def supplies(power):
    """External supplies of a wiring's power plan, as a list (also reads the older single-supply format)."""
    power = power or {}
    if isinstance(power.get("supplies"), list):
        return power["supplies"]
    ext = power.get("external")
    if isinstance(ext, dict) and ext.get("powers"):
        return [{"id": "psu1", "volts": ext.get("volts", 5.0), "min_ma": ext.get("min_ma"), "peak_ma": ext.get("peak_ma"),
                 "powers": ext["powers"], "reason": "current"}]
    return []


def externally_powered(power):
    """part id -> the supply that powers it."""
    return {pid: s for s in supplies(power) for pid in s["powers"]}


def needed_volts(card):
    """The supply voltage a part needs when it cannot run from 5 V, or None if 5 V is fine."""
    v = card.get("supply_v")
    rng = card.get("vcc_range")
    if v and v > BOARD_VOLTS:
        return float(v)
    if rng and rng[0] > BOARD_VOLTS:
        return next((x for x in STANDARD_VOLTS if rng[0] <= x <= rng[1]), float(rng[0]))
    return None


def plan_power(wiring, extra_cards=None):
    """Power from the Arduino's 5 V pin while every part runs on 5 V and the parts draw at most USB_PARTS_MA together.
    Otherwise use external supplies, only as far as needed:
      - a part that needs more than 5 V gets a supply at a voltage inside its range (one supply per voltage);
      - if the 5 V parts still draw more than USB_PARTS_MA (or their brief peaks exceed the USB fuse), the parts with the
        largest draw move to an external 5 V supply until the rest fits.
    Every external supply shares GND with the Arduino. The notice must be shown to the user."""
    import copy
    import math
    cards = {**PARTS, **(extra_cards or {})}
    w = copy.deepcopy(wiring)
    limits = BOARD_LIMITS(w["board"])
    known = [p for p in w["parts"] if p["part"] in cards]
    draw = {p["id"]: cards[p["part"]].get("rail_ma", 0) or 0 for p in known}
    peak_of = {p["id"]: max(cards[p["part"]].get("stall_ma") or 0, draw[p["id"]]) for p in known}

    groups = {}                       # volts -> [part ids], with the reason
    for p in known:
        v = needed_volts(cards[p["part"]])
        if v:
            groups.setdefault(v, []).append(p["id"])
    on_board = [p for p in known if not any(p["id"] in ids for ids in groups.values())]
    parts_ma = sum(draw[p["id"]] for p in on_board)
    peak_ma = limits["board_self_ma"] + sum(peak_of[p["id"]] for p in on_board)
    moved_5v = []
    for p in sorted(on_board, key=lambda p: (-draw[p["id"]], -peak_of[p["id"]])):
        if parts_ma <= USB_PARTS_MA and peak_ma <= limits["usb_peak_ma"]:
            break
        if not draw[p["id"]] or not any(s.get("rail") == "5V" for s in cards[p["part"]]["pins"].values()):
            continue
        moved_5v.append(p["id"])
        parts_ma -= draw[p["id"]]
        peak_ma -= peak_of[p["id"]]
    if moved_5v:
        groups.setdefault(BOARD_VOLTS, []).extend(moved_5v)

    usb = {"usb_load_ma": limits["board_self_ma"] + parts_ma, "usb_peak_ma": peak_ma, "parts_ma": parts_ma,
           "parts_limit_ma": USB_PARTS_MA}
    if not groups:
        w["power"] = {"source": "usb", **usb}
        return w

    sups, lines = [], []
    for n, (volts, ids) in enumerate(sorted(groups.items()), 1):
        sid = f"psu{n}"
        typ = sum(draw[i] for i in ids)
        peak = sum(peak_of[i] for i in ids)
        rating = max(1000, math.ceil(max(peak, typ) * 1.25 / 500) * 500)
        high_v = volts > BOARD_VOLTS
        net = f"EXT_{volts:g}V"
        moved = {f"{p['id']}.{pin}" for p in known if p["id"] in ids
                 for pin, spec in cards[p["part"]]["pins"].items() if spec.get("rail") == "5V"}
        for nt in w["nets"]:
            if nt["name"] == "5V":
                nt["nodes"] = [x for x in nt["nodes"] if x not in moved]
            if nt["name"] == "GND" and f"{sid}.GND" not in nt["nodes"]:
                nt["nodes"].append(f"{sid}.GND")
        w["nets"] = [nt for nt in w["nets"] if nt["name"] != "5V" or len(nt["nodes"]) > 1]
        w["nets"].append({"name": net, "nodes": [f"{sid}.+V"] + sorted(moved)})
        reason = "voltage" if high_v else "current"
        w["extras"].append({"id": sid, "part": "external_supply" if high_v else "external_5v_supply", "volts": volts,
                            "min_ma": rating, "for": ",".join(ids), "purpose": "power", "reason": reason})
        sups.append({"id": sid, "volts": volts, "min_ma": rating, "typical_ma": typ, "peak_ma": peak, "powers": ids,
                     "reason": reason, "net": net})
        names = ", ".join(ids)
        why = (f"{names} need{'s' if len(ids) == 1 else ''} {volts:g} V, more than the Arduino's 5 V pin provides"
               if high_v else
               f"together the parts would draw more than {USB_PARTS_MA} mA from the Arduino's 5 V pin, so {names} "
               f"(up to {peak} mA) run{'s' if len(ids) == 1 else ''} from a separate supply")
        lines.append(f"a regulated {volts:g} V supply rated at least {rating / 1000:g} A for {names}: {why}")
    w["power"] = {
        "source": "usb+external", **usb, "supplies": sups,
        "notice": ("IMPORTANT - this build needs " + "; and ".join(lines) + ". Connect each supply's + only to the power "
                   "wires of the parts it powers (never to the Arduino's 5V pin), and connect every supply's GND to an "
                   "Arduino GND pin so they share a common ground. Use a regulated supply of exactly that voltage."),
    }
    return w


def BOARD_LIMITS(board_key):
    return BOARDS[board_key]["limits"]
