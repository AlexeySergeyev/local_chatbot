from flask import Flask, render_template, request, Response, session, stream_with_context
import requests
import json
import os
import uuid
from datetime import datetime, timezone
from threading import Lock

# ---- Config ----


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL_NAME = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")  # change to your preferred model
CONNECT_TIMEOUT = _env_float("OLLAMA_CONNECT_TIMEOUT", 10.0)
READ_TIMEOUT = _env_float("OLLAMA_READ_TIMEOUT", 300.0)
REQUEST_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)


def current_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "").replace("T", " ")
# ----------------

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me")  # set a real secret in prod

conversation_store = {}
conversation_lock = Lock()


def _ensure_conversation_id():
    conv_id = session.get("conversation_id")
    if not conv_id:
        conv_id = str(uuid.uuid4())
        session["conversation_id"] = conv_id
    return conv_id


def get_history():
    conv_id = _ensure_conversation_id()
    with conversation_lock:
        history = conversation_store.get(conv_id, [])
        return [dict(item) for item in history]


def set_history(history):
    conv_id = _ensure_conversation_id()
    with conversation_lock:
        conversation_store[conv_id] = [dict(item) for item in history]
    session.modified = True

@app.route("/", methods=["GET"])
def index():
    history = get_history()
    return render_template("index.html", history=history, model=MODEL_NAME, ollama_url=OLLAMA_URL)

@app.route("/reset", methods=["POST"])
def reset():
    conv_id = session.pop("conversation_id", None)
    if conv_id:
        with conversation_lock:
            conversation_store.pop(conv_id, None)
    return ("", 204)

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True)
    user_msg = (data or {}).get("message", "").strip()
    if not user_msg:
        return ("No message", 400)

    history = list(get_history())
    # Ollama expects messages like [{"role":"user","content":"..."}, {"role":"assistant","content":"..."}...]
    ollama_messages = [{"role": item["role"], "content": item["content"]} for item in history]
    ollama_messages.append({"role": "user", "content": user_msg})

    user_timestamp = current_timestamp()

    url = f"{OLLAMA_URL}/api/chat"
    payload = {
        "model": MODEL_NAME,
        "messages": ollama_messages,
        "stream": True
    }
    assistant_text = []

    def iter_tokens():
        with requests.post(
            url,
            json=payload,
            stream=True,
            timeout=REQUEST_TIMEOUT,
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = obj.get("message")
                if msg and msg.get("role") == "assistant":
                    token = msg.get("content", "")
                    assistant_text.append(token)
                    yield token

                if obj.get("done"):
                    break

    def finalize_history():
        assistant_full = "".join(assistant_text)
        history.append({
            "role": "user",
            "content": user_msg,
            "timestamp": user_timestamp,
        })
        history.append({
            "role": "assistant",
            "content": assistant_full,
            "timestamp": current_timestamp(),
        })
        set_history(history)
        return assistant_full

    if app.config.get("TESTING"):
        body = "".join(iter_tokens())
        finalize_history()
        return Response(body, mimetype="text/plain; charset=utf-8")

    # stream from Ollama and forward chunks to client
    def stream():
        for token in iter_tokens():
            yield token
        finalize_history()

    return Response(stream_with_context(stream()), mimetype="text/plain; charset=utf-8")

if __name__ == "__main__":
    # First run will auto-pull the model when Ollama sees it
    # Make sure you have `ollama serve` running (usually started automatically).
    app.run(host="127.0.0.1", port=5000, debug=True)