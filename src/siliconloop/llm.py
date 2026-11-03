"""Muse Spark Open (Baseten) client + cost log. Every model call in SiliconLoop goes through chat()."""
import base64, json, os, re, threading, time
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

from . import budget

load_dotenv()

SPARK_MODEL = "meta-models/muse-spark-open-1026"
spark = OpenAI(base_url="https://inference.baseten.co/v1", api_key=os.getenv("baseten_api_key"), timeout=180, max_retries=1)

# USD per million tokens: (input, output).
PRICES = {SPARK_MODEL: (float(os.getenv("SPARK_PRICE_IN", "1.25")), float(os.getenv("SPARK_PRICE_OUT", "4.25"))),}
COST_LOG = Path(os.getenv("COST_LOG") or Path(__file__).resolve().parents[2] / "costs.jsonl")
_lock = threading.Lock()

def _log(job, model, resp, seconds):
    u = resp.usage
    tin, tout = (u.prompt_tokens, u.completion_tokens) if u else (0, 0)
    details = getattr(u, "completion_tokens_details", None) if u else None
    p_in, p_out = PRICES.get(model, (0.0, 0.0))
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "job": job,
        "model": model,
        "in": tin,
        "out": tout,
        "reasoning": getattr(details, "reasoning_tokens", None),
        "usd": round(tin * p_in / 1e6 + tout * p_out / 1e6, 6),
        "sec": round(seconds, 2),}
    COST_LOG.parent.mkdir(parents=True, exist_ok=True)
    global _total
    with _lock:
        if _total is None:
            _total = _last_total()
        _total = round(_total + row["usd"], 6)
        row["total_usd"] = _total  # running total of all spend so far
        with COST_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")


_total = None  # cached running total, so the budget guard does not re-read the log on every request


def total_spent():
    """USD spent so far according to COST_LOG."""
    global _total
    with _lock:
        if _total is None:
            _total = _last_total()
        return _total


def _last_total():
    """total_usd from the last line of the log (sums older lines that predate the running total)."""
    if not COST_LOG.exists():
        return 0.0
    lines = [l for l in COST_LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return 0.0
    last = json.loads(lines[-1])
    if "total_usd" in last:
        return last["total_usd"]
    return sum(json.loads(l).get("usd", 0) for l in lines)

def chat(job, model, messages, **kw):
    """Every model call goes through here so it is logged to COST_LOG."""
    kw.setdefault("reasoning_effort", os.getenv("SPARK_REASONING", "low"))  # model default is xhigh: slow and costly
    budget.hard_stop(total_spent())  # never call the model once the budget cap is reached
    t = time.monotonic()
    resp = spark.chat.completions.create(model=model, messages=messages, **kw)
    _log(job, model, resp, time.monotonic() - t)
    return resp


def text_of(resp):
    """Answer text without any inline reasoning block."""
    content = resp.choices[0].message.content or ""
    return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()


def image_part(data, mime="image/jpeg"):
    """An image as an OpenAI-style content part."""
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}


def extract_json(text):
    """First JSON object in the text (tolerates code fences and surrounding prose)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object found")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


def chat_json(job, model, messages, validate=None, retries=2, on_problems=None, **kw):
    """Chat until the reply is a JSON object that passes validate(obj) -> list of problems (empty = OK).
    on_problems(attempt, problems) is called before each retry (used for the live execution trace)."""
    msgs = list(messages)
    for attempt in range(retries + 1):
        reply = text_of(chat(job, model, msgs, **kw))
        try:
            obj = extract_json(reply)
            problems = validate(obj) if validate else []
        except ValueError as e:
            problems = [f"Your reply was not valid JSON ({e})."]
        if not problems:
            return obj
        if attempt == retries:
            raise ValueError(f"{job}: model output still invalid after {retries + 1} tries: {problems}")
        if on_problems:
            on_problems(attempt + 1, problems)
        msgs += [{"role": "assistant", "content": reply},
                 {"role": "user", "content": "Fix these problems and reply with the corrected JSON only:\n- " + "\n- ".join(problems)}]


def code_of(text):
    """The C++ code block from a reply (or the whole reply if there is no fence)."""
    m = re.search(r"```(?:cpp|c\+\+|arduino|ino|c)?\s*\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


def cost_summary():
    """Total spend, plus calls and USD per model and per job, from COST_LOG."""
    out = {"total_usd": 0.0, "calls": 0, "by_model": {}, "by_job": {}}
    if COST_LOG.exists():
        for line in COST_LOG.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            out["calls"] += 1
            out["total_usd"] = round(out["total_usd"] + r["usd"], 6)
            for group, key in (("by_model", r["model"]), ("by_job", r["job"].split(":")[0])):
                t = out[group].setdefault(key, {"calls": 0, "usd": 0.0})
                t["calls"] += 1
                t["usd"] = round(t["usd"] + r["usd"], 6)
    return out

if __name__ == "__main__":
    # Day-0 probe: python -m siliconloop.llm
    r = chat("probe", SPARK_MODEL, [{"role": "user", "content": "Hello!"}])
    print("Spark Open:", r.choices[0].message.content)
    c = chat("probe", SPARK_MODEL, [{"role": "user", "content": "Which number is larger, 9.11 or 9.8?"}])
    print("Spark Open (2):", c.choices[0].message.content)
    print("Costs so far:", cost_summary())
