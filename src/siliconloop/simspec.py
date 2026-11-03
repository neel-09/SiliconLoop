"""Part cards -> simulation specs. The only place that knows how a card's 'sim' entry becomes rule keys and model
instances, so every other module (prompts, plan validation, telemetry, the simulator) stays part-agnostic.

A card's 'sim' is a list of model instances (or the name of a template in parts.json '_generic'):
  {"model": <type in sim_models.json>, "pins": {terminal: card pin key | {"role": pin role}},
   "params": {...} ('@field' takes the card's field), "io": {rule key: native key | {"key", "requires_option"}}}
"""
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
MODELS = {k: v for k, v in json.loads((DATA / "sim_models.json").read_text(encoding="utf-8")).items() if not k.startswith("_")}
BOARD_IO = {"in": {}, "out": {k: v["type"] for k, v in
                              json.loads((DATA / "sim_models.json").read_text(encoding="utf-8"))["_board"]["native"].items()}}
_GENERIC = json.loads((DATA / "parts.json").read_text(encoding="utf-8"))["_generic"]


class SimSpecError(ValueError):
    pass


def instances(card):
    sim = card.get("sim")
    if isinstance(sim, str):
        if sim not in _GENERIC:
            raise SimSpecError(f"{card.get('name')}: unknown sim template '{sim}'")
        sim = _GENERIC[sim]["sim"]
    if not isinstance(sim, list) or not sim:
        raise SimSpecError(f"{card.get('name')}: has no simulation model")
    return sim


def _bind(card, binding):
    """Terminal binding -> card pin key (or None). {'role': r} picks the card's pin with that role."""
    if isinstance(binding, str):
        return binding if binding in card["pins"] else None
    role = binding.get("role")
    cands = [p for p, s in card["pins"].items() if s.get("role") == role]
    cands.sort(key=lambda p: bool(card["pins"][p].get("optional")))
    return cands[0] if cands else None


def _params(card, inst):
    model = MODELS[inst["model"]]
    out = dict(model.get("params", {}))
    for k, v in (inst.get("params") or {}).items():
        if isinstance(v, str) and v.startswith("@"):
            v = card.get(v[1:])
            if v is None:
                continue  # card does not state it: keep the model default
        out[k] = v
    return out


def resolve(card, options=None):
    """Model instances with pins bound to card pin keys. With options (an intent's options), instances whose
    required pins are optional and not in options.use are dropped, and conditional rule keys are applied.
    options=None means 'everything the part could offer' (for describing the part to the planner)."""
    out = []
    used = set((options or {}).get("use") or [])
    for inst in instances(card):
        model = MODELS.get(inst["model"])
        if model is None:
            raise SimSpecError(f"{card.get('name')}: unknown model type '{inst['model']}'")
        pins, skip = {}, False
        for term, tspec in model["terminals"].items():
            pin = _bind(card, inst.get("pins", {}).get(term, {})) if term in inst.get("pins", {}) else None
            optional_pin = pin and card["pins"][pin].get("optional")
            if pin and optional_pin and options is not None and pin not in used:
                pin = None
            if pin is None and not tspec.get("optional"):
                skip = True  # a required terminal is unwired: this instance does not exist in this design
            pins[term] = pin
        if skip and options is not None:
            continue
        io = {}
        for rkey, nat in inst["io"].items():
            nkey, req = (nat, None) if isinstance(nat, str) else (nat["key"], nat.get("requires_option"))
            if nkey not in model["native"]:
                raise SimSpecError(f"{card.get('name')}: '{nkey}' is not a {inst['model']} key")
            n = model["native"][nkey]
            io[rkey] = {"native": nkey, "dir": n["dir"], "type": n["type"], "default": n.get("default"),
                        "requires_option": req}
        out.append({"model": inst["model"], "pins": pins, "params": _params(card, inst), "io": io})
    return out


def io_of(card, options=None):
    """{'in': {rule key: type}, 'out': {...}, 'conditional': {rule key: option}} for one part (or one intent)."""
    res = {"in": {}, "out": {}, "conditional": {}}
    for inst in resolve(card, options):
        for k, e in inst["io"].items():
            if e["requires_option"]:
                res["conditional"][k] = e["requires_option"]
                if options is not None and not options.get(e["requires_option"]):
                    continue
            res[e["dir"]][k] = e["type"]
    return res


def defaults_of(card, options=None):
    return {k: e["default"] for inst in resolve(card, options) for k, e in inst["io"].items()
            if e["dir"] == "in" and e["default"] is not None}


def check_cards(cards):
    """Problems in part cards' sim entries (used by the self-test so a bad card is caught before a build)."""
    p = []
    for key, card in cards.items():
        try:
            insts = resolve(card)
        except SimSpecError as e:
            p.append(str(e))
            continue
        seen = {}
        for inst in insts:
            for t, pin in inst["pins"].items():
                if pin is None and not MODELS[inst["model"]]["terminals"][t].get("optional"):
                    p.append(f"{key}: terminal '{t}' of {inst['model']} is not bound to a pin")
            for k in inst["io"]:
                if k in seen:
                    p.append(f"{key}: rule key '{k}' is defined twice")
                seen[k] = True
    return p
