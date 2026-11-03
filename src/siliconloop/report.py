"""The instructions report: generated deterministically from the wiring, the part cards, the plan and the safety
result, never written by a model, so it cannot disagree with the twin or the firmware.

report.data(build, cards)     -> structured report (the website renders it)
report.markdown(build, cards) -> the same report as Markdown (download, print, and the chat assistant's context)
"""
from .allocator import BOARDS, active_pins
from .safety import externally_powered, supplies

POWER_COLOURS = {"5V": "red", "GND": "black"}
SIGNAL_COLOURS = ["yellow", "green", "blue", "white", "purple", "grey", "brown", "pink"]

# Most likely real-world mistake per part, for the "compare with your twin" checklist.
MISTAKES = {
    "led": "the LED is in backwards (the long leg must go to the resistor side) or its resistor is missing",
    "rgb_led": "the long leg (common cathode) is not on GND, or the red/green/blue legs are swapped",
    "push_button": "both button wires are on the same side of the button, so it is always (or never) pressed",
    "potentiometer": "the middle leg is not the one going to the analog pin",
    "joystick": "VRx and VRy are swapped, or the module has no 5V",
    "ldr": "the 10 kOhm resistor from the analog pin to GND is missing",
    "active_buzzer": "the buzzer's + leg is not on the pin side",
    "sg90_servo": "the orange signal wire is on the wrong pin, or the red wire is not on 5V",
    "hc_sr04": "Trig and Echo are swapped",
    "pir": "the sensor is still warming up (wait up to a minute after power-up) or its sensitivity knob is at minimum",
    "ir_obstacle": "the sensitivity knob needs adjusting, or OUT is on the wrong pin",
    "tilt_switch": "the switch is mounted so the ball cannot move",
    "touch_sensor": "VCC and GND are swapped",
    "sound_sensor": "AO (not DO) must go to the analog pin",
    "soil_moisture": "AO (not DO) must go to the analog pin",
    "flame_sensor": "DO is on the wrong pin or the sensitivity knob needs adjusting",
    "relay_module": "IN is on the wrong pin, or VCC/GND are swapped",
    "dht11": "the data pin is on the wrong Arduino pin, or VCC and GND are swapped",
    "dht22": "the data pin is on the wrong Arduino pin, or VCC and GND are swapped",
    "mpu6050": "SDA and SCL are swapped",
    "ssd1306": "SDA and SCL are swapped, or the display uses address 0x3D instead of 0x3C",
    "lcd1602_i2c": "SDA and SCL are swapped, or the contrast screw on the backpack needs turning",
}


def ohms(v):
    if v >= 1_000_000:
        return f"{v / 1_000_000:g} MOhm"
    if v >= 1000:
        return f"{v / 1000:g} kOhm"
    return f"{v:g} Ohm"


def _board_pin(pin):
    return f"pin {pin[1:]}" if pin.startswith("D") else f"pin {pin}"


def data(build, cards):
    board = BOARDS[build["board"]]
    plan, wiring = build.get("plan") or {}, build.get("wiring") or {}
    safety = build.get("safety") or {}
    intents = {i["id"]: i for i in plan.get("intents", [])}
    extras = wiring.get("extras", [])
    series = {e["for"]: e for e in extras if e.get("purpose") == "series"}
    pulldown = {e["for"]: e for e in extras if e.get("purpose") == "pulldown"}
    ext_of = externally_powered(wiring.get("power"))
    sups = supplies(wiring.get("power"))
    colour_of, colours = {}, iter(SIGNAL_COLOURS * 6)

    def colour(net):
        if net in POWER_COLOURS:
            return POWER_COLOURS[net]
        if net.startswith("EXT_"):
            return "orange"
        if net not in colour_of:
            colour_of[net] = next(colours)
        return colour_of[net]

    # Parts list
    shopping = [{"item": board["name"], "count": 1, "note": "with its USB cable"}]
    for p in wiring.get("parts", []):
        card, opts = cards[p["part"]], p.get("options") or {}
        note = ", ".join(f"{k}: {v}" for k, v in opts.items() if k != "use" and v not in (False, None))
        shopping.append({"item": card["name"], "count": 1, "note": note, "id": p["id"]})
    res = {}
    for e in extras:
        if e["part"] == "resistor":
            res[e["ohms"]] = res.get(e["ohms"], 0) + 1
    for v, n in sorted(res.items()):
        shopping.append({"item": f"{ohms(v)} resistor", "count": n, "note": "1/4 W"})
    for sp in sups:
        shopping.append({"item": f"Regulated {sp['volts']:g} V power supply ({sp['id']})", "count": 1,
                         "note": f"at least {sp['min_ma'] / 1000:g} A, for {', '.join(sp['powers'])}"})
    shopping.append({"item": "Breadboard and jumper wires", "count": 1, "note": "red, black and a few other colours"})

    # Wiring steps: power rails first, then each part, one connection per step
    steps = [{"text": f"Connect the Arduino's 5V pin to the breadboard's + rail.", "colour": "red", "from": "Arduino 5V", "to": "+ rail"},
             {"text": f"Connect an Arduino GND pin to the breadboard's - rail.", "colour": "black", "from": "Arduino GND", "to": "- rail"}]
    for sp in sups:
        steps.append({"text": f"Connect the {sp['volts']:g} V supply's ({sp['id']}) GND to the breadboard's - rail (common "
                              "ground). Keep the supply unplugged until everything else is wired.", "colour": "black",
                      "from": f"{sp['id']} GND", "to": "- rail"})
    table = []
    for p in wiring.get("parts", []):
        card, opts = cards[p["part"]], p.get("options") or {}
        for pin, spec in active_pins(card, opts).items():
            label, node = spec.get("label", pin), f"{p['id']}.{pin}"
            name = f"{card['name']} ({p['id']})"
            if "rail" in spec:
                rail = spec["rail"]
                if rail == "5V" and p["id"] in ext_of:
                    sp = ext_of[p["id"]]
                    steps.append({"text": f"{name}: connect {label} to the + wire of the {sp['volts']:g} V supply ({sp['id']}), "
                                          "NOT the Arduino 5V pin.", "colour": "orange", "from": f"{p['id']} {label}",
                                  "to": f"{sp['id']} +{sp['volts']:g} V"})
                    table.append({"part": p["id"], "pin": label, "to": f"{sp['id']} +{sp['volts']:g} V", "via": ""})
                else:
                    where = "+ rail (5V)" if rail == "5V" else "- rail (GND)" if rail == "GND" else rail
                    steps.append({"text": f"{name}: connect {label} to the {where}.", "colour": colour(rail),
                                  "from": f"{p['id']} {label}", "to": where})
                    table.append({"part": p["id"], "pin": label, "to": "5V" if rail == "5V" else rail, "via": ""})
                continue
            bp = p["pins"].get(pin)
            if not bp:
                continue
            c = colour(bp)
            if node in series:
                r = series[node]
                steps.append({"text": f"Put a {ohms(r['ohms'])} resistor ({r['id']}) on the breadboard. Connect Arduino "
                                      f"{_board_pin(bp)} to one end of it.", "colour": c, "from": f"Arduino {bp}", "to": f"{r['id']}"})
                steps.append({"text": f"{name}: connect {label} to the other end of {r['id']}.", "colour": c,
                              "from": f"{p['id']} {label}", "to": r["id"]})
                table.append({"part": p["id"], "pin": label, "to": bp, "via": f"{ohms(r['ohms'])} resistor ({r['id']})"})
            else:
                steps.append({"text": f"{name}: connect {label} to Arduino {_board_pin(bp)}.", "colour": c,
                              "from": f"{p['id']} {label}", "to": f"Arduino {bp}"})
                table.append({"part": p["id"], "pin": label, "to": bp, "via": ""})
                if node in pulldown:
                    r = pulldown[node]
                    steps.append({"text": f"Connect a {ohms(r['ohms'])} resistor ({r['id']}) from {label} of {p['id']} "
                                          f"to the - rail (GND).", "colour": "black", "from": r["id"], "to": "- rail"})
                    table[-1]["via"] = f"plus {ohms(r['ohms'])} to GND ({r['id']})"
    if sups:
        steps.append({"text": f"Check every wire against the pin table, then plug in the external suppl{'y' if len(sups) == 1 else 'ies'}.",
                      "colour": "orange", "from": "", "to": ""})

    # Safety
    notes = list(safety.get("warnings", []))
    if not notes:
        notes = ["Everything runs from the Arduino's USB power. Unplug the USB cable before changing any wire."]

    # Flashing
    pkgs = sorted({pk for p in wiring.get("parts", []) for pk in (cards[p["part"]].get("library_packages") or {}).values()})
    old = build["board"] == "nano_old"
    flashing = [
        "Plug the Arduino into your computer with the USB cable.",
        "In SiliconLoop, press Flash to board (Chrome or Edge on a computer), choose the Arduino's port and wait for the "
        "green tick. The board restarts with the new firmware.",
        "No Web Serial? Download sketch.ino and open it in the Arduino IDE"
        + (f", install the librar{'y' if len(pkgs) == 1 else 'ies'} {', '.join(pkgs)} from the Library Manager" if pkgs else "")
        + f", choose the board '{board['name']}'" + (" (Processor: ATmega328P (Old Bootloader))" if old else "")
        + " and the port, then press Upload.",
        "A Nano clone that will not upload usually has the old bootloader: choose 'Arduino Nano (old bootloader)'. "
        "Clones with a CH340 USB chip may need the CH340 driver on Windows or macOS." if build["board"].startswith("nano")
        else "If the port does not appear, try another USB cable: some cables only carry power.",
        "Open a serial monitor at 115200 baud to see the status messages and the telemetry lines (T,...).",
    ]

    # Compare with your twin
    checklist = []
    sim_rules = {r["id"]: r for r in (build.get("simulation") or {}).get("rules", [])}
    for r in plan.get("rules", []):
        ids = {iid for st in r.get("steps", []) for iid in list((st.get("set") or {}).keys()) + list((st.get("expect") or {}).keys())
               if iid in intents}
        likely = [MISTAKES[intents[i]["part"]] for i in sorted(ids) if intents[i]["part"] in MISTAKES]
        checklist.append({"id": r.get("id"), "expect": r.get("description", ""),
                          "passed_in_sim": sim_rules.get(r.get("id"), {}).get("ok"),
                          "if_not": "; or ".join(dict.fromkeys(likely)) or "a loose wire: press every jumper firmly into the breadboard"})

    sim = build.get("simulation") or {}
    pw = wiring.get("power") or {}
    power = {"source": pw.get("source", "usb"), "parts_ma": pw.get("parts_ma"), "limit_ma": pw.get("parts_limit_ma"),
             "supplies": [{k: sp.get(k) for k in ("id", "volts", "min_ma", "powers", "reason")} for sp in sups]}
    return {
        "title": plan.get("title", "Your gadget"), "summary": plan.get("summary", ""), "wish": build.get("wish", ""),
        "board": board["name"], "status": build.get("status"),
        "verified": f"{sim.get('passed', 0)} of {sim.get('total', 0)} behaviour rules passed in simulation" if sim else "",
        "memory": build.get("memory") or {},
        "power": power, "parts": shopping, "steps": steps, "pins": table, "safety": notes, "flashing": flashing,
        "checklist": checklist, "assumptions": plan.get("assumptions", []), "libraries": wiring.get("libraries", []),
        "net_colours": {**colour_of, **POWER_COLOURS},  # board pin net -> wire colour, shared with the circuit diagram
    }


def markdown(build, cards):
    d = data(build, cards)
    L = [f"# {d['title']}", "", d["summary"], "", f"- Board: {d['board']}", f"- Your wish: {d['wish']}"]
    if d["verified"]:
        L.append(f"- Proof: {d['verified']}")
    m = d["memory"]
    if m.get("flash"):
        L.append(f"- Size: {m['flash']} of {m['flash_max']} bytes of flash, {m['ram']} of {m['ram_max']} bytes of RAM")
    pw = d["power"]
    if pw["supplies"]:
        L.append("- Power: " + "; ".join(f"{sp['volts']:g} V external supply ({sp['id']}, at least {sp['min_ma'] / 1000:g} A) "
                                         f"for {', '.join(sp['powers'])}" for sp in pw["supplies"])
                 + ("; the rest from the Arduino's 5 V pin" if pw.get("parts_ma") else ""))
    elif pw.get("parts_ma") is not None:
        L.append(f"- Power: from the Arduino's 5 V pin (parts draw about {pw['parts_ma']} mA of the {pw['limit_ma']} mA limit)")
    L += ["", "## Parts list", ""]
    for x in d["parts"]:
        L.append(f"- {x['count']} x {x['item']}" + (f" ({x['note']})" if x.get("note") else ""))
    L += ["", "## Wiring steps", "", "Unplug the USB cable while you wire. Do one connection per step.", ""]
    for n, s in enumerate(d["steps"], 1):
        L.append(f"{n}. {s['text']}" + (f" Wire colour: {s['colour']}." if s.get("colour") else ""))
    L += ["", "## Pin table", "", "| Part | Part pin | Connects to | Through |", "|---|---|---|---|"]
    for r in d["pins"]:
        L.append(f"| {r['part']} | {r['pin']} | {r['to']} | {r['via'] or '-'} |")
    L += ["", "## Safety notes", ""] + [f"- {x}" for x in d["safety"]]
    L += ["", "## Flashing", ""] + [f"{n}. {x}" for n, x in enumerate(d["flashing"], 1)]
    L += ["", "## Compare with your twin", "",
          "Run the twin in SiliconLoop next to your real gadget. For each behaviour below, the real gadget should do "
          "exactly what the twin does.", ""]
    for c in d["checklist"]:
        L.append(f"- [ ] {c['id']}: {c['expect']}. If not: {c['if_not']}.")
    if d["assumptions"]:
        L += ["", "## Design assumptions", ""] + [f"- {x}" for x in d["assumptions"]]
    L += ["", "---", "Generated by SiliconLoop, built with Muse Spark Open. SiliconLoop is an AI-based product and can make "
          "mistakes: check your wiring against this report before you power the circuit."]
    return "\n".join(L) + "\n"
