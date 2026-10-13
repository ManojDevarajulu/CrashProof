import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import Field, TypeAdapter, ValidationError

from crashproof.agent.base import Action, AgentError, Done, Observation, Wait
from crashproof.models import Json, ReturnRequest
from crashproof.runtime.tools import ToolCall, ToolModel, ToolName, ToolResult

LLM_MODEL_ID = "gemini-2.0-flash"
MAX_RECORD_SEEDS = 50
MAX_MODEL_CALLS = 600
MAX_AGENT_STEPS = 30


type EndReason = Literal["finished", "ended_by_malformed_output", "ended_by_step_cap"]


class CassetteMiss(AgentError):
    pass


class ModelCallError(Exception):
    pass


class CallCapReached(Exception):
    pass


class ModelClient(Protocol):
    def generate(self, prompt: str) -> str: ...


class _ToolAction(ToolModel):
    action: Literal["tool"]
    tool: ToolName
    args: dict[str, Json]


class _WaitAction(ToolModel):
    action: Literal["wait"]


class _DoneAction(ToolModel):
    action: Literal["done"]


_ACTION = TypeAdapter(
    Annotated[_ToolAction | _WaitAction | _DoneAction, Field(discriminator="action")]
)

_INSTRUCTIONS = """\
You handle one customer return request for an online store. Decide the next step.
A normal return is: look up the order, check eligibility, create a return label,
wait for the returned package to be scanned, issue the refund for the total price
of the returned lines, then close the case.

Tools (arguments in braces):
- get_order {order_id}
- check_policy {order_id}
- create_return_label {order_id}
- issue_refund {order_id, amount}  (amount is an integer in minor currency units)
- escalate_to_human {reason}
- close_case {}

Reply with exactly one JSON object and nothing else, one of:
{"action":"tool","tool":"<tool name>","args":{...}}
{"action":"wait"}  (wait for an outside event before the next step)
{"action":"done"}  (nothing more to do)
"""


def _dump(value: Json) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _describe_call(call: ToolCall, result: ToolResult | None) -> str:
    head = f"called {call.tool} {_dump(call.args)} -> "
    if result is None:
        return head + "timed out, result unknown"
    if result.ok:
        return head + f"ok {_dump(result.data)}"
    return head + f"error {result.error}"


def _yes_no(flag: bool) -> str:
    return "yes" if flag else "no"


def build_prompt(request: ReturnRequest, history: list[str], obs: Observation) -> str:
    """The prompt depends only on the request, the agent's own history and the
    observation, so it is byte-identical under every runtime."""
    lines = [
        _INSTRUCTIONS,
        "Return request:",
        f"- order_id: {request.order_id}",
        f"- returned line skus: {', '.join(request.line_skus)}",
        f"- reason: {request.reason}",
        "",
        "History so far:",
    ]
    lines += [f"{n}. {entry}" for n, entry in enumerate(history, start=1)] or ["(none)"]
    lines += [
        "",
        "Current status:",
        f"- returned package scanned: {_yes_no(obs.scan_received)}",
        f"- decision received: {_yes_no(obs.approval_decided)}",
        f"- last call timed out: {_yes_no(obs.timed_out)}",
        "",
        "Next step:",
    ]
    return "\n".join(lines) + "\n"


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def parse_action(raw: str) -> Action:
    """Validate raw model output. Raises ValidationError on anything malformed."""
    parsed = _ACTION.validate_json(raw)
    if isinstance(parsed, _ToolAction):
        return ToolCall(tool=parsed.tool, args=parsed.args)
    if isinstance(parsed, _WaitAction):
        return Wait()
    return Done()


class Cassette:
    """Recorded model outputs for one seed, keyed by (step, prompt_hash).

    The seed is part of the key through the file name. One cassette is shared by
    all runtimes, because the prompt never names the runtime.
    """

    def __init__(self, seed: int, model: str = LLM_MODEL_ID) -> None:
        self.seed = seed
        self.model = model
        self._entries: dict[tuple[int, str], str] = {}
        self.runs: dict[str, dict[str, str | int]] = {}

    def get(self, step: int, digest: str) -> str | None:
        return self._entries.get((step, digest))

    def put(self, step: int, digest: str, raw_output: str) -> None:
        self._entries[(step, digest)] = raw_output

    def __len__(self) -> int:
        return len(self._entries)

    def to_json(self) -> str:
        entries = [
            {"step": step, "prompt_hash": digest, "raw_output": raw}
            for (step, digest), raw in sorted(self._entries.items())
        ]
        body = {
            "model": self.model,
            "seed": self.seed,
            "entries": entries,
            "runs": self.runs,
        }
        return json.dumps(body, sort_keys=True, indent=2) + "\n"

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / cassette_name(self.seed)
        path.write_bytes(self.to_json().encode("utf-8"))
        return path

    @classmethod
    def load(cls, path: Path) -> "Cassette":
        return cls.load_from_text(path.read_text(encoding="utf-8"))

    @classmethod
    def load_from_text(cls, text: str) -> "Cassette":
        body = json.loads(text)
        cassette = cls(int(body["seed"]), str(body["model"]))
        cassette.runs = body.get("runs", {})
        for entry in body["entries"]:
            cassette.put(
                int(entry["step"]), str(entry["prompt_hash"]), str(entry["raw_output"])
            )
        return cassette


def cassette_name(seed: int) -> str:
    return f"seed-{seed:04d}.json"


def cassette_paths(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(directory.glob("seed-*.json"))


class LLMAgent:
    """Asks a model (or a cassette) for one action per observation.

    With a client, a cassette miss calls the model and records the raw output.
    Without one, a miss raises CassetteMiss. Output that fails validation is a
    failed step: the agent ends with Done, and the raw output stays in the cassette.
    """

    def __init__(
        self,
        request: ReturnRequest,
        cassette: Cassette,
        client: ModelClient | None = None,
    ) -> None:
        self._request = request
        self._cassette = cassette
        self._client = client
        self._history: list[str] = []
        self._pending: ToolCall | None = None
        self._waited = False
        self._step = 0
        self._ended = False
        self.failed_steps: list[int] = []
        self.end_reason: EndReason = "finished"

    def next_action(self, obs: Observation) -> Action:
        if self._ended:
            return Done()
        self._note_previous(obs)
        if self._step >= MAX_AGENT_STEPS:
            return self._fail()
        prompt = build_prompt(self._request, self._history, obs)
        raw = self._raw_output(prompt)
        step = self._step
        self._step += 1
        try:
            action = parse_action(raw)
        except ValidationError:
            self.failed_steps.append(step)
            self.end_reason = "ended_by_malformed_output"
            return self._end()
        if isinstance(action, ToolCall):
            self._pending = action
        elif isinstance(action, Wait):
            self._waited = True
        else:
            self._ended = True
        return action

    @property
    def steps(self) -> int:
        return self._step

    def _note_previous(self, obs: Observation) -> None:
        if self._pending is not None:
            result = None if obs.timed_out else obs.last_result
            self._history.append(_describe_call(self._pending, result))
        elif self._waited:
            self._history.append("waited")
        self._pending = None
        self._waited = False

    def _raw_output(self, prompt: str) -> str:
        digest = prompt_hash(prompt)
        recorded = self._cassette.get(self._step, digest)
        if recorded is not None:
            return recorded
        if self._client is None:
            raise CassetteMiss(
                f"no cassette entry for seed {self._cassette.seed} "
                f"step {self._step} prompt {digest[:12]}"
            )
        raw = self._client.generate(prompt)
        self._cassette.put(self._step, digest, raw)
        return raw

    def _fail(self) -> Done:
        self.failed_steps.append(self._step)
        self.end_reason = "ended_by_step_cap"
        return self._end()

    def _end(self) -> Done:
        self._ended = True
        return Done()


def summarize_run(agents: list["LLMAgent"]) -> dict[str, str | int]:
    """How one runtime's run ended, over every agent it started. Malformed output
    outranks the step cap, which outranks finished. ended_by_call_budget is never
    stored: a seed that hits the call budget is discarded, not saved."""
    reasons = {agent.end_reason for agent in agents}
    ended: EndReason = "finished"
    if "ended_by_step_cap" in reasons:
        ended = "ended_by_step_cap"
    if "ended_by_malformed_output" in reasons:
        ended = "ended_by_malformed_output"
    return {"ended": ended, "steps": sum(agent.steps for agent in agents)}


class CallBudget:
    def __init__(self, limit: int = MAX_MODEL_CALLS) -> None:
        self.limit = limit
        self.calls = 0
        self.prompt_tokens = 0
        self.output_tokens = 0

    def charge(self) -> None:
        if self.calls >= self.limit:
            raise CallCapReached(f"model call cap of {self.limit} reached")
        self.calls += 1


class GeminiClient:
    """Gemini via Vertex AI. Used only by `make record`, never by tests or eval.

    The SDK is imported lazily and project, location and model come from the
    environment. Errors are re-raised without the SDK message, which can contain
    the project id.
    """

    def __init__(
        self, budget: CallBudget, env: Mapping[str, str] | None = None
    ) -> None:
        values = os.environ if env is None else env
        project = self._required(values, "GCP_PROJECT")
        location = self._required(values, "GCP_LOCATION")
        self._model = self._required(values, "GEMINI_MODEL")
        if self._model != LLM_MODEL_ID:
            raise ModelCallError(
                f"GEMINI_MODEL must be {LLM_MODEL_ID} (frozen in DECISIONS.md)"
            )
        self._budget = budget
        self._genai, self._types = _load_sdk()
        self._client = self._genai.Client(
            vertexai=True, project=project, location=location
        )

    @staticmethod
    def _required(values: Mapping[str, str], name: str) -> str:
        value = values.get(name)
        if not value:
            raise ModelCallError(f"environment variable {name} is not set")
        return value

    def generate(self, prompt: str) -> str:
        self._budget.charge()
        config = self._types.GenerateContentConfig(
            temperature=0, response_mime_type="application/json"
        )
        try:
            response = self._client.models.generate_content(
                model=self._model, contents=prompt, config=config
            )
        except Exception as exc:
            raise ModelCallError(f"model call failed: {type(exc).__name__}") from None
        usage = response.usage_metadata
        if usage is not None:
            self._budget.prompt_tokens += usage.prompt_token_count or 0
            self._budget.output_tokens += usage.candidates_token_count or 0
        text = response.text
        if text is None:
            raise ModelCallError("model returned no text")
        return str(text)


def _load_sdk() -> tuple[Any, Any]:
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise ModelCallError(
            'google-genai is not installed; run: pip install -e ".[llm]"'
        ) from None
    return genai, types
