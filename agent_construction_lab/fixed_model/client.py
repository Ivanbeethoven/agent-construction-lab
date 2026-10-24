"""Audited, fixed-configuration chat completions over the real HTTP protocol.

Unknown provider usage remains unknown. A request seed is a recorded request
parameter, never a claim that a hosted model is deterministic.
"""
from __future__ import annotations

import copy
import ctypes
from dataclasses import asdict, dataclass
import http.client
import json
import math
import os
from pathlib import Path
import socket
import threading
import time
from urllib import error, parse, request
import uuid

from agent_construction_lab._io import append_json


class _NativeClock:
    """OS clocks bypass AppWorld/freezegun's monkeypatched Python clocks."""

    def __init__(self):
        if os.name == "nt":
            self.backend = "windows_query_performance_counter_and_filetime"
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.counter = kernel.QueryPerformanceCounter
            self.counter.argtypes = [ctypes.POINTER(ctypes.c_longlong)]
            self.counter.restype = ctypes.c_int
            frequency = ctypes.c_longlong()
            kernel.QueryPerformanceFrequency(ctypes.byref(frequency))
            self.frequency = frequency.value
            self.system = getattr(kernel, "GetSystemTimePreciseAsFileTime", kernel.GetSystemTimeAsFileTime)
            self.system.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
            self.system.restype = None
        else:
            self.backend = "posix_clock_gettime"

            class Timespec(ctypes.Structure):
                _fields_ = [("seconds", ctypes.c_long), ("nanoseconds", ctypes.c_long)]

            self.timespec = Timespec
            self.clock_gettime = ctypes.CDLL(None, use_errno=True).clock_gettime
            self.clock_gettime.argtypes = [ctypes.c_int, ctypes.POINTER(Timespec)]
            self.clock_gettime.restype = ctypes.c_int

    def _posix(self, clock_id):
        result = self.timespec()
        if self.clock_gettime(clock_id, ctypes.byref(result)):
            raise OSError(ctypes.get_errno(), "Native clock_gettime failed")
        return result.seconds + result.nanoseconds / 1_000_000_000

    def monotonic(self):
        if os.name != "nt":
            return self._posix(time.CLOCK_MONOTONIC)
        result = ctypes.c_longlong()
        if not self.counter(ctypes.byref(result)):
            raise OSError("QueryPerformanceCounter failed")
        return result.value / self.frequency

    def timestamp(self):
        if os.name == "nt":
            result = ctypes.c_ulonglong()
            self.system(ctypes.byref(result))
            seconds = (result.value - 116444736000000000) / 10_000_000
        else:
            seconds = self._posix(time.CLOCK_REALTIME)
        whole = math.floor(seconds)
        # An explicit timestamp avoids freezegun's default-time replacement.
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(whole)) + f".{int((seconds - whole) * 1_000_000):06d}+00:00"


REAL_CLOCK = _NativeClock()
CLOCK_SOURCE = "os_clock_gettime"  # Retained ledger schema; clock_backend names the actual OS API.


def real_monotonic():
    """Compatibility entry point for adapter-validation timing."""
    return REAL_CLOCK.monotonic()


def real_timestamp_utc():
    """UTC timestamp from the OS clock, independent of benchmark time freezing."""
    return REAL_CLOCK.timestamp()


@dataclass(frozen=True)
class ModelConfig:
    model: str
    model_snapshot: str | None = None
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str | None = "OPENAI_API_KEY"
    expected_response_model: str | None = None
    timeout: float = 60.0
    retries: int = 0
    temperature: float | None = 0
    top_p: float | None = None
    reasoning_effort: str | None = None
    max_output_tokens: int = 1024
    max_tokens_parameter: str = "max_tokens"
    request_seed: int | None = None
    send_seed: bool = True
    use_tools: bool = True
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    cached_input_price_per_million: float | None = None
    pricing_version: str | None = None
    max_model_calls: int | None = None
    max_total_tokens: int | None = None
    request_token_reserve: int | None = None
    actor_protocol: str = "v1"
    actor_protocol_max_repairs: int = 1

    def __post_init__(self):
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("A fixed model identifier is required")
        endpoint = parse.urlsplit(self.base_url)
        if endpoint.scheme not in {"http", "https"} or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError("base_url must be an HTTP(S) URL without credentials, query or fragment")
        if type(self.retries) is not int or self.retries < 0:
            raise ValueError("retries must be a nonnegative integer")
        if not isinstance(self.timeout, (int, float)) or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if type(self.max_output_tokens) is not int or self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if self.max_tokens_parameter not in {"max_tokens", "max_completion_tokens"}:
            raise ValueError("Unsupported token limit parameter")
        for name in ("input_price_per_million", "output_price_per_million", "cached_input_price_per_million"):
            value = getattr(self, name)
            if value is not None and (type(value) not in {int, float} or not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be a finite nonnegative rate")
        if self.request_seed is not None and type(self.request_seed) is not int:
            raise ValueError("request_seed must be an integer or None")
        for name in ("max_model_calls", "max_total_tokens", "request_token_reserve"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"{name} must be a positive integer or None")
        if self.max_total_tokens is not None and self.request_token_reserve is None:
            raise ValueError("A token limit requires an explicit per-request token upper bound")
        if self.request_token_reserve is not None and self.request_token_reserve < self.max_output_tokens:
            raise ValueError("request_token_reserve must cover the maximum output tokens")
        if not isinstance(self.actor_protocol, str) or self.actor_protocol not in {"v1", "v2_required_tools"}:
            raise ValueError("Unknown actor protocol")
        if type(self.actor_protocol_max_repairs) is not int or not 0 <= self.actor_protocol_max_repairs <= 1:
            raise ValueError("actor_protocol_max_repairs must be zero or one")
        if self.actor_protocol == "v2_required_tools":
            if not self.use_tools:
                raise ValueError("The v2 actor protocol requires function tools")
            # DeepSeek Chat Completions rejects required tools in thinking mode.
            # Official API: reasoning_effort=none explicitly disables thinking.
            if parse.urlsplit(self.base_url).hostname == "api.deepseek.com" and self.reasoning_effort != "none":
                raise ValueError("DeepSeek required tools need reasoning_effort='none'")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(**value)


TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_tokens")


def aggregate_usage(rows):
    """Sum known values while exposing any gaps in the aggregate totals."""
    rows = list(rows)
    totals = {"attempts": len(rows), "model_calls": sum(bool(row.get("request_sent")) for row in rows),
              "failed_attempts": sum(not row.get("success", False) for row in rows),
              "latency_seconds": sum(row.get("latency_seconds", 0) or 0 for row in rows)}
    for key in (*TOKEN_FIELDS, "cost_usd"):
        known = sum(row[key] for row in rows if row.get(key) is not None)
        missing = sum(row.get(key) is None for row in rows)
        totals["known_" + key] = known
        totals["unknown_" + key + "_attempts"] = missing
        totals[key] = None if missing else known
    return totals


class UsageLedger:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else None
        self.entries = []
        self._lock = threading.RLock()
        # Appending to an existing ledger must not reuse attempt identifiers.
        if self.path is not None and self.path.exists():
            self.entries = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def record(self, entry):
        with self._lock:
            row = copy.deepcopy(entry)
            row["attempt_id"] = f"attempt-{len(self.entries) + 1:08d}"
            if self.path is not None:
                append_json(self.path, row)
            self.entries.append(row)
            return row["attempt_id"]

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self.entries)

    def totals(self, rows=None):
        return aggregate_usage(self.snapshot() if rows is None else rows)

    def budget_status(self, config):
        rows = [row for row in self.snapshot() if row.get("request_sent")]
        unknown = [row for row in rows if row.get("total_tokens") is None]
        known = sum(row["total_tokens"] for row in rows if row.get("total_tokens") is not None)
        return {"max_model_calls": config.max_model_calls, "max_total_tokens": config.max_total_tokens,
                "request_token_reserve": config.request_token_reserve, "model_calls": len(rows),
                "known_total_tokens": known, "unknown_usage_attempts": len(unknown),
                "conservatively_reserved_tokens": known + sum(row.get("request_token_reserve") or 0 for row in unknown)}


class ModelCallError(RuntimeError):
    def __init__(self, error_type, message=None, *, attempt_ids=(), http_status=None):
        self.error_type = error_type
        self.attempt_ids = list(attempt_ids)
        self.http_status = http_status
        super().__init__(message or error_type)


@dataclass
class Completion:
    content: str
    tool_calls: list
    model: str | None
    usage: dict
    attempt_ids: list
    finish_reason: str | None = None


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _usage(payload, config):
    usage = payload.get("usage") if isinstance(payload, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    inputs, outputs, total = (_count(usage.get(name)) for name in ("prompt_tokens", "completion_tokens", "total_tokens"))
    input_details = usage.get("prompt_tokens_details") or {}
    output_details = usage.get("completion_tokens_details") or {}
    cached = _count(input_details.get("cached_tokens")) if isinstance(input_details, dict) and inputs is not None else None
    reasoning = _count(output_details.get("reasoning_tokens")) if isinstance(output_details, dict) and outputs is not None else None
    violations = []
    cache_source = "prompt_tokens_details.cached_tokens" if cached is not None else "unknown"
    if "prompt_cache_hit_tokens" in usage:
        hit = _count(usage["prompt_cache_hit_tokens"])
        if hit is None or cached is not None and hit != cached:
            violations.append("invalid_or_conflicting_cache_hit_tokens")
        else:
            cached, cache_source = hit, "prompt_cache_hit_tokens"
    miss = _count(usage.get("prompt_cache_miss_tokens"))
    if "prompt_cache_miss_tokens" in usage:
        if miss is None or inputs is not None and cached is not None and inputs != cached + miss:
            violations.append("invalid_or_conflicting_cache_miss_tokens")
    if cached is not None and inputs is not None and cached > inputs:
        violations.append("cached_tokens_exceed_input")
    if reasoning is not None and outputs is not None and reasoning > outputs:
        violations.append("reasoning_tokens_exceed_output")
    if inputs is not None and outputs is not None and total is not None and total != inputs + outputs:
        violations.append("total_tokens_disagree_with_parts")
    source = "provider" if total is not None else "unknown"
    if total is None and inputs is not None and outputs is not None:
        total, source = inputs + outputs, "sum_of_input_and_output"
    cost = None
    if not violations and inputs is not None and outputs is not None and config.input_price_per_million is not None and config.output_price_per_million is not None:
        input_cost = inputs * config.input_price_per_million
        if config.cached_input_price_per_million is not None:
            if cached is None or cached > inputs:
                input_cost = None
            else:
                input_cost = (inputs - cached) * config.input_price_per_million + cached * config.cached_input_price_per_million
        if input_cost is not None:
            cost = (input_cost + outputs * config.output_price_per_million) / 1_000_000
    values = dict(input_tokens=inputs, output_tokens=outputs, total_tokens=total,
                  cached_input_tokens=cached, reasoning_tokens=reasoning, cost_usd=cost,
                  total_tokens_source=source, cached_input_tokens_source=cache_source,
                  uncached_input_tokens=miss if miss is not None else inputs-cached if inputs is not None and cached is not None else None,
                  usage_consistent=not violations, usage_errors=violations)
    values["usage_status"] = "complete" if all(values[key] is not None for key in TOKEN_FIELDS) else "partial" if usage else "unknown"
    return values


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenAICompatibleClient:
    def __init__(self, config, ledger=None):
        self.config = config if isinstance(config, ModelConfig) else ModelConfig.from_dict(config)
        self.ledger = ledger if ledger is not None else UsageLedger()
        # urllib otherwise follows redirects and can forward Authorization.
        self._opener = request.build_opener(_NoRedirect())

    def complete(self, messages, *, phase="execution", role="executor", domain=None,
                 task_id=None, seed=None, tools=None, tool_choice=None, max_attempts=None):
        # One ledger is one spending pool. Serialize its admissions, including
        # clients sharing the same ledger, so concurrent calls cannot overshoot.
        with self.ledger._lock:
            return self._complete(messages, phase=phase, role=role, domain=domain,
                                  task_id=task_id, seed=seed, tools=tools, tool_choice=tool_choice, max_attempts=max_attempts)

    def _budget_error(self, encoded):
        config = self.config
        state = self.ledger.budget_status(config)
        if config.max_model_calls is not None and state["model_calls"] >= config.max_model_calls:
            return "model_call_budget_exhausted"
        if config.max_total_tokens is not None:
            if state["unknown_usage_attempts"]:
                return "budget_usage_unknown"
            if state["known_total_tokens"] + config.request_token_reserve > config.max_total_tokens:
                return "token_budget_exhausted"
        # The caller must configure a documented model context-window bound.
        # This additional conservative byte check also stops oversized builder
        # prompts before dispatch; it is not an exact tokenizer claim.
        if config.request_token_reserve is not None and len(encoded) + config.max_output_tokens > config.request_token_reserve:
            return "request_token_reservation_exceeded"
        if any(row.get("error_type") == "token_reservation_exceeded" for row in self.ledger.snapshot()):
            return "token_reservation_exceeded"
        return None

    def _complete(self, messages, *, phase="execution", role="executor", domain=None,
                  task_id=None, seed=None, tools=None, tool_choice=None, max_attempts=None):
        config = self.config
        if max_attempts is not None and (type(max_attempts) is not int or max_attempts < 1):
            raise ValueError("max_attempts must be a positive integer or None")
        attempt_limit = config.retries + 1 if max_attempts is None else min(config.retries + 1, max_attempts)
        seed = config.request_seed if seed is None else seed
        if seed is not None and type(seed) is not int:
            raise ValueError("seed must be an integer or None")
        body = {"model": config.model, "messages": messages, config.max_tokens_parameter: config.max_output_tokens}
        for field in ("temperature", "top_p", "reasoning_effort"):
            if getattr(config, field) is not None:
                body[field] = getattr(config, field)
        if config.send_seed and seed is not None:
            body["seed"] = seed
        if config.use_tools and tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice or "auto"
            body["parallel_tool_calls"] = False
        encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        call_id, attempt_ids = uuid.uuid4().hex, []
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        credential = os.environ.get(config.api_key_env, "") if config.api_key_env else None
        if credential:
            headers["Authorization"] = "Bearer " + credential

        for index in range(attempt_limit):
            started = REAL_CLOCK.monotonic()
            row = {"schema_version": 1, "call_id": call_id, "attempt_index": index + 1,
                   "timestamp_utc": REAL_CLOCK.timestamp(), "clock_source": CLOCK_SOURCE,
                   "clock_backend": REAL_CLOCK.backend, "phase": phase, "role": role, "domain": domain,
                   "task_id": task_id, "base_url": config.base_url, "request_model": config.model,
                   "declared_model_snapshot": config.model_snapshot,
                   "expected_response_model": config.expected_response_model,
                   "request_seed": seed, "seed_sent": "seed" in body, "seed_determinism_guaranteed": False,
                   "temperature": config.temperature, "top_p": config.top_p,
                   "reasoning_effort": config.reasoning_effort, "max_output_tokens": config.max_output_tokens,
                   "tool_choice": copy.deepcopy(body.get("tool_choice")), "max_attempts": max_attempts,
                   "request_sent": False, "success": False, "error_type": None, "http_status": None,
                   "retry_scheduled": False, "response_model": None, "provider_request_id": None,
                   "finish_reason": None, "pricing_version": config.pricing_version,
                   "cost_is_estimate": True,
                   "request_token_reserve": config.request_token_reserve,
                   "cost_basis": "configured token rates; does not include unpriced external charges"}
            row.update(_usage({}, config))
            payload, message, retryable = None, None, False
            if config.api_key_env and not credential:
                row["error_type"] = "missing_api_key"
            elif (budget_error := self._budget_error(encoded)) is not None:
                row["error_type"] = budget_error
                row.update({key: 0 for key in TOKEN_FIELDS})
                row.update(cost_usd=0.0, usage_status="not_sent", total_tokens_source="no_http_request",
                           budget_blocked=True, budget=self.ledger.budget_status(config))
            else:
                req = request.Request(config.base_url.rstrip("/") + "/chat/completions", data=encoded, headers=headers, method="POST")
                try:
                    row["request_sent"] = True
                    with self._opener.open(req, timeout=config.timeout) as result:
                        row["http_status"] = result.status
                        row["provider_request_id"] = result.headers.get("x-request-id")
                        raw = result.read()
                    try:
                        payload = json.loads(raw)
                    except (ValueError, UnicodeError):
                        row["error_type"] = "invalid_response_json"
                    if row["error_type"] is None:
                        row.update(_usage(payload, config))
                        if not row["usage_consistent"]:
                            row["error_type"] = "invalid_provider_usage"
                        elif not isinstance(payload, dict):
                            row["error_type"] = "invalid_response_schema"
                        else:
                            row["response_model"] = payload.get("model")
                            if config.expected_response_model and row["response_model"] != config.expected_response_model:
                                row["error_type"] = "response_model_identity_mismatch"
                            else:
                                choices = payload.get("choices")
                                choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
                                message = choice.get("message")
                                row["finish_reason"] = choice.get("finish_reason")
                                if row["finish_reason"] in {"insufficient_system_resource", "aborted"}:
                                    row["error_type"] = "provider_generation_interrupted"
                                elif (not isinstance(message, dict) or
                                        message.get("content") is not None and not isinstance(message["content"], str) or
                                        message.get("tool_calls") is not None and not isinstance(message["tool_calls"], list) or
                                        isinstance(message, dict) and message.get("content") is None and not message.get("tool_calls")):
                                    row["error_type"] = "invalid_response_schema"
                                else:
                                    row["success"] = True
                except error.HTTPError as exc:
                    row["http_status"] = exc.code
                    row["provider_request_id"] = exc.headers.get("x-request-id") if exc.headers else None
                    row["error_type"] = f"http_{exc.code}"
                    retryable = exc.code in {408, 429} or 500 <= exc.code < 600
                    try:
                        row.update(_usage(json.loads(exc.read()), config))
                    except (ValueError, UnicodeError, OSError):
                        pass
                    finally:
                        exc.close()
                except (error.URLError, TimeoutError, socket.timeout, OSError, http.client.HTTPException):
                    row["error_type"] = "transport_error"
                    retryable = True
            if row["request_sent"] and config.request_token_reserve is not None and row["total_tokens"] is not None and row["total_tokens"] > config.request_token_reserve:
                row["underlying_error_type"] = row["error_type"]
                row["success"], row["error_type"], retryable = False, "token_reservation_exceeded", False
            if row["request_sent"] and config.max_total_tokens is not None and row["total_tokens"] is None:
                # Even a successful response with missing usage makes further
                # spending unauditable. Preserve null usage and abort now.
                row["underlying_error_type"] = row["error_type"]
                row["success"], row["error_type"], retryable = False, "budget_usage_unknown", False
            row["retry_scheduled"] = retryable and index + 1 < attempt_limit
            row["latency_seconds"] = max(0.0, REAL_CLOCK.monotonic() - started)
            attempt_ids.append(self.ledger.record(row))
            if row["success"]:
                return Completion(content=message.get("content") or "", tool_calls=copy.deepcopy(message.get("tool_calls") or []),
                                  model=row["response_model"], usage={key: row[key] for key in (*TOKEN_FIELDS, "cost_usd", "usage_status", "total_tokens_source")},
                                  attempt_ids=attempt_ids, finish_reason=row["finish_reason"])
            if not row["retry_scheduled"]:
                raise ModelCallError(row["error_type"], attempt_ids=attempt_ids, http_status=row["http_status"])
            time.sleep(min(0.25 * 2 ** index, 4.0))
        raise AssertionError("Unreachable retry state")
