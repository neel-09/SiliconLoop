"""Hand-written sketches whose behaviour is known, used to prove the simulator and the part models before trusting
them with generated firmware. Each case: (name, board, plan, app code without pin constants, should pass)."""
import copy

from .prompts import PLAN_EXAMPLE

NIGHT_LIGHT = r"""
#include <Wire.h>
#include <Servo.h>
#include <SSD1306Ascii.h>
#include <SSD1306AsciiWire.h>

const int DARK_BELOW = 300;            // light reading; lower = darker
const int DARK_HYST = 20;              // LED turns off again above 320
const int NEAR_CM = 30;                // wave when someone is this close
const int NEAR_HYST = 4;               // stop waving above 34 cm
const int WAVE_ANGLE = 90;
const int REST_ANGLE = 0;
const unsigned long SONAR_MS = 60;
const unsigned long TELEMETRY_MS = 250;
const unsigned long OLED_MS = 500;

Servo flag;
SSD1306AsciiWire oled;
bool ledOn = false, near = false;
int light = 0, angle = REST_ANGLE;
long distanceCm = 400;
unsigned long lastSonar = 0, lastTelemetry = 0, lastOled = 0;
int shownLight = -1;

long readDistanceCm() {
  digitalWrite(PIN_SONAR1_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_SONAR1_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_SONAR1_TRIG, LOW);
  unsigned long us = pulseIn(PIN_SONAR1_ECHO, HIGH, 25000UL);
  if (us == 0) return 400;             // nothing in range
  return us / 58;
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_LED1_ANODE, OUTPUT);
  pinMode(PIN_SONAR1_TRIG, OUTPUT);
  pinMode(PIN_SONAR1_ECHO, INPUT);
  flag.attach(PIN_SERVO1_SIGNAL);
  flag.write(REST_ANGLE);
  Wire.begin();
  oled.begin(&Adafruit128x64, 0x3C);
  oled.setFont(System5x7);
  oled.clear();
}

void loop() {
  unsigned long now = millis();
  light = analogRead(PIN_LDR1_B);
  if (!ledOn && light < DARK_BELOW) ledOn = true;
  else if (ledOn && light > DARK_BELOW + DARK_HYST) ledOn = false;
  digitalWrite(PIN_LED1_ANODE, ledOn ? HIGH : LOW);

  if (now - lastSonar >= SONAR_MS) {
    lastSonar = now;
    distanceCm = readDistanceCm();
    if (!near && distanceCm <= NEAR_CM) near = true;
    else if (near && distanceCm > NEAR_CM + NEAR_HYST) near = false;
    angle = near ? WAVE_ANGLE : REST_ANGLE;
    flag.write(angle);
  }

  if (now - lastOled >= OLED_MS && light != shownLight) {
    lastOled = now;
    shownLight = light;
    oled.setCursor(0, 0);
    oled.print(F("Light: "));
    oled.print(light);
    oled.clearToEOL();
  }

  if (now - lastTelemetry >= TELEMETRY_MS) {
    lastTelemetry = now;
    Serial.print(F("T,ldr1.adc=")); Serial.print(light);
    Serial.print(F(",led1.on=")); Serial.print(ledOn ? 1 : 0);
    Serial.print(F(",sonar1.distance_cm=")); Serial.print(distanceCm);
    Serial.print(F(",servo1.angle=")); Serial.println(angle);
  }
}
"""


def _broken(old, new):
    assert old in NIGHT_LIGHT, old
    return NIGHT_LIGHT.replace(old, new)


# Deliberate mistakes, each of which a real build could contain. Every one must be caught.
BROKEN = {
    "inverted LED logic": _broken("digitalWrite(PIN_LED1_ANODE, ledOn ? HIGH : LOW);", "digitalWrite(PIN_LED1_ANODE, ledOn ? LOW : HIGH);"),
    "servo never returns": _broken("angle = near ? WAVE_ANGLE : REST_ANGLE;", "if (near) angle = WAVE_ANGLE;"),
    "wrong OLED address": _broken("oled.begin(&Adafruit128x64, 0x3C);", "oled.begin(&Adafruit128x64, 0x3D);"),
    "blocking delay in loop": _broken("  if (now - lastTelemetry >= TELEMETRY_MS) {", "  delay(900);\n  if (now - lastTelemetry >= TELEMETRY_MS) {"),
    "wrong baud rate": _broken("Serial.begin(115200);", "Serial.begin(9600);"),
    "status flood": _broken("  if (now - lastTelemetry >= TELEMETRY_MS) {", "  Serial.println(ledOn ? F(\"DARK\") : F(\"BRIGHT\"));\n  if (now - lastTelemetry >= TELEMETRY_MS) {"),
    "telemetry reports a stale value": _broken("Serial.print(F(\"T,ldr1.adc=\")); Serial.print(light);", "Serial.print(F(\"T,ldr1.adc=\")); Serial.print(512);"),
}

CASES = [("night light", "uno", PLAN_EXAMPLE, NIGHT_LIGHT, True)]
CASES += [(f"night light: {k}", "uno", PLAN_EXAMPLE, v, False) for k, v in BROKEN.items()]
CASES += [("night light on Mega", "mega", PLAN_EXAMPLE, NIGHT_LIGHT, True)]


# ---------- one case per part model ----------

def _plan(title, intents, rules):
    return {"title": title, "summary": title, "intents": intents, "rules": rules, "assumptions": []}


def _step(set_, expect, within=1000, hold=None):
    s = {"set": set_, "expect": expect, "within_ms": within}
    if hold:
        s["hold_ms"] = hold
    return s


BUTTON_PLAN = _plan("Hold to buzz", [
    {"id": "btn1", "part": "push_button", "options": {}},
    {"id": "led1", "part": "led", "options": {"color": "red", "dimmable": False}},
    {"id": "bz1", "part": "active_buzzer", "options": {}},
], [
    {"id": "R1", "description": "Pressed: LED and buzzer on", "steps": [
        _step({"btn1": {"pressed": True}}, {"led1": {"on": True}, "bz1": {"on": True}}, 500)]},
    {"id": "R2", "description": "Released: both off and stay off", "steps": [
        _step({"btn1": {"pressed": True}}, {"led1": {"on": True}}, 500),
        _step({"btn1": {"pressed": False}}, {"led1": {"on": False}, "bz1": {"on": False}}, 500, hold=500)]},
])
BUTTON = r"""
const unsigned long DEBOUNCE_MS = 25, TELEMETRY_MS = 250;
bool stable = false, lastRaw = false; unsigned long changedAt = 0, lastTel = 0;
void setup() {
  Serial.begin(115200);
  pinMode(PIN_BTN1_A, INPUT_PULLUP);
  pinMode(PIN_LED1_ANODE, OUTPUT);
  pinMode(PIN_BZ1_POS, OUTPUT);
}
void loop() {
  bool raw = digitalRead(PIN_BTN1_A) == LOW;
  if (raw != lastRaw) { lastRaw = raw; changedAt = millis(); }
  if (millis() - changedAt >= DEBOUNCE_MS) stable = raw;
  digitalWrite(PIN_LED1_ANODE, stable); digitalWrite(PIN_BZ1_POS, stable);
  if (millis() - lastTel >= TELEMETRY_MS) { lastTel = millis();
    Serial.print(F("T,btn1.pressed=")); Serial.print(stable); Serial.print(F(",led1.on=")); Serial.print(stable);
    Serial.print(F(",bz1.on=")); Serial.println(stable); }
}
"""

POT_PLAN = _plan("Colour knob", [
    {"id": "pot1", "part": "potentiometer", "options": {}},
    {"id": "rgb1", "part": "rgb_led", "options": {}},
], [
    {"id": "R1", "description": "Knob at 0: blue", "steps": [_step({"pot1": {"adc": 0}}, {"rgb1": {"r": 0, "b": 1}})]},
    {"id": "R2", "description": "Knob at max: red", "steps": [_step({"pot1": {"adc": 1023}}, {"rgb1": {"r": 1, "b": 0}})]},
    {"id": "R3", "description": "Knob in the middle: half and half",
     "steps": [_step({"pot1": {"adc": 512}}, {"rgb1": {"r": {"min": 0.4, "max": 0.6}, "g": 0}})]},
])
POT = r"""
unsigned long lastTel = 0;
void setup() { Serial.begin(115200); pinMode(PIN_RGB1_R, OUTPUT); pinMode(PIN_RGB1_G, OUTPUT); pinMode(PIN_RGB1_B, OUTPUT); }
void loop() {
  int v = analogRead(PIN_POT1_WIPER);
  int r = map(v, 0, 1023, 0, 255);
  analogWrite(PIN_RGB1_R, r); analogWrite(PIN_RGB1_G, 0); analogWrite(PIN_RGB1_B, 255 - r);
  if (millis() - lastTel >= 250) { lastTel = millis();
    Serial.print(F("T,pot1.adc=")); Serial.print(v); Serial.print(F(",rgb1.r=")); Serial.print(r / 255.0, 2);
    Serial.print(F(",rgb1.g=0,rgb1.b=")); Serial.println((255 - r) / 255.0, 2); }
}
"""

GENERIC_PLAN = _plan("Generic modules", [
    {"id": "joy1", "part": "joystick", "options": {"use": ["sw"]}},
    {"id": "relay1", "part": "relay_module", "options": {}},
    {"id": "pir1", "part": "pir", "options": {}},
    {"id": "mic1", "part": "sound_sensor", "options": {}},
    {"id": "led1", "part": "led", "options": {"color": "green", "dimmable": False}},
], [
    {"id": "R1", "description": "Stick right switches the relay on", "steps": [
        _step({"joy1": {"x": 900}}, {"relay1": {"active": True}}),
        _step({"joy1": {"x": 512}}, {"relay1": {"active": False}})]},
    {"id": "R2", "description": "Motion lights the LED", "steps": [
        _step({"pir1": {"active": True}}, {"led1": {"on": True}}),
        _step({"pir1": {"active": False}}, {"led1": {"on": False}})]},
    {"id": "R3", "description": "Loud sound or stick press prints LOUD", "steps": [
        _step({"mic1": {"adc": 700}}, {"board": {"serial_contains": "LOUD", "telemetry": {"mic1.adc": {"min": 690, "max": 710}}}})]},
    {"id": "R4", "description": "Stick press prints CLICK, quiet prints nothing", "steps": [
        _step({"joy1": {"pressed": True}, "mic1": {"adc": 100}}, {"board": {"serial_contains": "CLICK", "serial_excludes": "LOUD"}})]},
])
GENERIC = r"""
unsigned long lastTel = 0; bool loud = false, click = false;
void setup() {
  Serial.begin(115200);
  pinMode(PIN_JOY1_SW, INPUT_PULLUP);
  pinMode(PIN_PIR1_OUT, INPUT);
  pinMode(PIN_RELAY1_IN, OUTPUT); digitalWrite(PIN_RELAY1_IN, HIGH);   // active-LOW module: start off
  pinMode(PIN_LED1_ANODE, OUTPUT);
}
void loop() {
  int x = analogRead(PIN_JOY1_VRX), y = analogRead(PIN_JOY1_VRY), mic = analogRead(PIN_MIC1_AO);
  bool pressed = digitalRead(PIN_JOY1_SW) == LOW, motion = digitalRead(PIN_PIR1_OUT) == HIGH;
  bool relayOn = x > 800;
  digitalWrite(PIN_RELAY1_IN, relayOn ? LOW : HIGH);
  digitalWrite(PIN_LED1_ANODE, motion);
  if ((mic > 600) != loud) { loud = mic > 600; if (loud) Serial.println(F("LOUD")); }
  if (pressed != click) { click = pressed; if (click) Serial.println(F("CLICK")); }
  if (millis() - lastTel >= 250) { lastTel = millis();
    Serial.print(F("T,joy1.x=")); Serial.print(x); Serial.print(F(",joy1.y=")); Serial.print(y);
    Serial.print(F(",joy1.pressed=")); Serial.print(pressed); Serial.print(F(",relay1.active=")); Serial.print(relayOn);
    Serial.print(F(",pir1.active=")); Serial.print(motion); Serial.print(F(",mic1.adc=")); Serial.print(mic);
    Serial.print(F(",led1.on=")); Serial.println(motion); }
}
"""

DHT_PLAN = _plan("Weather display", [
    {"id": "dht1", "part": "dht22", "options": {}},
    {"id": "lcd1", "part": "lcd1602_i2c", "options": {}},
    {"id": "dht2", "part": "dht11", "options": {}},
], [
    {"id": "R1", "description": "LCD shows the DHT22 temperature", "steps": [
        _step({"dht1": {"temperature_c": 31.5, "humidity_pct": 40}}, {"lcd1": {"text_contains": "31.5C"},
              "board": {"telemetry": {"dht1.temperature_c": {"min": 31.4, "max": 31.6}, "dht1.humidity_pct": {"min": 39.9, "max": 40.1}}}}, 5000)]},
    {"id": "R2", "description": "Negative temperature and the DHT11 humidity", "steps": [
        _step({"dht1": {"temperature_c": -5.2}, "dht2": {"temperature_c": 22, "humidity_pct": 65}},
              {"lcd1": {"text_contains": "-5.2C"}, "board": {"telemetry": {"dht2.humidity_pct": 65, "dht2.temperature_c": 22}}}, 5000)]},
])
DHT_SKETCH = r"""
#include <Wire.h>
#include <DHT.h>
#include <LiquidCrystal_I2C.h>
DHT dht(PIN_DHT1_DATA, DHT22);
DHT dht11(PIN_DHT2_DATA, DHT11);
LiquidCrystal_I2C lcd(0x27, 16, 2);
float t = 0, h = 0, t2 = 0, h2 = 0; unsigned long lastRead = 0, lastTel = 0;
void setup() {
  Serial.begin(115200);
  dht.begin(); dht11.begin();
  lcd.init(); lcd.backlight();
}
void loop() {
  if (millis() - lastRead >= 2000 || lastRead == 0) {
    lastRead = millis();
    float a = dht.readTemperature(), b = dht.readHumidity(), c = dht11.readTemperature(), d = dht11.readHumidity();
    if (!isnan(a)) t = a; if (!isnan(b)) h = b; if (!isnan(c)) t2 = c; if (!isnan(d)) h2 = d;
    lcd.setCursor(0, 0); lcd.print(F("T ")); lcd.print(t, 1); lcd.print(F("C   "));
    lcd.setCursor(0, 1); lcd.print(F("H ")); lcd.print(h, 0); lcd.print(F("%   "));
  }
  if (millis() - lastTel >= 250) { lastTel = millis();
    Serial.print(F("T,dht1.temperature_c=")); Serial.print(t, 1); Serial.print(F(",dht1.humidity_pct=")); Serial.print(h, 1);
    Serial.print(F(",dht2.temperature_c=")); Serial.print(t2, 1); Serial.print(F(",dht2.humidity_pct=")); Serial.println(h2, 1); }
}
"""

IMU_PLAN = _plan("Tilt alarm", [
    {"id": "imu1", "part": "mpu6050", "options": {}},
    {"id": "led1", "part": "led", "options": {"color": "red", "dimmable": False}},
], [
    {"id": "R1", "description": "Tilted forward: LED on, pitch reported", "steps": [
        _step({"imu1": {"pitch_deg": 40}}, {"led1": {"on": True}, "board": {"telemetry": {"imu1.pitch_deg": {"min": 37, "max": 43}}}}, 1500)]},
    {"id": "R2", "description": "Rolled: roll reported, level again: LED off", "steps": [
        _step({"imu1": {"roll_deg": -35}}, {"led1": {"on": True}, "board": {"telemetry": {"imu1.roll_deg": {"min": -38, "max": -32}}}}, 1500),
        _step({"imu1": {"roll_deg": 5}}, {"led1": {"on": False}}, 1500)]},
])
IMU = r"""
#include <Wire.h>
#include <MPU6050_light.h>
MPU6050 mpu(Wire);
const float TILT_DEG = 30;
unsigned long lastTel = 0;
void setup() {
  Serial.begin(115200);
  pinMode(PIN_LED1_ANODE, OUTPUT);
  Wire.begin();
  mpu.begin();
  mpu.calcOffsets();
}
void loop() {
  mpu.update();
  float pitch = mpu.getAngleY(), roll = mpu.getAngleX();
  digitalWrite(PIN_LED1_ANODE, fabs(pitch) > TILT_DEG || fabs(roll) > TILT_DEG);
  if (millis() - lastTel >= 250) { lastTel = millis();
    Serial.print(F("T,imu1.pitch_deg=")); Serial.print(pitch, 1); Serial.print(F(",imu1.roll_deg=")); Serial.print(roll, 1);
    Serial.print(F(",led1.on=")); Serial.println(digitalRead(PIN_LED1_ANODE)); }
}
"""

CASES += [
    ("button + buzzer", "uno", BUTTON_PLAN, BUTTON, True),
    ("button without pull-up", "uno", BUTTON_PLAN, BUTTON.replace("INPUT_PULLUP", "INPUT"), False),
    ("pot + RGB LED", "uno", POT_PLAN, POT, True),
    ("joystick, relay, PIR, sound", "uno", GENERIC_PLAN, GENERIC, True),
    ("relay with inverted active level", "uno", GENERIC_PLAN, GENERIC.replace("relayOn ? LOW : HIGH", "relayOn ? HIGH : LOW"), False),
    ("DHT22 + DHT11 + LCD", "uno", DHT_PLAN, DHT_SKETCH, True),
    ("DHT22 read as DHT11", "uno", DHT_PLAN, DHT_SKETCH.replace("DHT dht(PIN_DHT1_DATA, DHT22);", "DHT dht(PIN_DHT1_DATA, DHT11);"), False),
    ("MPU6050 tilt", "uno", IMU_PLAN, IMU, True),
    ("MPU6050 axes swapped", "uno", IMU_PLAN, IMU.replace("pitch = mpu.getAngleY(), roll = mpu.getAngleX()", "pitch = mpu.getAngleX(), roll = mpu.getAngleY()"), False),
    ("MPU6050 on Mega", "mega", IMU_PLAN, IMU, True),
]


# ---------- passive contacts, comparator outputs, OLED fonts ----------

TILT_PLAN = _plan("Tilt alarm", [
    {"id": "tilt1", "part": "tilt_switch", "options": {}},
    {"id": "led1", "part": "led", "options": {"color": "red", "dimmable": False}},
], [
    {"id": "R1", "description": "Tilted: LED on; upright: off", "steps": [
        _step({"tilt1": {"active": True}}, {"led1": {"on": True}}),
        _step({"tilt1": {"active": False}}, {"led1": {"on": False}}, hold=300)]},
])
TILT = r"""
unsigned long lastTel = 0;
void setup() { Serial.begin(115200); pinMode(PIN_TILT1_A, INPUT_PULLUP); pinMode(PIN_LED1_ANODE, OUTPUT); }
void loop() {
  bool tilted = digitalRead(PIN_TILT1_A) == LOW;
  digitalWrite(PIN_LED1_ANODE, tilted);
  if (millis() - lastTel >= 250) { lastTel = millis();
    Serial.print(F("T,tilt1.active=")); Serial.print(tilted); Serial.print(F(",led1.on=")); Serial.println(tilted); }
}
"""

DO_PLAN = _plan("Comparator outputs", [
    {"id": "mic1", "part": "sound_sensor", "options": {"use": ["do"]}},
    {"id": "soil1", "part": "soil_moisture", "options": {"use": ["do"]}},
    {"id": "fire1", "part": "flame_sensor", "options": {"use": ["ao"]}},
], [
    {"id": "R1", "description": "Loud sound trips the sound module's DO", "steps": [
        _step({"mic1": {"adc": 800}}, {"board": {"serial_contains": "LOUD"}}),
        _step({"mic1": {"adc": 100}}, {"board": {"serial_contains": "QUIET"}})]},
    {"id": "R2", "description": "Wet soil pulls the soil module's DO low", "steps": [
        _step({"soil1": {"adc": 200}}, {"board": {"serial_contains": "WET", "serial_excludes": "LOUD"}})]},
    {"id": "R3", "description": "Flame drops the flame module's analog output", "steps": [
        _step({"fire1": {"active": True}}, {"board": {"serial_contains": "FIRE"}})]},
    {"id": "R4", "description": "Nothing happening prints none of them", "steps": [
        _step({"mic1": {"adc": 100}, "soil1": {"adc": 900}, "fire1": {"active": False}},
              {"board": {"serial_excludes": "WET"}}),
        _step({}, {"board": {"serial_excludes": "FIRE"}})]},
])
DO_SKETCH = r"""
unsigned long lastTel = 0; int loudWas = -1, wetWas = -1, fireWas = -1;
void setup() { Serial.begin(115200); pinMode(PIN_MIC1_DO, INPUT); pinMode(PIN_SOIL1_DO, INPUT); pinMode(PIN_FIRE1_DO, INPUT); }
void loop() {
  int loud = digitalRead(PIN_MIC1_DO) == HIGH, wet = digitalRead(PIN_SOIL1_DO) == LOW, fire = analogRead(PIN_FIRE1_AO) < 500;
  if (loud != loudWas) { loudWas = loud; Serial.println(loud ? F("LOUD") : F("QUIET")); }
  if (wet != wetWas) { wetWas = wet; if (wet) Serial.println(F("WET")); }
  if (fire != fireWas) { fireWas = fire; if (fire) Serial.println(F("FIRE")); }
  if (millis() - lastTel >= 250) { lastTel = millis();
    Serial.print(F("T,mic1.adc=")); Serial.print(analogRead(PIN_MIC1_AO)); Serial.print(F(",soil1.adc=")); Serial.print(analogRead(PIN_SOIL1_AO));
    Serial.print(F(",fire1.active=")); Serial.println(digitalRead(PIN_FIRE1_DO) == LOW); }
}
"""

BIG_TEXT = NIGHT_LIGHT.replace("    oled.setCursor(0, 0);\n    oled.print(F(\"Light: \"));",
                               "    oled.set2X();\n    oled.setCursor(0, 0);\n    oled.print(F(\"Light: \"));")

# ---------- new parts added as JSON cards only (no code) ----------

EXTRA_CARDS = {
    "reed_switch": {
        "name": "Reed switch", "aliases": ["reed switch", "magnetic switch"],
        "pins": {"a": {"role": "digital_in", "label": "one lead"}, "b": {"rail": "GND", "label": "other lead"}},
        "sim": [{"model": "contact", "pins": {"contact": "a"}, "io": {"magnet_near": "closed"}}],
        "extra": {}, "input_mode": "INPUT_PULLUP", "active_level": "LOW", "pin_ma": 0, "vcc_range": None,
        "rail_ma": 0, "libraries": [], "notes": ["Closes when a magnet is near; use INPUT_PULLUP."],
    },
    "laser_module": {
        "name": "Laser diode module (KY-008)", "aliases": ["laser module", "ky-008"],
        "pins": {"s": {"role": "digital_out", "label": "S"}, "vcc": {"rail": "5V", "label": "middle"}, "gnd": {"rail": "GND", "label": "-"}},
        "sim": "generic_digital_out",
        "extra": {}, "active_level": "HIGH", "pin_ma": 0, "vcc_range": [4.5, 5.5], "rail_ma": 30, "libraries": [],
        "notes": ["S HIGH turns the laser on. Never point it at eyes."],
        "safety_notes": {"always": ["Never point the laser at anyone's eyes."]},
    },
    "lcd2004_i2c": {
        "name": "20x4 I2C LCD", "aliases": ["2004 lcd", "20x4 lcd"],
        "pins": {"gnd": {"rail": "GND", "label": "GND"}, "vcc": {"rail": "5V", "label": "VCC"},
                 "sda": {"role": "i2c_sda", "label": "SDA"}, "scl": {"role": "i2c_scl", "label": "SCL"}},
        "sim": [{"model": "hd44780_i2c", "pins": {}, "io": {"text_contains": "text"},
                 "params": {"address": "@i2c_address", "cols": 20, "rows": 4}}],
        "extra": {}, "i2c_address": "0x27", "pin_ma": 0, "vcc_range": [4.5, 5.5], "rail_ma": 30,
        "libraries": ["Wire.h", "LiquidCrystal_I2C.h"], "library_packages": {"LiquidCrystal_I2C.h": "LiquidCrystal I2C"},
        "notes": ["LiquidCrystal_I2C lcd(0x27, 20, 4); lcd.init(); lcd.backlight();"],
    },
}
JSON_PLAN = _plan("Door alarm", [
    {"id": "reed1", "part": "reed_switch", "options": {}},
    {"id": "laser1", "part": "laser_module", "options": {}},
    {"id": "lcd1", "part": "lcd2004_i2c", "options": {}},
], [
    {"id": "R1", "description": "Door opens (magnet away): laser on, row 4 says OPEN", "steps": [
        _step({"reed1": {"magnet_near": False}}, {"laser1": {"active": True}, "lcd1": {"text_contains": "Door: OPEN"}}),
        _step({"reed1": {"magnet_near": True}}, {"laser1": {"active": False}, "lcd1": {"text_contains": "Door: SHUT"}})]},
])
JSON_SKETCH = r"""
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
LiquidCrystal_I2C lcd(0x27, 20, 4);
unsigned long lastTel = 0; int shown = -1;
void setup() { Serial.begin(115200); pinMode(PIN_REED1_A, INPUT_PULLUP); pinMode(PIN_LASER1_S, OUTPUT); lcd.init(); lcd.backlight(); }
void loop() {
  bool near = digitalRead(PIN_REED1_A) == LOW;
  digitalWrite(PIN_LASER1_S, !near);
  if (near != shown) { shown = near; lcd.setCursor(0, 3); lcd.print(near ? F("Door: SHUT") : F("Door: OPEN")); }
  if (millis() - lastTel >= 250) { lastTel = millis();
    Serial.print(F("T,reed1.magnet_near=")); Serial.print(near); Serial.print(F(",laser1.active=")); Serial.println(!near); }
}
"""

CASES += [
    ("tilt switch", "uno", TILT_PLAN, TILT, True),
    ("tilt switch without pull-up", "uno", TILT_PLAN, TILT.replace("INPUT_PULLUP", "INPUT"), False),
    ("sound/soil DO and flame AO", "uno", DO_PLAN, DO_SKETCH, True),
    ("soil DO read the wrong way round", "uno", DO_PLAN, DO_SKETCH.replace("digitalRead(PIN_SOIL1_DO) == LOW", "digitalRead(PIN_SOIL1_DO) == HIGH"), False),
    ("OLED 2x text", "uno", PLAN_EXAMPLE, BIG_TEXT, True),
    ("JSON-only parts: reed, laser, 20x4 LCD", "uno", JSON_PLAN, JSON_SKETCH, True, EXTRA_CARDS),
    ("JSON-only parts: wrong LCD text", "uno", JSON_PLAN, JSON_SKETCH.replace("Door: SHUT", "Door: shut"), False, EXTRA_CARDS),
]

# Lint cases: (name, card keys, app code, should pass lint)
LINT_CASES = [
    ("OLED 5x7 font", ["ssd1306"], NIGHT_LIGHT, True),
    ("OLED large font", ["ssd1306"], NIGHT_LIGHT.replace("setFont(System5x7)", "setFont(Arial14)"), False),
    ("OLED without a font", ["ssd1306"], NIGHT_LIGHT.replace("  oled.setFont(System5x7);\n", ""), False),
]
