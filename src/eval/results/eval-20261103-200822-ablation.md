# SiliconLoop evaluation, 2026-11-03 20:01

Model: Muse Spark Open. Every build ran through the website's pipeline (design, firmware, compile, simulation test, up to 3 repairs) with the parts given as confirmed.

## Ablation: Muse Spark Open alone

Same wishes, same wiring, same behaviour rules and test runner. The model got only the wish, the pin connections and the serial format, and wrote the sketch in one shot (no part cards, facts, example, code rules or repairs).

| Metric | Result |
|---|---|
| Compiles | 20/22 (91%) |
| Passes every rule and health check | 6/22 (27%) |
| Rules passed | 40/81 (49%) |
| Cost | $0.588 for 22 sketches |

| Wish | Board | Compiled | Rules passed | Verified |
|---|---|---|---|---|
| t1-blink | uno | yes | 0/3 | no |
| t1-hold | nano | yes | 2/2 | yes |
| t1-toggle | uno | yes | 2/3 | no |
| t1-buzz | uno | yes | 0/2 | no |
| t1-touch | nano | yes | 0/2 | no |
| t1-knob | uno | yes | 4/4 | yes |
| t2-dark | uno | yes | 4/4 | yes |
| t2-near | uno | yes | 2/2 | yes |
| t2-pir | nano | yes | 0/3 | no |
| t2-soil | uno | yes | 0/2 | no |
| t2-flame | uno | yes | 0/3 | no |
| t2-temp | uno | no | 0/4 | no |
| t2-tilt | uno | yes | 5/6 | no |
| t2-dim | mega | yes | 4/4 | yes |
| t3-night | uno | no | 0/5 | no |
| t3-parking | uno | yes | 0/5 | no |
| t3-weather | mega | yes | 3/5 | no |
| t3-clap | nano | yes | 0/2 | no |
| t3-joy | mega | yes | 3/3 | yes |
| t3-alarm | nano | yes | 6/7 | no |
| pw-servos | mega | yes | 2/4 | no |
| pw-relay-lcd | uno | yes | 3/6 | no |
