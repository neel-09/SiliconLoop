# SiliconLoop

**Imagination to silicon, proven in simulation.**

**Powered by Muse Spark Open, Meta's open-weight model, served on [Baseten](https://www.baseten.co/).** Every AI step
in SiliconLoop runs on this one open-weight model; no closed model is used anywhere.

Photograph a handful of electronic parts and say, in one sentence, what you want them to do. SiliconLoop designs the
circuit, writes the Arduino firmware and runs that exact binary in a simulated Arduino until every behaviour you asked
for passes, before you touch a single wire. You get three things that always agree with each other:

1. **A live twin** of your gadget in the browser, running the verified firmware on the same part models as the test.
2. **The firmware**: the sketch (`.ino`) and the compiled `.hex`, with one-click flashing from Chrome or Edge.
3. **A build report**: parts list, one-connection-per-step wiring, pin table, power plan, safety notes and a
   checklist to compare the real gadget with the twin. A chat assistant answers questions about the build.

Boards: Arduino Uno, Arduino Nano (including clones with the old bootloader) and Arduino Mega 2560.
Parts: 22 curated parts with simulation models, plus simple digital, analog and switched modules admitted from
approved datasheet sites.

## Powered by Muse Spark Open on Baseten

| | |
|---|---|
| Model | **Muse Spark Open**, a Meta open-weight model (`meta-models/muse-spark-open-1026`) |
| Hosting | Baseten's OpenAI-compatible inference API (`https://inference.baseten.co/v1`) |
| Client | `src/siliconloop/llm.py`; every call goes through `chat()` and is logged to the cost log |
| Settings | reasoning effort `low` (seconds and well under a cent per call); images sent inline for recognition |

What Muse Spark Open does, and only Muse Spark Open:
- **Sees:** recognises the components in the user's photos, with a confidence per part.
- **Imagines:** proposes three gadgets the confirmed parts can build.
- **Plans:** turns a plain-English wish into machine-checkable behaviour rules for the simulator.
- **Codes:** writes the Arduino firmware, then repairs it from compiler and simulation reports as a tool-using loop.
- **Reads:** extracts verified facts from datasheet search snippets (each fact must match a verbatim quote).
- **Explains:** answers questions about a finished build in the build assistant.

Deterministic code decides everything electrical (pins, resistors, current, voltage, power supplies, refusals), so
the model's open weights are paired with checks anyone can read and test. Web search (Tavily) supplies raw snippets
only; its generated answers are never used, so all generated text in the system comes from Muse Spark Open.

## Results

27 wishes through the same pipeline as the website (`src/eval`), with the parts given as confirmed. Both columns use
the same model, Muse Spark Open on Baseten; the difference is the system around it:

| | SiliconLoop | Muse Spark Open alone |
|---|---|---|
| Verified: every behaviour rule and health check passes | **22 / 22 (100%)** | 6 / 22 (27%) |
| Behaviour rules passed | **79 / 79** | 40 / 81 (49%) |
| Compiles | 22 / 22 | 20 / 22 (91%) |
| Verified on the first draft, before any repair | 21 / 22 | - |
| Unsafe (mains) wishes refused | 5 / 5 | - |
| External power supply planned where the parts need it | 2 / 2 | - |

- 6 / 6 single-part, 8 / 8 sensor-to-output and 6 / 6 three-or-more-part gadgets verified; 3.6 behaviour rules per
  gadget on average; one repair round in the whole set.
- About 0.08 US dollars and 24 seconds per verified build.
- The baseline ("Muse Spark Open alone") got the same wishes, wiring, rules and test, but only the wish, the pin
  connections and the serial format, and wrote the sketch in one shot: no part cards, datasheet facts, example, code
  rules or repair loop. Every baseline sketch that compiled passed the health checks (baud, telemetry, no resets), so
  its failures are wrong behaviour, not output format. The two that did not compile included headers that are not
  real Arduino libraries.

## How a build runs

1. **Recognise.** Muse Spark Open identifies the components in the photos; the user confirms or corrects the list.
2. **Ideas (optional).** Muse Spark Open proposes three gadgets the confirmed parts can build; each is checked against
   the catalogue and pre-checked by the pin allocator.
3. **Ground.** Datasheet facts come from approved manufacturer and distributor sites (Tavily search, raw snippets
   only) and every fact must match a verbatim quote.
4. **Plan.** Muse Spark Open turns the wish into machine-checkable behaviour rules; a validator enforces coverage
   (every input set to two values, every output seen changing state, both sides of every threshold).
5. **Wire.** A deterministic pin allocator chooses every pin and resistor from the board facts. No model picks a pin.
6. **Check.** The electrical safety check limits current per pin and in total, plans the power (see below), checks
   voltage ranges and I2C addresses, and refuses anything that involves mains.
7. **Write, compile, simulate, repair.** Muse Spark Open writes the firmware; it is linted, compiled with arduino-cli
   (with flash and RAM limits) and run headless in avr8js against every rule. Failures go back to the model with the
   exact report, up to three times. Only a verified build gets a `.hex`.

### Power

Parts are powered from the Arduino's 5 V pin while every part runs on 5 V and the parts draw at most 300 mA together
(`safety.USB_PARTS_MA`). Otherwise `safety.plan_power` adds external supplies only as needed: the parts with the
largest draw move to a 5 V supply until the rest is within 300 mA, and a part that needs more than 5 V gets a supply
at a voltage inside its range. Every supply shares GND with the Arduino. Datasheet current figures may raise a part's
budget but never lower it. The report, the circuit diagram and the safety notes all show the power plan.

### Simulation test gate

Every behaviour rule gets a fresh power-up of the exact compiled binary, with the part models wired as the allocator
decided. Health checks apply to every run: 115200 baud, a telemetry line every 250 ms with every key, no serial
flooding, no resets. The build is `verified`, and the hex is served, only when everything passes.

## Run locally

Prerequisites: [uv](https://docs.astral.sh/uv/), Node.js 22, and
[arduino-cli](https://arduino.github.io/arduino-cli/) with the AVR core (`arduino-cli core install arduino:avr`).
Put your keys in `.env` at the repo root (see `.env.example`): `baseten_api_key` (a Baseten API key with access to
Muse Spark Open) and `tavily_api_key`. `.env` is git-ignored; never commit it.

One-time setup, from the repo root:
```
uv sync
cd src/sim && npm install && cd ../..
cd src/web && npm install && cd ../..
```

Then start the site and the API together, from `src/web`:
```
npm start          # rebuilds the site on every save and runs the API with auto-reload
npm run start:lan  # the same, reachable from your phone on the same Wi-Fi (for the QR camera)
```

Or with uv directly, from the repo root (build the site first with `npm run build` in `src/web`):
```
uv run uvicorn siliconloop.main:app --reload
```

Open http://localhost:8000. `?intro=0` skips the opening animation; `?build=<id>` reopens a verified build on its
twin (the Share link). Swagger UI for the API is at http://localhost:8000/docs.

Checks:
```
uv run python -m siliconloop.llm                    # probe Muse Spark Open on Baseten
uv run python -m siliconloop.simulator --selftest   # known-good and known-broken sketches through the simulation gate
```

## Evaluation

`src/eval/wishes.json` holds 27 wishes: 20 gadgets in three tiers, 2 that must plan an external power supply and
5 that must be refused. `src/eval/run.py` writes `src/eval/results/eval-<time>-system.md` and `latest-system.md`:
```
uv run python src/eval/run.py                  # everything (about 2 US dollars)
uv run python src/eval/run.py --tier 1         # one tier: 1, 2, 3, power or unsafe
uv run python src/eval/run.py --only t3-night  # one wish (comma-separate several)
uv run python src/eval/run.py --jobs 3         # three builds at a time
uv run python src/eval/run.py --ablation       # baseline: Muse Spark Open alone, same wiring, rules and test
```
The ablation reuses the latest system design for each wish, so it only spends model calls on firmware.

## Layout

- `src/siliconloop/` - Python backend: FastAPI routes, Muse Spark Open client and prompts, pin allocator, safety and
  power check, compiler, simulator bridge, report
- `src/sim/` - avr8js simulator (Uno, Nano, Mega), part models and behaviour-rule runner, shared by the backend
  test and the browser twin
- `src/web/` - Vite single-page site: intro, intake (upload or phone via QR), confirm and ideas, live execution
  trace, output (twin and circuit diagram, code, build assistant, Web Serial flashing)
- `src/eval/` - evaluation set and runner

### API

Step by step (Swagger): `POST /api/builds` (board + photos), `POST /api/builds/{id}/design`,
`POST /api/builds/{id}/firmware`, `GET /api/builds/{id}`, `GET /api/builds/{id}/sketch.ino`, `GET /api/builds/{id}/firmware.hex`.

Used by the site:
- `POST /api/builds/{id}/ideas` - three gadget ideas for the confirmed parts
- `POST /api/builds/{id}/run` - design + firmware as Server-Sent Events (the execution trace), ending with `done` or `error`
- `GET /api/builds/{id}/twin` - the verified hex plus part models for the browser twin
- `GET /api/builds/{id}/report` and `/report.md` - the deterministic build report
- `POST /api/builds/{id}/chat` - questions about one build, answered from its report, rules and sketch
- `POST /api/handoff`, `POST|GET /api/handoff/{token}`, `GET /api/handoff/{token}/{i}` - phone photo hand-off (in memory, 15 minutes)
- `GET /api/boards`, `GET /api/parts`, `GET /api/health`

### Flashing

`src/web/src/flash.js` uses Web Serial: STK500 v1 for the Uno and Nano (115200 baud, then 57600 for the old Nano
bootloader) and STK500 v2 for the Mega, with a full read-back verify. Without Web Serial, the report's flashing
steps explain the Arduino IDE route.

## Adding a part

Edit only `src/siliconloop/data/parts.json`. A card's `sim` lists instances of the reusable model types in
`data/sim_models.json` (load, contact, driven_digital, analog_source, servo_pulse, ultrasonic, dht, imu_mpu6050,
ssd1306, hd44780_i2c), binds each model terminal to one of the card's pins, sets params (`@field` reads the card) and
names the rule keys. Safety warnings (`safety_notes`), Arduino library packages (`library_packages`) and code rules
(`lint`) live on the card too. Only a part with genuinely new behaviour (a new protocol) needs a new model type in
`src/sim/parts.js`. The self-test resolves every card and runs three JSON-only test parts end to end. To show it in
the circuit diagram with a Wokwi drawing, add its pin mapping to `PARTS` in `src/web/src/twin.js`; otherwise it is
drawn as a generic module.

## Deploy to Google Cloud Run (one URL for site + API)

One-time setup (project, region and bucket names are yours to choose):
```
gcloud config set project YOUR_PROJECT
gcloud config set run/region asia-south1
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
gcloud storage buckets create gs://YOUR_BUCKET --location=asia-south1
printf '%s' "KEY" | gcloud secrets create baseten_api_key --data-file=-
printf '%s' "KEY" | gcloud secrets create tavily_api_key --data-file=-
```
Give the Cloud Run service account (by default PROJECT_NUMBER-compute@developer.gserviceaccount.com) the Secret
Manager Secret Accessor role and Storage Object User on the bucket.

Deploy (repeat for every release):
```
gcloud run deploy siliconloop --source . --allow-unauthenticated \
  --execution-environment gen2 --max-instances 1 --memory 1Gi --timeout 900 \
  --add-volume name=data,type=cloud-storage,bucket=YOUR_BUCKET \
  --add-volume-mount volume=data,mount-path=/data \
  --set-secrets baseten_api_key=baseten_api_key:latest,tavily_api_key=tavily_api_key:latest
```
The cost log is written to `gs://YOUR_BUCKET/costs.jsonl` and survives restarts and redeploys
(`gcloud storage cat gs://YOUR_BUCKET/costs.jsonl`). `--max-instances 1` keeps a single writer on the cost log.

## Credits

Built with **Muse Spark Open**, a Meta open-weight model served on **Baseten**, for every AI step: recognising
parts, suggesting ideas, planning rules, writing and repairing firmware and answering questions. Simulation by
[avr8js](https://github.com/wokwi/avr8js) (MIT); part drawings by [@wokwi/elements](https://github.com/wokwi/wokwi-elements)
(MIT); datasheet search by Tavily. SiliconLoop is an AI-based product and can make mistakes: check your wiring
against the build report before you power a circuit.
