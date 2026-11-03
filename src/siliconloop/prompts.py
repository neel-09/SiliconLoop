"""All model prompts. The rule vocabulary (what each part lets a rule set or check) comes from the part cards via simspec.

Prompts are plain functions returning chat messages, so every caller sends exactly the same instructions.
"""
import json

from . import simspec

PLAN_EXAMPLE = {
    "title": "Doorway Night Light",
    "summary": "The LED turns on when it gets dark; the servo waves when someone comes close; the OLED shows the light level.",
    "intents": [
        {"id": "ldr1", "part": "ldr", "options": {}},
        {"id": "led1", "part": "led", "options": {"color": "red", "dimmable": False}},
        {"id": "sonar1", "part": "hc_sr04", "options": {}},
        {"id": "servo1", "part": "sg90_servo", "options": {}},
        {"id": "oled1", "part": "ssd1306", "options": {}},
    ],
    "rules": [
        {"id": "R1", "description": "LED is on when it is dark",
         "steps": [{"set": {"ldr1": {"adc": 280}}, "expect": {"led1": {"on": True}}, "within_ms": 1000}]},
        {"id": "R2", "description": "LED turns off again when it gets bright",
         "steps": [{"set": {"ldr1": {"adc": 280}}, "expect": {"led1": {"on": True}}, "within_ms": 1000},
                   {"set": {"ldr1": {"adc": 335}}, "expect": {"led1": {"on": False},
                    "board": {"telemetry": {"ldr1.adc": {"min": 325, "max": 345}}}}, "within_ms": 1000}]},
        {"id": "R3", "description": "Servo waves to 90 degrees when someone is within 30 cm, and returns when they leave",
         "steps": [{"set": {"sonar1": {"distance_cm": 26}}, "expect": {"servo1": {"angle": 90}}, "within_ms": 2000},
                   {"set": {"sonar1": {"distance_cm": 37}}, "expect": {"servo1": {"angle": 0}}, "within_ms": 3000}]},
        {"id": "R4", "description": "OLED shows the word Light",
         "steps": [{"set": {}, "expect": {"oled1": {"text_contains": "Light"}}, "within_ms": 2000}]},
    ],
    "thresholds": [
        {"input": "ldr1.adc", "value": 300, "active": "below"},
        {"input": "sonar1.distance_cm", "value": 30, "active": "below"},
    ],
    "assumptions": ["Dark means an ADC reading below 300 (with the 10k divider, readings rise with brightness).",
                    "Someone is close when the distance is 30 cm or less."],
}


def _sources_block(sources):
    if not sources:
        return "No web sources were available. Rely only on the part cards."
    return "\n".join(f"[{s['n']}] {s['title']} ({s['url']})\n{s['content']}" for s in sources)


def _card_summary(key, card):
    pins = []
    for p, s in card["pins"].items():
        what = s.get("rail") or s.get("role")
        pins.append(f"{p} ({s.get('label', p)}): {what}{' [optional]' if s.get('optional') else ''}")
    io = simspec.io_of(card)
    return {
        "part": key, "name": card["name"], "pins": pins, "options": card.get("options", {}),
        "notes": card.get("notes", []), "rule_inputs": io["in"], "rule_outputs": io["out"],
        **({"rule_keys_needing_option": io["conditional"]} if io["conditional"] else {}),
        "source": "web (unverified)" if card.get("unverified") else "curated",
    }


def recognise(photos, cards):
    """Spark Open: components in one or more photos -> structured list."""
    catalogue = [{"part": k, "name": c["name"], "aliases": c.get("aliases", [])} for k, c in cards.items()]
    sys = (
        "You identify electronic components in photos for an Arduino project builder.\n"
        "Return ONLY a JSON object: {\"parts\": [{\"name\": str, \"part\": str|null, \"count\": int, \"confidence\": number}],"
        " \"ignored\": [str], \"image_quality\": \"ok\"|\"unclear\"}.\n"
        "- name: what the component is, in plain words (include any model number printed on it).\n"
        "- part: the matching key from the catalogue, or null if nothing in the catalogue matches.\n"
        "- count: how many identical units are visible. confidence: 0 to 1.\n"
        "- Put Arduino boards, breadboards, jumper wires, USB cables and loose resistors in 'ignored'; the system adds resistors itself.\n"
        "- Never guess a part you cannot see. If the photo is too blurry or dark to identify parts, set image_quality to 'unclear'.\n"
        f"Catalogue: {json.dumps(catalogue)}"
    )
    content = [{"type": "text", "text": "Identify every component in these photos."}, *photos]
    return [{"role": "system", "content": sys}, {"role": "user", "content": content}]


def plan(board, parts_cards, wish, fact_sheet, names=None):
    """Spark Open: wish + confirmed parts + grounding -> intents and machine-checkable behaviour rules."""
    sys = (
        "You design Arduino gadgets. You never choose pins or resistor values; a deterministic allocator does that.\n"
        "Return ONLY a JSON object with keys: title, summary, intents, rules, thresholds, assumptions.\n"
        "- intents: one entry per physical part used: {id, part, options}. id is short lowercase like led1. "
        "Use only parts from the available list, never more units than available. options may include color, "
        "any option listed on the part card (e.g. dimmable: true only if the LED must fade; a rule key listed in "
        "rule_keys_needing_option exists only when that option is true), and use (a list of optional pins to wire; "
        "rule keys that need an optional pin exist only when it is in use).\n"
        "- rules: 2 to 10 checkable behaviour rules that together define the gadget. Each rule: "
        "{id, description, steps: [{set, expect, within_ms, hold_ms?}]}. 'set' gives simulated inputs, "
        "'expect' gives outputs that must be true within within_ms (100-10000) after the inputs are set; "
        "hold_ms (optional) means they must then stay true that long. Inputs persist across steps of a rule; every "
        "rule starts from a fresh power-up with default inputs.\n"
        "- In set/expect use ONLY the rule_inputs and rule_outputs listed for each part, keyed by intent id. "
        "Use the key 'board' with serial_contains to check serial output. adc values are 0-1023; angles 0-180; "
        "fractions 0-1. A number for angle means 'within 10 degrees'; you may instead give {\"min\": a, \"max\": b}.\n"
        "- Every input part must be set in at least one rule, and every output part checked in at least one rule.\n"
        "- Every output must be checked in at least two different states (e.g. on and off, 90 and 0 degrees), "
        "every input must be set to at least two different values, and every serial_contains text must also appear "
        "in a serial_excludes check.\n"
        "- Every output must change state inside at least one rule: one step brings it to a state (e.g. LED on), a later "
        "step changes the inputs and checks the opposite state (LED off). A state that is already true at power-up "
        "proves nothing on its own.\n"
        "- For every threshold, include rules on BOTH sides of it (the output is active above it AND inactive below it), "
        "so firmware that is always on cannot pass. Use serial_excludes for the 'not printed' side.\n"
        "- thresholds: one entry per numeric rule input (adc, distance, angle, tilt, temperature, humidity): "
        "{\"input\": \"<id>.<key>\", \"value\": number, \"active\": \"below\"|\"above\"} (the output reacts when the input is "
        "below/above value), or {\"input\": \"<id>.<key>\", \"proportional\": true} when the input has no threshold "
        "(e.g. a knob that sets brightness). For every threshold, at least one step must set the input on the active side "
        "within one band of it and one step on the other side between one and two bands past it. Band: 25 ADC counts, "
        "5 cm, 5 degrees, 2 C, 5 %. E.g. value 50 cm, active below: test 45-49 cm and 55-60 cm. The firmware adds a "
        "hysteresis band on the inactive side, which is why the inactive test must be at least one band past.\n"
        "- Choose part options from what the user calls each confirmed part (user_names), e.g. 'red LED' means color red.\n"
        "- Serial is fixed at 115200 baud. The firmware prints a telemetry line every 250 ms: T,<id>.<key>=<value>,... "
        "for every rule input and output (e.g. T,imu1.pitch_deg=10.5,led1.on=1). To check a number, use "
        "board.telemetry ({\"imu1.pitch_deg\": {\"min\": 8, \"max\": 12}}), never serial_contains with digits. "
        "serial_contains and serial_excludes look only at output printed after the step's inputs were set. "
        "Do not make assumptions about baud rate or print format.\n"
        "- If the wish is ambiguous (e.g. 'tilted' could mean either axis), choose what a beginner would expect and "
        "write rules that pin it down. Never leave a behaviour open to more than one implementation.\n"
        "- Choose realistic thresholds and timings that agree with the verified datasheet facts (sensor ranges, warm-up "
        "times, read intervals). State each threshold in assumptions.\n"
        f"Example of the exact format:\n{json.dumps(PLAN_EXAMPLE)}"
    )
    user = (
        f"Board: {board['name']}.\n"
        f"Wish: {wish}\n"
        f"Available parts (part cards):\n{json.dumps([{**_card_summary(k, c), 'user_names': (names or {}).get(k, [])} for k, c in parts_cards.items()], indent=1)}\n"
        f"Verified datasheet facts:\n{_facts_block(fact_sheet, set(parts_cards))}"
    )
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


def ideas(board, parts_cards, counts):
    """Spark Open: three gadget ideas the confirmed parts can build, phrased so they go straight into plan()."""
    sys = (
        "You suggest Arduino gadgets a beginner can build from exactly the confirmed parts listed, plus the board itself.\n"
        "SiliconLoop will design each idea, write the firmware and prove it in a simulator, so every idea must stay "
        "inside what the simulator can test:\n"
        "- The behaviour may depend ONLY on the parts' rule_inputs (what a sensor or control measures) and may show itself "
        "ONLY through the parts' rule_outputs (LED on/brightness, buzzer on, servo angle, relay on, text on a display) "
        "or a short status message on the serial monitor.\n"
        "- Never use anything not in the list: no Wi-Fi, Bluetooth, internet, phone apps, cloud, SD cards, real-time "
        "clocks or time of day, cameras, speakers playing tunes, motors or displays that are not listed. Never mains power.\n"
        "- Use at most the listed count of each part. A part may be left out if the idea does not need it.\n"
        "- Give every threshold a concrete number in the units the sensor measures (e.g. 'closer than 30 cm', "
        "'tilted more than 20 degrees', 'when the light reading drops below 300', 'above 28 C').\n"
        "- Make the three ideas clearly different: one simple build that uses one or two parts, one that uses most of "
        "the parts, and one playful or useful everyday gadget.\n"
        "- The wish is ONE plain-English sentence (at most 200 characters) saying what the gadget does, written as the "
        "user's own request, e.g. 'A night light that turns the LED on when the light reading drops below 300 and "
        "waves the servo to 90 degrees when someone is closer than 30 cm.'\n"
        "- title: 2 to 5 words. why: one short line on what makes it fun or useful.\n"
        "Return ONLY JSON: {\"ideas\": [{\"title\": str, \"wish\": str, \"why\": str, \"parts_used\": [part keys, "
        "repeated once per unit used]}]} with exactly 3 ideas."
    )
    listed = [{**_card_summary(k, c), "count": counts.get(k, 1)} for k, c in parts_cards.items()]
    for x in listed:  # the pin list is noise for ideation; keep the behaviour vocabulary
        x.pop("pins", None)
    user = f"Board: {board['name']}.\nConfirmed parts:\n{json.dumps(listed, indent=1)}"
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


def chat(board, build, report_md):
    """Spark Open: answer a beginner's questions about one finished build, using only that build's facts."""
    sim = build.get("simulation") or {}
    sys = (
        "You are SiliconLoop's build assistant. A beginner has just received a gadget design: firmware that was "
        "proven in an Arduino simulator, a wiring report and a circuit diagram. Answer their questions about THIS build.\n"
        "- Use only the facts below. Pins, resistor values and wiring come from the report's pin table; never invent "
        "or change a pin, a resistor value or a part. If they ask to change the behaviour, explain what would change "
        "and tell them to start a new build with the new wish, because every design must pass the simulation test again.\n"
        "- Keep answers short and practical: two to six sentences, or a short list for steps. Plain words, no jargon "
        "without a one-line explanation. Markdown is fine; code only when they ask about the code, quoting the sketch.\n"
        "- For a misbehaving real gadget, point to the most likely wiring mistake from the report's checklist.\n"
        "- Never help with mains electricity, batteries above 12 V, or bypassing the safety notes.\n"
        "- If the question is not about this build or basic Arduino use, say briefly that you can only help with this build.\n\n"
        f"Board: {board['name']}\n"
        f"Gadget: {build['plan'].get('title')}: {build['plan'].get('summary')}\n"
        f"Status: {build.get('status')}; simulation {sim.get('passed', 0)}/{sim.get('total', 0)} rules passed.\n"
        f"Behaviour rules: {json.dumps([{'id': r.get('id'), 'description': r.get('description')} for r in build['plan']['rules']])}\n"
        f"Assumptions: {json.dumps(build['plan'].get('assumptions', []))}\n\n"
        f"Build report:\n{report_md}\n\n"
        f"Firmware sketch:\n```cpp\n{(build.get('sketch') or '(not written yet)')[:9000]}\n```"
    )
    return [{"role": "system", "content": sys}]


def pin_block(wiring):
    """The pin constants prepended to every sketch. Generated here, never written by a model."""
    lines = ["// ---- SiliconLoop pin map (generated, do not edit) ----", "#include <Arduino.h>"]
    for node, pin in wiring["pin_map"].items():
        name = "PIN_" + node.replace(".", "_").upper()
        value = pin[1:] if pin.startswith("D") else pin
        lines.append(f"const uint8_t {name} = {value};")
    lines.append("// ------------------------------------------------------")
    return "\n".join(lines) + "\n"


def telemetry_keys(plan_obj, cards):
    """<intent id>.<key> for every numeric/boolean rule input and output of every part, in intent order."""
    keys = []
    for i in plan_obj["intents"]:
        io = simspec.io_of(cards[i["part"]], i.get("options") or {})
        for k, t in {**io["in"], **io["out"]}.items():
            if t == "str":
                continue
            keys.append(f"{i['id']}.{k}")
    return keys


SKETCH_EXAMPLE = """// (pin constants PIN_BTN1_A, PIN_LED1_ANODE, PIN_LDR1_B and Arduino.h are already above this code)

const int DARK_BELOW = 300;                 // light reading; lower = darker
const unsigned long DEBOUNCE_MS = 25;
const unsigned long TELEMETRY_MS = 250;

bool ledOn = false;
bool btnStable = false, btnLastRaw = false;
unsigned long btnChangedAt = 0, lastTelemetry = 0;
char lastStatus[16] = "";

void status(const char* msg) {              // print a status line only when it changes
  if (strcmp(msg, lastStatus) != 0) { strncpy(lastStatus, msg, sizeof(lastStatus) - 1); Serial.println(msg); }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_BTN1_A, INPUT_PULLUP);
  pinMode(PIN_LED1_ANODE, OUTPUT);
}

void loop() {
  bool raw = digitalRead(PIN_BTN1_A) == LOW;           // pressed = LOW with the internal pull-up
  if (raw != btnLastRaw) { btnLastRaw = raw; btnChangedAt = millis(); }
  if (millis() - btnChangedAt >= DEBOUNCE_MS && raw != btnStable) {
    btnStable = raw;
    if (btnStable) ledOn = !ledOn;                     // toggle on each press
  }
  int light = analogRead(PIN_LDR1_B);
  digitalWrite(PIN_LED1_ANODE, ledOn ? HIGH : LOW);
  status(light < DARK_BELOW ? "DARK" : "BRIGHT");

  if (millis() - lastTelemetry >= TELEMETRY_MS) {
    lastTelemetry = millis();
    Serial.print(F("T,btn1.pressed=")); Serial.print(btnStable ? 1 : 0);
    Serial.print(F(",ldr1.adc=")); Serial.print(light);
    Serial.print(F(",led1.on=")); Serial.println(ledOn ? 1 : 0);
  }
}
"""


def firmware(board, wiring, plan_obj, cards, fact_sheet):
    """Spark Open: the complete single-file sketch. Pins come from the generated constants; libraries from the allowlist."""
    used = {i["part"]: cards[i["part"]] for i in plan_obj["intents"]}
    part_notes = {k: {"name": c["name"], "input_mode": c.get("input_mode"), "active_level": c.get("active_level"),
                      "i2c_address": c.get("i2c_address"), "libraries": c.get("libraries", []),
                      "notes": c.get("notes", [])} for k, c in used.items()}
    keys = telemetry_keys(plan_obj, cards)
    sys = (
        "You are an expert Arduino engineer. Write the complete firmware (one .ino file) for a gadget that must pass an "
        "automated behaviour test in a simulator, and that a beginner will read.\n"
        "Reply with ONE ```cpp code block and nothing else.\n"
        "Hard rules:\n"
        "- The pin constants listed below are already declared above your code. Use them by name for every pin. "
        "Never redeclare them and never write raw pin numbers.\n"
        "- Arduino.h is already included above your code; do not include it again. "
        + (f"#include only these headers, all of them needed: {', '.join(wiring['libraries'])}.\n" if wiring["libraries"]
           else "This design needs no libraries: write no #include lines.\n") +
        "- loop() must never block for more than 20 ms: no delay() in loop(), use millis() timers. "
        "pulseIn() always needs a timeout of 30000 microseconds or less. Read slow sensors (e.g. DHT) at most every 2 s.\n"
        "- Serial.begin(115200). Every 250 ms print exactly one telemetry line, starting with T, then these key=value "
        f"pairs in this order: {', '.join(keys) or '(none)'}. Numbers only, booleans as 1/0, "
        "e.g. T,ldr1.adc=412,led1.on=1. Telemetry must report the values your code actually read or set.\n"
        "- Any other message (e.g. TILTED) is printed on its own line ONLY when it changes, never on every loop() pass; "
        "when the condition ends, print the opposite status (e.g. LEVEL).\n"
        "- No String class (use char arrays); wrap string literals in F() inside Serial.print. Put every threshold and "
        "time in a named const at the top.\n"
        "- Follow each part's input_mode, active_level, i2c_address and notes exactly.\n"
        "- Implement every behaviour rule exactly, including thresholds on both sides and the timings.\n"
        "- Give every analog or distance threshold that switches an output a small hysteresis band (a named const, "
        "e.g. 20 ADC counts or 4 cm) so the output does not flicker when a reading hovers at the threshold. The stated "
        "threshold is exactly where the output switches ON; put the whole band on the OFF side (e.g. on at <= 50 cm, "
        "off above 54 cm). Never make it wider than 20 ADC counts, 4 cm, 3 degrees, 1 C or 3 %, so every rule's test values "
        "are still clearly on their side.\n"
        "- Accurate and to the point: only the code the rules need, no unused variables, features or debug prints; "
        "brief comments.\n"
        f"Example of the expected style (for a different gadget):\n```cpp\n{SKETCH_EXAMPLE}```"
    )
    user = (
        f"Board: {board['name']}.\n"
        f"Pin constants (already declared above your code):\n{pin_block(wiring)}\n"
        f"Gadget: {plan_obj['title']}: {plan_obj['summary']}\n"
        f"Behaviour rules (these are the test):\n{json.dumps(plan_obj['rules'], indent=1)}\n"
        f"Assumptions: {json.dumps(plan_obj.get('assumptions', []))}\n"
        f"Parts:\n{json.dumps(part_notes, indent=1)}\n"
        f"Verified datasheet and library facts (use these library calls exactly):\n{_facts_block(fact_sheet, set(used))}"
    )
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


def repair(stage, errors, sketch_numbered):
    """Spark Open: fix the sketch from lint / compile / memory / simulation feedback."""
    if stage == "simulation":
        intro = (
            "Your sketch compiled, but it failed the automated behaviour test. The compiled binary ran in an AVR simulator "
            "with models of the real parts, wired exactly as the pin constants say; each rule starts from a fresh power-up "
            "with default inputs. The test and the part models are fixed and correct, so change the firmware, not the "
            f"expectations. Failures:\n{errors}\n\n"
            "Observed values are what the simulated parts actually did (e.g. the LED pin level, the servo angle from its "
            "pulse width, the text on the display) or the last telemetry line. Keep every rule that already passes working.\n\n"
        )
    else:
        intro = f"Your sketch failed at the {stage} stage:\n{errors}\n\n"
    return {
        "role": "user",
        "content": (
            intro +
            f"The sketch as it was compiled (the pin constants at the top are generated), with line numbers:\n{sketch_numbered}\n\n"
            "Fix it, keep every hard rule, and reply with the complete corrected sketch (without the pin constants) in one ```cpp block."
        ),
    }


def facts(parts_cards, libraries, sources):
    """Spark Open: raw approved-site snippets -> a structured fact sheet. Every field carries a verbatim quote,
    which the caller checks against the sources before anything is used."""
    names = {k: c["name"] for k, c in parts_cards.items()}
    sys = (
        "You turn electronics web sources into a strict fact sheet. Use ONLY what the sources state about the exact part; "
        "ignore anything about other parts, boards or projects.\n"
        "Return ONLY JSON: {\"parts\": {<part key>: {"
        "\"pins\": [{\"label\": str, \"function\": \"power\"|\"ground\"|\"signal_in\"|\"signal_out\"|\"i2c_sda\"|\"i2c_scl\"|\"other\", \"quote\": str}], "
        "\"vcc_min\": number|null, \"vcc_max\": number|null, \"vcc_quote\": str|null, "
        "\"typical_ma\": number|null, \"typical_quote\": str|null, \"peak_ma\": number|null, \"peak_quote\": str|null, "
        "\"specs\": [{\"name\": str, \"value\": str, \"quote\": str}]}}, "
        "\"libraries\": {<header>: {\"calls\": [{\"code\": str, \"quote\": str}]}}}.\n"
        "- Every quote must be copied character for character from one source and must contain the label, number or code it supports.\n"
        "- pins: the module's pins as labelled. specs: measurable facts useful for behaviour (range, accuracy, "
        "response or warm-up time, read interval), at most 5 per part.\n"
        "- typical_ma: normal operating current; peak_ma: stall, start-up or maximum current.\n"
        "- libraries: up to 6 essential calls for each header (constructor, begin/initialise, read or write calls), "
        "exactly as written in the sources.\n"
        "- Use null or an empty list when the sources do not state something. Never estimate or fill in from memory."
    )
    user = f"Parts: {json.dumps(names)}\nLibraries: {json.dumps(libraries)}\nSources:\n{_sources_block(sources)}"
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


def _facts_block(fact_sheet, keys=None):
    """The verified fact sheet as compact JSON for prompts (no raw web text)."""
    if not fact_sheet or not (fact_sheet.get("parts") or fact_sheet.get("libraries")):
        return "No verified web facts were found; rely on the part cards."
    parts = {k: {f: v for f, v in x.items() if f in ("pins", "vcc_range", "rail_ma", "stall_ma", "specs")}
             for k, x in fact_sheet.get("parts", {}).items() if keys is None or k in keys}
    return json.dumps({"parts": parts, "libraries": {h: x["calls"] for h, x in fact_sheet.get("libraries", {}).items()}}, indent=1)


def extract_part(name, sources):
    """Spark Open: datasheet snippets -> structured facts for an unknown part (validated afterwards)."""
    sys = (
        "You extract electrical facts about an electronics module from web sources. Use ONLY facts stated in the sources.\n"
        "Return ONLY JSON: {\"name\": str, \"kind\": \"digital_sensor\"|\"analog_sensor\"|\"digital_and_analog_sensor\"|"
        "\"switched_module\"|\"protocol_device\"|\"other\", \"pins\": [{\"label\": str, \"function\": \"vcc\"|\"gnd\"|"
        "\"digital_out\"|\"analog_out\"|\"control_in\"|\"other\"}], \"vcc_min\": number, \"vcc_max\": number, "
        "\"current_ma\": number, \"active_level\": \"HIGH\"|\"LOW\"|null, \"notes\": [str], \"evidence\": [str]}.\n"
        "- digital_out / analog_out: the module sends a signal to the Arduino. control_in: the Arduino switches the module on/off.\n"
        "- protocol_device: it talks I2C, SPI, UART, one-wire or any data protocol.\n"
        "- pin labels exactly as printed on the module. evidence: short quotes from the sources supporting the pins and voltages.\n"
        "- If the sources do not state a value, use null rather than guessing."
    )
    return [{"role": "system", "content": sys},
            {"role": "user", "content": f"Module: {name}\nSources:\n{_sources_block(sources)}"}]
