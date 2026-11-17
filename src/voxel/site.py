"""The Voxel website, on localhost only: a Build page and a Guide page.

Run it:
    python -m voxel.serve                 # then open the address it prints

Where the keys live:
- The Anthropic key comes in the body of one POST, is held in memory for that one
  build, and is only ever used by the relay (voxel.relay), which sends it to
  api.anthropic.com. The builder process gets a one-time job token instead. The
  key is never written to disk, logged, or put in a URL, and the site refuses to
  start if ANTHROPIC_API_KEY is in its own environment or in .env.
- The Rebrickable key is read on the server (REBRICKABLE_API_KEY or .env) and is
  never sent to the page.

After each build the record folder, the job folder and the builder's output are
searched for both keys. A match is removed and the build is reported as failed.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .design import REVIEW_FILE, USAGE_FILE, prompt_style
from .relay import REDACTED, Relay, Secret
from .sequence import STEPPED_FILE
from .webjob import QUESTIONS_MARK, RESULT_MARK, SETTINGS_FILE

WEB = Path(__file__).parent / "web"
PAGES = {"/": "index.html", "/index.html": "index.html", "/guide": "guide.html", "/guide.html": "guide.html"}
STATIC = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".woff2": "font/woff2",
    ".svg": "image/svg+xml",
}
KEY_NAME = "ANTHROPIC_API_KEY"
IMAGE_HOST = "cdn.rebrickable.com"  # set pictures are only ever fetched from here
SEARCH_GAP = 1.1  # seconds between Rebrickable calls (about one per second is allowed)
SEARCH_TTL = 600.0  # seconds a search result is remembered
SEARCH_MIN, SEARCH_MAX = 2, 60  # characters
MAX_POST = 64 * 1024  # bytes
MAX_SETS = 20
MAX_REQUEST_CHARS = 500
JOB_TIMEOUT = 45 * 60.0  # seconds
ANSWER_TIMEOUT = 15 * 60.0  # seconds to answer the questions before the defaults are used
# Build settings on the Build page. The model is the one that builds; the short
# questions and review calls keep Voxel's own models.
MODELS = {"Opus": "claude-opus-5-5", "Sonnet": "claude-sonnet-5-5", "Haiku": "claude-haiku-5-5"}
EFFORTS = ("low", "medium", "high")
DEFAULT_SETTINGS = {"model": "Opus", "effort": "medium", "max_cost_usd": 2.5, "questions": False}
MIN_COST, MAX_COST = 0.25, 20.0  # USD a person may set as the limit of one build


def spend_cap(max_cost: float) -> float:
    """What the relay lets one build spend: the person's limit plus room for the
    last call, which the builder's own budget check may let start just under it."""
    return max_cost * 1.25 + 0.10


def clean_settings(given, defaults: dict) -> dict:
    """The settings of one build: what the page sent, checked, else the defaults."""
    given = given if isinstance(given, dict) else {}
    model = given.get("model", defaults["model"])
    if model not in MODELS:
        raise SiteError("Pick Opus, Sonnet or Haiku as the model.")
    effort = given.get("effort", defaults["effort"])
    if effort not in EFFORTS:
        raise SiteError("Pick low, medium or high effort.")
    cost = given.get("max_cost_usd", defaults["max_cost_usd"])
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not (MIN_COST <= cost <= MAX_COST):
        raise SiteError(f"Set the max cost between ${MIN_COST:.2f} and ${MAX_COST:.2f}.")
    questions = given.get("questions", defaults["questions"])
    if not isinstance(questions, bool):
        raise SiteError("Turn the clarifying questions on or off.")
    return {"model": model, "effort": effort, "max_cost_usd": round(float(cost), 2), "questions": questions}


def clean_questions(raw) -> list[dict]:
    """The builder's questions, cut down to what the page shows."""
    out = []
    for q in raw if isinstance(raw, list) else []:
        if not isinstance(q, dict) or not isinstance(q.get("id"), str) or not isinstance(q.get("text"), str):
            continue
        options = [str(o)[:60] for o in q.get("options") or [] if isinstance(o, (str, int, float))][:12]
        out.append({"id": q["id"][:60], "text": q["text"][:300], "options": options, "default": str(q.get("default") or "")[:60]})
    return out[:3]
RECORD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SET_NUM = re.compile(r"[A-Za-z0-9]+(-\d+)?")
# What the builder process may inherit from the server's environment.
ENV_NAMES = {
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP",
    "SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "VIRTUAL_ENV", "PYTHONPATH", "LDRAW_LIBRARY_PATH", "DISPLAY",
}  # fmt: skip
ENV_PREFIXES = ("BRICKWRIGHT_", "VOXEL_")
SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


class SiteError(Exception):
    """A problem worded for the page. Never contains a key."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --- Startup check -------------------------------------------------------------


def anthropic_key_problem(root: Path, environ=None) -> str:
    """Why the site must not start, or "". The site takes the Anthropic key from the
    page only: a key in the environment or in .env could reach the builder."""
    environ = os.environ if environ is None else environ
    if (environ.get(KEY_NAME) or "").strip():
        return f"{KEY_NAME} is set in this shell's environment. Unset it, then start the site again."
    env_file = Path(root) / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            name, sep, value = line.partition("=")
            if sep and name.strip() == KEY_NAME and value.strip().strip('"').strip("'"):
                return f"{KEY_NAME} is in {env_file}. Remove that line, then start the site again."
    return ""


def protect_memory() -> None:
    """Linux: stop other processes of the same user (the builder) reading this one."""
    if sys.platform.startswith("linux"):
        try:
            import ctypes

            ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE = 0
        except Exception:
            pass


# --- Secrets: scan and redact --------------------------------------------------


def redact_tree(folders: list[Path], secrets_: list[str]) -> list[Path]:
    """Remove every copy of each secret from every file under the folders.
    Returns the files that held one."""
    needles = [s.encode() for s in secrets_ if s]
    hit: list[Path] = []
    for folder in folders:
        if not folder.exists():
            continue
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if any(n in data for n in needles):
                for n in needles:
                    data = data.replace(n, REDACTED.encode())
                path.write_bytes(data)
                hit.append(path)
    return hit


def child_env(environ, relay_url: str, token: str, records_dir: Path) -> dict[str, str]:
    """The builder's environment: an allowlist, the relay address and the job token."""
    env = {
        name: value
        for name, value in environ.items()
        if (name in ENV_NAMES or name.startswith(ENV_PREFIXES))
        and not any(word in name.upper() for word in SECRET_WORDS)
    }
    env.update(
        ANTHROPIC_API_BASE=relay_url,
        ANTHROPIC_API_KEY=token,  # not the key: only the relay accepts it, only for this job
        LITELLM_LOCAL_MODEL_COST_MAP="True",  # no price-map download
        LITELLM_MODE="PRODUCTION",  # litellm does not load .env
        BRICKWRIGHT_RECORDS_DIR=str(records_dir),
        PYTHONUTF8="1",
    )
    return env


# --- Gallery (Guide page) ------------------------------------------------------


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def record_picture(record: Path) -> Path | None:
    for name in ("build.display.png", "build.png"):
        path = record / "out" / name
        if path.is_file():
            return path
    return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def typed_request(record: Path, meta: dict) -> str:
    """The words the person typed, or "". From settings.json or expansion.json; for older records the
    first line of the record's prompt, only when the record id confirms that line is
    the request (the id is made from it). Nothing else of that prompt is ever used."""
    for name in (SETTINGS_FILE, "expansion.json"):
        saved = _load(record / name)
        if isinstance(saved, dict) and isinstance(saved.get("request"), str) and saved["request"].strip():
            return saved["request"].strip()
    lines = str(meta.get("prompt") or "").splitlines()
    first = lines[0].strip() if lines else ""
    parts = record.name.split("-")
    id_slug = "-".join(parts[2:-1]) if len(parts) > 3 else ""
    if first and id_slug and _slug(first).startswith(id_slug):
        return first
    return ""


def model_name(model: str) -> str:
    for family in ("Haiku", "Sonnet", "Opus"):
        if family.lower() in model.lower():
            return family
    return model


def review_summary(record: Path) -> dict | None:
    """The last review round: passed, and each item with its reason."""
    rounds = _load(record / REVIEW_FILE)
    if not isinstance(rounds, list) or not rounds or not isinstance(rounds[-1], dict):
        return None
    last = rounds[-1]
    items = [
        {"item": str(v.get("item", "")), "passed": bool(v.get("passed")), "reason": str(v.get("reason", ""))}
        for v in last.get("verdicts") or []
        if isinstance(v, dict)
    ]
    return {
        "passed": bool(last.get("passed")),
        "items": items,
        "passed_count": sum(1 for i in items if i["passed"]),
    }


def record_settings(record: Path, usage: dict) -> dict:
    """The build settings of a record: settings.json (builds made on the site), else
    what usage.json says. A value that is not recorded is left out."""
    saved = _load(record / SETTINGS_FILE)
    saved = saved if isinstance(saved, dict) else {}
    out = {}
    effort = saved.get("effort") or usage.get("build_effort")
    if isinstance(effort, str) and effort:
        out["effort"] = effort
    cost = saved.get("max_cost_usd", usage.get("max_cost_usd"))
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        out["max_cost_usd"] = cost
    # Only builds made on the site record this: in older records a default answer
    # looks the same whether the question was shown or not.
    if isinstance(saved.get("questions"), bool):
        out["questions"] = saved["questions"]
    return out


def gallery_card(record: Path) -> dict | None:
    """What one Guide card shows, or None when the record has no picture."""
    meta = _load(record / "meta.json")
    if not isinstance(meta, dict) or record_picture(record) is None:
        return None
    usage = _load(record / USAGE_FILE)
    usage = usage if isinstance(usage, dict) else {}
    label = _load(record / "label.json")
    label = label if isinstance(label, dict) else {}
    generation = meta.get("generation") if isinstance(meta.get("generation"), dict) else {}
    request = typed_request(record, meta)
    model = str(usage.get("build_model") or generation.get("model") or "")
    cost = usage.get("total_cost_usd")
    if not isinstance(cost, (int, float)):
        cost = (meta.get("usage") or {}).get("cost_usd") if isinstance(meta.get("usage"), dict) else None
    turns = meta.get("turns")
    review = review_summary(record)
    settings = record_settings(record, usage)
    style = label.get("prompt_style") or usage.get("prompt_style") or (prompt_style(request) if request else "")
    return {
        "id": record.name,
        "picture": f"/records/{record.name}/picture",
        "request": request,
        "style": style if style in ("one-line", "detailed") else "",
        "model": model_name(model) if model else "",
        "cost_usd": cost if isinstance(cost, (int, float)) else None,
        "turns": turns if isinstance(turns, int) and turns > 0 else None,
        "review": (
            {"passed": review["passed"], "passed_count": review["passed_count"], "total": len(review["items"])}
            if review
            else None
        ),
        "effort": settings.get("effort") or "",
        "max_cost_usd": settings.get("max_cost_usd"),
        "questions": settings.get("questions"),
        "created": str(meta.get("created") or ""),
    }


def gallery(records_dir: Path) -> list[dict]:
    if not records_dir.is_dir():
        return []
    cards = [gallery_card(d) for d in records_dir.iterdir() if d.is_dir()]
    return sorted((c for c in cards if c), key=lambda c: (c["created"], c["id"]), reverse=True)


# --- One build ------------------------------------------------------------------


@dataclass
class Job:
    id: str
    request: str
    sets: list[str]
    settings: dict = field(default_factory=lambda: dict(DEFAULT_SETTINGS))
    started: float = field(default_factory=time.time)
    state: str = "building"  # building | asking | done | failed
    questions: list[dict] | None = None  # shown on the page while state is "asking"
    answers: dict | None = None
    answered: threading.Event = field(default_factory=threading.Event)
    error: str = ""
    result: dict | None = None
    output: bytes = b""  # the builder's output with secrets removed; memory only, never served

    def public(self) -> dict:
        out = {
            "id": self.id,
            "state": self.state,
            "request": self.request,
            "settings": self.settings,
            "seconds": int(time.time() - self.started),
        }
        if self.state == "asking" and self.questions:
            out["questions"] = self.questions
        if self.error:
            out["error"] = self.error
        if self.result:
            out["result"] = self.result
        return out


class Site:
    def __init__(
        self,
        root: Path,
        *,
        records_dir: Path | None = None,
        runs_dir: Path | None = None,
        relay: Relay | None = None,
        inventory_for=None,
        rebrickable=None,
        max_cost: float = DEFAULT_SETTINGS["max_cost_usd"],
        environ=None,
    ):
        self.root = Path(root).resolve()
        self.records_dir = Path(records_dir or self.root / "records").resolve()
        self.runs_dir = Path(runs_dir or self.root / "runs" / "web").resolve()
        self.cache_dir = self.root / ".cache" / "rebrickable"
        self.relay = relay or Relay()
        self.defaults = {**DEFAULT_SETTINGS, "max_cost_usd": max_cost}
        self.environ = os.environ if environ is None else environ
        self._inventory_for = inventory_for or self._rebrickable_inventory
        self._rebrickable = rebrickable  # a module-like object in tests
        self.job: Job | None = None
        self._job_lock = threading.Lock()
        self._search_lock = threading.Lock()
        self._search_cache: dict[str, tuple[float, list[dict]]] = {}
        self._search_seq = 0
        self._last_call = 0.0
        self._images: dict[str, str] = {}  # set number -> picture address at Rebrickable

    # --- Rebrickable (server side only) ---------------------------------------

    def _rb(self):
        if self._rebrickable is not None:
            return self._rebrickable
        from . import rebrickable

        return rebrickable

    def _rb_key(self) -> str:
        rb = self._rb()
        try:
            return rb.read_api_key(self.root / ".env")
        except rb.RebrickableError as err:
            raise SiteError(str(err), 503) from None

    def _throttled(self, call):
        """Run one Rebrickable call, at least SEARCH_GAP after the one before."""
        wait = self._last_call + SEARCH_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            return call()
        finally:
            self._last_call = time.monotonic()

    def search(self, query: str) -> dict:
        query = " ".join(query.split())
        if len(query) < SEARCH_MIN:
            return {"query": query, "results": []}
        query = query[:SEARCH_MAX]
        cached = self._search_cache.get(query.lower())
        if cached and time.monotonic() - cached[0] < SEARCH_TTL:
            return {"query": query, "results": cached[1]}
        self._search_seq += 1
        mine = self._search_seq
        rb = self._rb()
        with self._search_lock:
            if mine != self._search_seq:  # a newer search arrived while this one waited
                return {"query": query, "results": [], "superseded": True}
            key = self._rb_key()
            try:
                rows = self._throttled(lambda: rb.search_sets(query, key))
            except rb.RebrickableError as err:
                raise SiteError(str(err), 502) from None
        results = []
        for row in rows:
            if row["image"]:
                self._images[row["set_num"]] = row["image"]
            results.append({**row, "image": f"/api/set-image/{row['set_num']}" if row["image"] else ""})
        self._search_cache[query.lower()] = (time.monotonic(), results)
        return {"query": query, "results": results}

    def set_image(self, set_num: str) -> bytes:
        if not SET_NUM.fullmatch(set_num):
            raise SiteError("not a set number", 404)
        cached = self.cache_dir / "img" / f"{set_num}.jpg"
        if cached.is_file():
            return cached.read_bytes()
        rb = self._rb()
        address = self._images.get(set_num)
        if not address:
            key = self._rb_key()
            with self._search_lock:
                try:
                    address = self._throttled(lambda: rb.fetch_set(set_num, key))["image"]
                except rb.RebrickableError as err:
                    raise SiteError(str(err), 502) from None
        parts = urlsplit(address or "")
        if parts.scheme != "https" or parts.hostname != IMAGE_HOST:
            raise SiteError("no picture for this set", 404)
        try:  # no key is sent: the picture host is public
            with urllib.request.urlopen(urllib.request.Request(address), timeout=20) as response:
                data = response.read(5 * 1024 * 1024)
        except OSError:
            raise SiteError("could not load the picture", 502) from None
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
        return data

    def _rebrickable_inventory(self, sets: list[str]) -> list[dict]:
        rb = self._rb()
        key = self._rb_key()
        try:
            built = rb.build_inventory(sets, key, cache_dir=self.cache_dir)
        except (rb.RebrickableError, ValueError) as err:
            raise SiteError(str(err), 502) from None
        return rb.inventory_entries(built.inventory)

    # --- Build ----------------------------------------------------------------

    def config(self) -> dict:
        return {
            "models": list(MODELS),
            "efforts": list(EFFORTS),
            "defaults": self.defaults,
            "min_cost_usd": MIN_COST,
            "max_cost_usd": MAX_COST,
        }

    def answer(self, job_id: str, answers) -> dict:
        """The person's answers to the clarifying questions of the running build."""
        job = self.job
        if job is None or not secrets.compare_digest(job.id, job_id):
            raise SiteError("no such build", 404)
        if job.state != "asking" or job.answered.is_set():
            raise SiteError("This build is not waiting for answers.", 409)
        if not isinstance(answers, dict):
            raise SiteError("send JSON", 400)
        known = {q["id"] for q in job.questions or []}
        job.answers = {k: str(v)[:100] for k, v in answers.items() if k in known and isinstance(v, str)}
        job.answered.set()
        return job.public()

    def start_build(self, key: Secret, request: str, sets: list, settings=None) -> Job:
        request = " ".join(str(request or "").split())
        if not key.reveal() or len(key.reveal()) > 400 or any(c.isspace() for c in key.reveal()):
            raise SiteError("Paste your Anthropic key first.")
        if not request:
            raise SiteError("Say what to build.")
        if len(request) > MAX_REQUEST_CHARS:
            raise SiteError(f"Keep the request under {MAX_REQUEST_CHARS} characters.")
        if not isinstance(sets, list) or not sets:
            raise SiteError("Add at least one set to My sets.")
        if len(sets) > MAX_SETS or not all(isinstance(s, str) and SET_NUM.fullmatch(s) for s in sets):
            raise SiteError(f"My sets can hold up to {MAX_SETS} set numbers.")
        settings = clean_settings(settings, self.defaults)
        with self._job_lock:
            if self.job is not None and self.job.state in ("building", "asking"):
                raise SiteError("A build is already running. Wait for it to finish.", 409)
            job = Job(id=secrets.token_hex(8), request=request, sets=list(sets), settings=settings)
            self.job = job
        threading.Thread(target=self._run, args=(job, key), daemon=True).start()
        return job

    def _run(self, job: Job, key: Secret) -> None:
        token = secrets.token_urlsafe(32)
        job_dir = self.runs_dir / job.id
        output = b""
        outcome: dict = {}
        error = ""
        try:
            entries = self._inventory_for(job.sets)
            if not entries:
                raise SiteError("These sets have no parts Voxel can build with. Add another set.")
            job_dir.mkdir(parents=True, exist_ok=True)
            inventory = job_dir / "inventory.json"
            inventory.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
            self.records_dir.mkdir(parents=True, exist_ok=True)
            # The relay gets its own holder, so closing the token clears it at once.
            # Its spending cap follows the max cost the person picked for this build.
            chosen = job.settings
            self.relay.open(token, Secret(key.reveal()), spend_cap(chosen["max_cost_usd"]))
            command = [
                sys.executable, "-m", "voxel.webjob",
                "--request", job.request,
                "--inventory", str(inventory),
                "--records-dir", str(self.records_dir),
                "--max-cost", str(chosen["max_cost_usd"]),
                "--model", MODELS[chosen["model"]],
                "--effort", chosen["effort"],
            ]  # fmt: skip
            if chosen["questions"]:
                command.append("--questions")
            proc = subprocess.Popen(
                command,
                cwd=self.root,
                env=child_env(self._env_with_ldraw(), self.relay.url, token, self.records_dir),
                # Answers to the questions are the only thing ever written to the builder.
                stdin=subprocess.PIPE if chosen["questions"] else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            timed_out = threading.Event()
            watchdog = threading.Timer(JOB_TIMEOUT, lambda: (timed_out.set(), proc.kill()))
            watchdog.daemon = True
            watchdog.start()
            try:
                output = self._watch(job, proc)
            finally:
                watchdog.cancel()
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
            if timed_out.is_set():
                error = "The build took too long and was stopped."
            for line in output.decode("utf-8", "replace").splitlines():
                if line.startswith(RESULT_MARK):
                    try:
                        outcome = json.loads(line[len(RESULT_MARK):])
                    except ValueError:
                        outcome = {}
            if not outcome and not error:
                error = "The build stopped before it finished."
        except SiteError as err:
            error = str(err)
        except Exception:
            error = "The build could not start."
        finally:
            self.relay.close(token)  # the relay forgets the key and the token
            leaked = self._remove_secrets(job, key, job_dir, outcome, output)
            key.clear()
            del token
        if leaked:
            job.result = None
            job.error = (
                "A key was found in the build's files or output. It was removed and this build was "
                "discarded. Replace that key with a new one."
            )
            job.state = "failed"
            return
        job.result = self._result(outcome) if outcome else None
        if error or not outcome.get("ok"):
            job.error = error or self._why_failed(outcome)
            job.state = "failed"
        else:
            job.state = "done"

    def _watch(self, job: Job, proc) -> bytes:
        """Read the builder's output until it ends. When it asks its questions, show
        them on the page, wait for the answers and write them to the builder."""
        chunks: list[bytes] = []
        asked = False
        for raw in proc.stdout:
            chunks.append(raw)
            if asked or proc.stdin is None or not raw.startswith(QUESTIONS_MARK.encode()):
                continue
            asked = True
            try:
                questions = clean_questions(json.loads(raw[len(QUESTIONS_MARK):]))
            except ValueError:
                questions = []
            answers: dict = {}
            if questions:
                job.questions = questions
                job.state = "asking"
                job.answered.wait(ANSWER_TIMEOUT)  # no answer in time: the defaults
                answers = job.answers or {}
                job.answered.set()
                job.state = "building"
                job.questions = None
            try:
                proc.stdin.write((json.dumps(answers) + "\n").encode())
                proc.stdin.close()
            except OSError:
                pass
        if proc.stdin is not None and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except OSError:
                pass
        return b"".join(chunks)

    def _env_with_ldraw(self) -> dict[str, str]:
        env = dict(self.environ)
        if not env.get("LDRAW_LIBRARY_PATH"):
            from .serve import find_ldraw

            found = find_ldraw()
            if found is not None:
                env["LDRAW_LIBRARY_PATH"] = str(found)
        return env

    def _remove_secrets(self, job: Job, key: Secret, job_dir: Path, outcome: dict, output: bytes) -> bool:
        """Search everything the build wrote for the Anthropic and Rebrickable keys.
        True if one was found (it has been removed)."""
        found = [key.reveal()]
        try:
            found.append(self._rb().read_api_key(self.root / ".env"))
        except Exception:
            pass
        found = [s for s in found if s]
        folders = [job_dir]
        record_id = str(outcome.get("record_id") or "")
        if RECORD_ID.fullmatch(record_id):
            folders.append(self.records_dir / record_id)
        leaked = bool(redact_tree(folders, found))
        for secret in found:
            if secret.encode() in output:
                leaked = True
                output = output.replace(secret.encode(), REDACTED.encode())
            if secret in json.dumps(outcome):
                leaked = True
                outcome.clear()
        job.output = output[-20000:]
        return leaked

    def _why_failed(self, outcome: dict) -> str:
        if outcome.get("error"):
            return str(outcome["error"])
        if outcome.get("stopped"):
            return f"The build stopped: {outcome['stopped']}"
        if outcome.get("budget_note"):
            return "The build reached its cost limit before a model passed the checks."
        return "No model passed the checks. Try a simpler request or add more sets."

    def _result(self, outcome: dict) -> dict:
        record_id = str(outcome.get("record_id") or "")
        record = self.records_dir / record_id if RECORD_ID.fullmatch(record_id) else None
        out = {
            "cost_usd": outcome.get("cost_usd"),
            "max_cost_usd": outcome.get("max_cost_usd"),
            "parts": outcome.get("parts"),
            "pages": outcome.get("pages"),
        }
        if record is None or not outcome.get("ok"):
            return out
        out["record_id"] = record_id
        if record_picture(record) is not None:
            out["picture"] = f"/records/{record_id}/picture"
        if (record / "out" / STEPPED_FILE).is_file():
            out["manual"] = f"/records/{record_id}/manual.ldr"
        review = review_summary(record)
        # One plain line: the note's details (after its first sentence) stay on the server.
        note = str(outcome.get("review_note") or "").split("\n")[0].split(". ")[0].strip().rstrip(".")
        out["review"] = review or {"skipped": (note or "The review did not run") + "."}
        return out

    def job_status(self, job_id: str) -> dict:
        job = self.job
        if job is None or not secrets.compare_digest(job.id, job_id):
            raise SiteError("no such build", 404)
        return job.public()

    # --- Files from records ---------------------------------------------------

    def record_dir(self, record_id: str) -> Path:
        if not RECORD_ID.fullmatch(record_id):
            raise SiteError("no such build", 404)
        record = (self.records_dir / record_id).resolve()
        if record.parent != self.records_dir or not record.is_dir():
            raise SiteError("no such build", 404)
        return record


# --- HTTP ----------------------------------------------------------------------


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # no tracebacks on the terminal
        pass


def make_handler(site: Site, port: int):
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # nothing is logged: no paths, headers or bodies
            pass

        def log_error(self, *args):
            pass

        def _send(self, code: int, body: bytes, kind: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data):
            self._send(code, json.dumps(data).encode(), "application/json")

        def _local(self) -> bool:
            """Only this site's own pages may call it (stops other websites and
            DNS tricks from reaching the server through the browser)."""
            if (self.headers.get("Host") or "").strip().lower() not in hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin.strip().lower() in origins

        def _guarded(self, work):
            if not self._local():
                return self._json(403, {"error": "This site only answers on localhost."})
            try:
                work()
            except SiteError as err:
                self._json(err.status, {"error": str(err)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self._json(500, {"error": "Something went wrong on the server."})

        def do_GET(self):
            self._guarded(self._get)

        def do_POST(self):
            self._guarded(self._post)

        def _get(self):
            route, _, query = self.path.partition("?")
            route = unquote(route)
            if route in PAGES:
                return self._send(200, (WEB / PAGES[route]).read_bytes(), "text/html; charset=utf-8")
            if route.startswith("/static/"):
                name = route[len("/static/"):]
                target = (WEB / name).resolve()
                kind = STATIC.get(target.suffix)
                if kind and WEB.resolve() in target.parents and target.is_file():
                    return self._send(200, target.read_bytes(), kind)
                raise SiteError("not found", 404)
            if route == "/api/sets":
                q = (parse_qs(query).get("q") or [""])[0]
                return self._json(200, site.search(q))
            if route.startswith("/api/set-image/"):
                return self._send(200, site.set_image(route[len("/api/set-image/"):]), "image/jpeg")
            if route == "/api/config":
                return self._json(200, site.config())
            if route.startswith("/api/build/"):
                return self._json(200, site.job_status(route[len("/api/build/"):]))
            if route == "/api/gallery":
                return self._json(200, {"builds": gallery(site.records_dir)})
            if route.startswith("/records/"):
                record_id, _, what = route[len("/records/"):].partition("/")
                record = site.record_dir(record_id)
                if what == "picture":
                    picture = record_picture(record)
                    if picture is not None:
                        return self._send(200, picture.read_bytes(), "image/png")
                if what == "manual.ldr":
                    manual = record / "out" / STEPPED_FILE
                    if manual.is_file():
                        name = f'attachment; filename="{record_id}.ldr"'
                        return self._send(200, manual.read_bytes(), "text/plain; charset=utf-8", {"Content-Disposition": name})
            raise SiteError("not found", 404)

        def _post(self):
            route = self.path
            answering = re.fullmatch(r"/api/build/([0-9a-f]+)/answers", route)
            if route != "/api/build" and not answering:
                raise SiteError("not found", 404)
            # A page on another site cannot send JSON here without being refused.
            if self.headers.get("Origin") is None:
                raise SiteError("This site only answers on localhost.", 403)
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                raise SiteError("send JSON", 415)
            try:
                length = int(self.headers.get("Content-Length") or "")
            except ValueError:
                raise SiteError("Content-Length is required", 411) from None
            if length < 0 or length > MAX_POST:
                raise SiteError("request too large", 413)
            try:
                data = json.loads(self.rfile.read(length))
            except ValueError:
                data = None
            if not isinstance(data, dict):
                raise SiteError("send JSON", 400)
            if answering:
                return self._json(200, site.answer(answering.group(1), data.get("answers")))
            key = Secret(str(data.pop("key", "") or "").strip())
            job = site.start_build(key, data.get("request"), data.get("sets"), data.get("settings"))
            self._json(202, job.public())

    return Handler


def run_site(port: int = 8765, open_browser: bool = True, root: Path | None = None) -> int:
    root = Path(root or Path.cwd())
    problem = anthropic_key_problem(root)
    if problem:
        print(f"Error: {problem}", file=sys.stderr)
        print("The site takes the Anthropic key from the page, for one build at a time.", file=sys.stderr)
        return 1
    protect_memory()
    site = Site(root)
    server = _Server(("127.0.0.1", port), make_handler(site, port))
    url = f"http://127.0.0.1:{port}/"
    print(f"Voxel is at {url}   (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        site.relay.shutdown()
    return 0
