// Words shown on the home page: the About box and the FAQ.

export const ABOUT = [
  {
    title: 'What SiliconLoop does',
    body: [
      'You photograph a handful of electronic parts and say, in one sentence, what you want them to do. SiliconLoop designs the circuit, writes the Arduino firmware and proves it works in a simulated Arduino before you touch a single wire.',
      'You get three things that always agree with each other: a live twin of your gadget running the exact compiled firmware, the firmware itself, and a step-by-step build report.',
    ],
  },
  {
    title: 'How a build runs',
    steps: [
      ['Recognise', 'Muse Spark Open identifies the components in your photos. You confirm or correct the list.'],
      ['Ground', 'Datasheet facts are pulled from approved manufacturer and distributor sites and checked quote by quote.'],
      ['Plan', 'Muse Spark Open turns your sentence into behaviour rules a program can test, such as "when the light reading drops below 300, the LED is on within one second".'],
      ['Wire', 'A deterministic pin allocator chooses every pin and resistor from the board\'s real capabilities. No model ever picks a pin.'],
      ['Check', 'An electrical safety check limits current per pin, the USB power budget and voltage ranges, and refuses anything near mains power.'],
      ['Write, compile, simulate, repair', 'Muse Spark Open writes the firmware. It is compiled with arduino-cli and run in an AVR simulator against every rule. Failures go back to the model with the exact report, up to three times.'],
    ],
  },
  {
    title: 'Why simulation is the proof',
    body: [
      'The simulator runs the very binary you will flash, instruction by instruction, on a model of the real chip. Every external part has a behavioural model: the servo measures pulse widths, the ultrasonic sensor answers with echo pulses, the OLED decodes the I2C stream into pixels.',
      'A build is only marked verified when every behaviour rule and every health check passes. If it cannot be proven, you get an honest failure instead of a binary that might work.',
    ],
  },
  {
    title: 'What you get',
    list: [
      'A live twin you can play with: cover the light sensor, move a hand closer, press the button.',
      'The sketch (.ino) and the compiled firmware (.hex), with one-click flashing from Chrome or Edge.',
      'A build report: parts list, one-connection-per-step wiring, pin table, safety notes and a checklist to compare your real gadget with the twin.',
      'A chat assistant that answers questions about your build.',
    ],
  },
  {
    title: 'Built with Muse Spark Open',
    body: [
      'Every AI step, from recognising parts to repairing firmware, runs on Muse Spark Open, a Meta open-weight model. Everything electrical is decided by deterministic code that you can read and test.',
    ],
  },
  {
    title: 'Limits',
    list: [
      'Boards: Arduino Uno, Arduino Nano and Arduino Mega 2560.',
      'Parts: the curated catalogue plus simple digital, analog and switched modules found on approved datasheet sites.',
      'Low-voltage projects only. Anything that touches mains electricity is refused.',
      'SiliconLoop checks the design, not your wiring. Follow the report and compare the real gadget with the twin.',
    ],
  },
];

export const FAQ = [
  ['Do I need any hardware to try it?', 'No. Photos of components work, even stock photos from a shop. The twin runs in your browser, so you can see the gadget working before you buy or wire anything.'],
  ['Which boards are supported?', 'Arduino Uno, Arduino Nano (including clones with the old bootloader) and Arduino Mega 2560. Choose your board before you upload photos, because the pins and the simulator depend on it.'],
  ['Which parts can I use?', 'LEDs, RGB LEDs, buttons, potentiometers, joysticks, light sensors, buzzers, SG90 servos, HC-SR04 ultrasonic sensors, PIR, IR, tilt, touch, sound, soil and flame sensors, relay modules, DHT11 and DHT22, MPU6050, SSD1306 OLEDs and 16x2 I2C LCDs. Press Supported parts to see the full list with what each part can sense or do.'],
  ['How do you know the firmware works?', 'The compiled binary runs in an AVR simulator against behaviour rules written from your sentence, with models of every part wired exactly as the report says. A build is verified only when every rule passes.'],
  ['Is it safe to build?', 'Every design passes an electrical safety check before you see it: series resistors, current per pin, the USB power budget and voltage ranges. Projects involving mains electricity are refused. Always unplug USB while you wire.'],
  ['My real gadget does not behave like the twin. What now?', 'The design is proven, so the difference is almost always a wiring slip. Open the report\'s compare-with-your-twin checklist: each behaviour lists the most likely mistake. You can also ask the chat assistant.'],
  ['How do I put the firmware on my board?', 'Press Flash to board in Chrome or Edge on a computer, choose the Arduino\'s port and wait for the tick. Without Web Serial, download the sketch and upload it with the Arduino IDE; the report lists the libraries to install.'],
  ['Are my photos stored?', 'No. Photos are sent once to identify the parts. SiliconLoop keeps only a fingerprint of them so the same photos are recognised instantly next time. Phone hand-off photos are held in memory for at most 15 minutes.'],
  ['Which AI model does this use?', 'Muse Spark Open, a Meta open-weight model, for recognising parts, suggesting ideas, planning rules, writing and repairing firmware and answering questions. Pins, resistors and safety are decided by deterministic code.'],
];
