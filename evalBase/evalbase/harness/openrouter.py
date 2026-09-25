"""Host-managed OpenRouter agent loop: tool calling, compaction, resume.

This is a documented custom harness, not a claim of parity with the native
CLIs. Provider-reported usage is authoritative; a response without usable
`usage` stops the attempt as an accounting failure rather than spending
without a record. Stop policy is budget: a final answer without tool calls
is recorded and the loop prompts the model to keep working.
"""
from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from ..grader import jsonio
from ..interfaces import Instance
from . import attempt as att
from . import tools, workspace as ws
from .privacy import Redactor
from .provider import ENDPOINT, assistant_message, request, usage
from .tools import ToolSession, atomic_json, make_sandbox, tool_schemas


class Runner(ToolSession):
    def __init__(self, instance: Instance, options, run_dir, *, request_fn=None, resume=False):
        options.validate()
        if options.harness != "openrouter":
            raise ValueError("Runner is the openrouter harness")
        self.instance = instance
        self.options = options
        self.run_dir = Path(run_dir).resolve()
        self.request_fn = request_fn or request
        self.simulated = request_fn is not None
        self.key = "" if self.simulated else os.environ.get("OPENROUTER_API_KEY", "")
        if not self.simulated and not self.key:
            raise RuntimeError("OPENROUTER_API_KEY is missing. No container or paid request was started.")
        if not self.simulated and not options.max_cost_usd:
            raise RuntimeError("--max-cost-usd is required for a live openrouter attempt")
        self.redactor = Redactor(secrets=(self.key,), root=instance.root)
        self.workspace = self.run_dir / "workspace"
        self.tools = tool_schemas(instance.task.tool_descriptions)
        self.owner = self.run_dir.name
        self.sandbox = None
        self.base_elapsed = 0.0
        self.last_checkpoint = 0.0
        self.frozen_elapsed = None
        self.started = time.monotonic()
        if resume:
            self.state = json.loads((self.run_dir / "state.private.json").read_text())
            if self.state["options"] != asdict(options):
                raise ValueError("resume must preserve the original model, budgets and policy")
            if self.state["phase"] == "api_inflight":
                raise RuntimeError("the previous API call has an uncertain outcome and cost. "
                                   "Reconcile it with the provider before resuming; it was not retried.")
            if self.state["manifest"]["status"] == "complete":
                raise ValueError("completed attempts cannot be resumed as new trials")
            if self.state["manifest"]["simulated_model"] != self.simulated:
                raise ValueError("cannot change simulated/live provider on resume")
            if self.state["manifest"]["task_version"]["corpus"]["sha256"] != \
                    ws.corpus_digest(instance.corpus_public, instance.assets)["sha256"]:
                raise ValueError("the corpus changed; this is a different task version")
            self.base_elapsed = self.state["manifest"].get("solver_seconds", 0.0)
            self.last_checkpoint = self.base_elapsed
            if self.state["phase"] in ("exporting", "grading", "complete"):
                self.frozen_elapsed = self.base_elapsed
            if self.state["manifest"].get("stop_reason") == "operator_interrupt":
                self.state["manifest"]["stop_reason"] = None
            self.state["manifest"].pop("error", None)
            self.state["manifest"]["resume_count"] = self.state["manifest"].get("resume_count", 0) + 1
        else:
            self.run_dir.mkdir(parents=True, exist_ok=False)
            ws.populate(instance, self.workspace, stop_policy=options.stop_policy,
                        budget_seconds=options.budget_seconds,
                        checkpoint_seconds=options.checkpoint_seconds)
            manifest = att.base_manifest(instance, options, simulated=self.simulated, name=self.run_dir.name)
            manifest.update(
                endpoint=ENDPOINT, requests=0, resume_count=0,
                context_semantics=("explicit model-written handoff summaries at a serialized-size "
                                   f"threshold of {options.context_chars} characters"),
                cost_semantics="provider usage.cost, checked between requests; not a hard billing cap")
            manifest["usage"] = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
                                 "source": "provider usage field (authoritative)"}
            self.state = {"options": asdict(options), "phase": "ready", "messages": [
                {"role": "system", "content": instance.task.instructions + "\nStopping policy: budget."},
                {"role": "user", "content": "Read /task/TASK.md and /task/SPEC.md with the shell "
                                            "tool, then implement and test the task."}],
                "pending": [], "checkpoint_index": 0, "oracle_index": 0, "grade_index": 0,
                "checkpoints": [], "manifest": manifest}
        self.manifest.setdefault("infrastructure_incidents", [])
        self.manifest["owner"] = self.owner
        atomic_json(self.run_dir / "agent-config.json", {
            "instance": str(instance.root / "instance.py"),
            "harness": "openrouter", "options": asdict(options), "owner": self.owner,
            "sandbox": options.sandbox,
            "corpus_public": str(instance.corpus_public), "refcache": str(instance.refcache)}, mode=0o600)
        self.save()

    # ------------------------------------------------------------- plumbing
    @property
    def manifest(self):
        return self.state["manifest"]

    def elapsed(self):
        if self.frozen_elapsed is not None:
            return self.frozen_elapsed
        return self.base_elapsed + time.monotonic() - self.started

    def save(self):
        self.manifest["solver_seconds"] = round(self.elapsed(), 3)
        self.manifest["checkpoints"] = self.state.get("checkpoints", [])
        atomic_json(self.run_dir / "state.private.json", self.state, mode=0o600)
        atomic_json(self.run_dir / "attempt.json", self.redactor.clean(self.manifest), mode=0o644)

    def log(self, event, **data):
        with (self.run_dir / "transcript.jsonl").open("a") as f:
            f.write(self.redactor.dumps({"event": event, "elapsed": round(self.elapsed(), 3), **data},
                                        ensure_ascii=False) + "\n")

    # -------------------------------------------------------------- budgets
    def budget_stop(self):
        o, u = self.options, self.manifest["usage"]
        if self.elapsed() >= o.budget_seconds:
            return "budget_wall"
        if o.max_cost_usd and (u["cost_usd"] or 0) >= o.max_cost_usd:
            return "budget_cost"
        if o.max_tokens and (u["input_tokens"] or 0) + (u["output_tokens"] or 0) >= o.max_tokens:
            return "budget_tokens"
        if self.manifest["requests"] >= o.max_requests:
            return "budget_tokens"
        return None

    # ---------------------------------------------------------------- model
    def call_model(self, payload, kind="solve"):
        if self.budget_stop():
            raise RuntimeError("budget reached before the provider request")
        self.state["phase"] = "api_inflight"
        self.state["last_response_kind"] = kind
        self.state["last_response_accounted"] = False
        self.state.pop("last_response", None)
        self.save()
        self.log("request", kind=kind, payload=payload)
        timeout = max(1.0, min(900.0, self.options.budget_seconds - self.elapsed()))
        response = self.request_fn(payload, self.key, timeout)
        self.state["last_response"] = response
        self.state["phase"] = "response_received"
        self.save()
        self.account(response)
        self.log("response", kind=kind, response=response)
        return response

    def account(self, response):
        if self.state.get("last_response_accounted"):
            return
        prompt_tokens, completion_tokens, cost = usage(response)
        u = self.manifest["usage"]
        u["input_tokens"] += prompt_tokens
        u["output_tokens"] += completion_tokens
        u["cost_usd"] = round(u["cost_usd"] + cost, 8)
        self.manifest["requests"] += 1
        if response.get("model") and response["model"] not in self.manifest["models_served"]:
            self.manifest["models_served"].append(response["model"])
        provider = response.get("provider")
        if provider:
            self.manifest.setdefault("upstream_providers", [])
            if provider not in self.manifest["upstream_providers"]:
                self.manifest["upstream_providers"].append(provider)
        served = ((response.get("choices") or [{}])[0].get("message") or {}).get("reasoning_details")
        if served and self.manifest.get("reasoning_served") is None:
            effort = None
            if isinstance(served, list) and served and isinstance(served[0], dict):
                effort = served[0].get("effort")
            self.manifest["reasoning_served"] = effort or "reported (effort not stated)"
        self.state["last_response_accounted"] = True
        self.save()

    def payload(self):
        out = {"model": self.options.model, "messages": copy.deepcopy(self.state["messages"]),
               "tools": self.tools, "parallel_tool_calls": False, "stream": False,
               "max_tokens": self.options.max_output_tokens,
               "usage": {"include": True}}
        if self.options.reasoning:
            out["reasoning"] = {"effort": self.options.reasoning}
        return out

    # ------------------------------------------------------------ compaction
    def compact(self):
        self.checkpoint("Before context compaction")
        payload = self.payload()
        payload.pop("tools")
        payload.pop("parallel_tool_calls")
        payload["max_tokens"] = min(self.options.max_output_tokens, 8192)
        payload["messages"].append({"role": "user", "content":
            "Write a compact handoff for your next context. Preserve the architecture, exact "
            "filenames, the latest measured grade_dev results and defects, pending work, useful "
            "commands and decisions. The files remain available. Do not call tools."})
        self.consume_compaction(self.call_model(payload, "compaction"))

    def consume_compaction(self, response):
        message, _ = assistant_message(response)
        if message.get("tool_calls") or not isinstance(message.get("content"), str) or not message["content"].strip():
            raise RuntimeError("compaction did not produce a usable handoff; original context preserved")
        summary = message["content"]
        self.manifest["compaction_events"].append(
            {"at_seconds": round(self.elapsed(), 3), "request": self.manifest["requests"],
             "summary_chars": len(summary)})
        self.state["messages"] = [self.state["messages"][0], {"role": "user", "content":
            "Continue the same attempt. Re-read /task/NOTES.md and the current files. Prior "
            "context handoff:\n" + summary}]
        self.state["phase"] = "ready"
        self.state.pop("last_response", None)
        self.save()
        self.log("compaction", summary=summary, context_chars=len(json.dumps(self.state["messages"])))

    # ----------------------------------------------------------------- loop
    def consume_response(self, response):
        message, incomplete = assistant_message(response)
        self.state["messages"].append(message)
        self.state["pending"] = [{"call": c, "status": "queued"} for c in message.get("tool_calls") or []]
        self.state["phase"] = "tools"
        self.state.pop("last_response", None)
        self.save()
        if not self.state["pending"]:
            if not incomplete:
                self.manifest["model_final_events"] += 1
            self.state["messages"].append({"role": "user", "content":
                "The budget has not ended. Keep implementing, measuring with grade_dev and oracle, "
                "and improving; use tools rather than finishing."})

    def run_tools(self):
        for entry in self.state["pending"]:
            if entry["status"] == "done":
                continue
            if entry["status"] == "running":
                result = {"tool_error": "execution was interrupted; effects may already exist. "
                                        "Inspect the workspace before retrying."}
            elif self.manifest.get("stop_reason") or self.elapsed() >= self.options.budget_seconds:
                result = {"tool_error": "the stopping policy ended the attempt before this tool ran."}
            else:
                entry["status"] = "running"
                self.save()
                try:
                    tools.validate_arguments(entry["call"]["function"]["name"],
                                             json.loads(entry["call"]["function"]["arguments"] or "{}"))
                    name = entry["call"]["function"]["name"]
                    self.manifest["tool_calls"][name] = self.manifest["tool_calls"].get(name, 0) + 1
                    result = self.execute(entry["call"])
                except (ValueError, KeyError, TypeError, RuntimeError, OSError) as exc:
                    result = {"tool_error": self.redactor.text(exc)}
            entry["status"] = "done"
            entry["result"] = result
            self.state["messages"].append({"role": "tool", "tool_call_id": entry["call"]["id"],
                                           "content": jsonio.dumps(result)[:400000]})
            self.log("tool", call_id=entry["call"]["id"], name=entry["call"]["function"]["name"],
                     result=result)
            self.save()
            if self.elapsed() - self.last_checkpoint >= self.options.checkpoint_seconds:
                self.checkpoint("Automatic checkpoint")
        self.state["pending"] = []
        self.state["phase"] = "ready"
        self.save()

    def run(self):
        try:
            if self.state["phase"] in ("exporting", "grading"):
                return self.finish()
            self.sandbox = make_sandbox(self.options.sandbox, self.workspace,
                                        image=self.instance.solver_image,
                                        cpus=self.options.cpus, memory=self.options.memory,
                                        owner=self.owner, on_incident=self.record_incident)
            self.manifest["solver_image"] = self.sandbox.image
            self.manifest["status"] = "running"
            self.save()
            self.checkpoint("Attempt start or resume")
            if self.state["phase"] == "response_received":
                self.account(self.state["last_response"])
                if self.state.get("last_response_kind") == "compaction":
                    self.consume_compaction(self.state["last_response"])
                else:
                    self.consume_response(self.state["last_response"])
            if self.state["pending"]:
                self.run_tools()
            while not self.manifest.get("stop_reason"):
                stop = self.budget_stop()
                if stop:
                    self.manifest["stop_reason"] = stop
                    break
                if self.elapsed() - self.last_checkpoint >= self.options.checkpoint_seconds:
                    self.checkpoint("Automatic checkpoint")
                if len(json.dumps(self.state["messages"])) > self.options.context_chars:
                    self.compact()
                    stop = self.budget_stop()
                    if stop:
                        self.manifest["stop_reason"] = stop
                        break
                response = self.call_model(self.payload())
                self.consume_response(response)
                self.run_tools()
                u = self.manifest["usage"]
                print(f"request {self.manifest['requests']}: "
                      f"{u['input_tokens'] + u['output_tokens']} tokens, ${u['cost_usd']:.4f}, "
                      f"{self.elapsed() / 60:.1f} min", flush=True)
            self.checkpoint("Final source")
            self.frozen_elapsed = self.elapsed()
            self.manifest["status"] = "solver_finished"
            self.manifest["ended"] = att.iso()
            self.manifest["ended_unix"] = time.time()
            self.state["phase"] = "exporting"
            self.save()
            self.sandbox.close()
            self.sandbox = None
            return self.finish()
        except KeyboardInterrupt:
            self.manifest["status"] = "interrupted"
            if self.frozen_elapsed is None:
                self.manifest["stop_reason"] = "operator_interrupt"
            self.save()
            raise
        except RuntimeError as exc:
            self.manifest["status"] = "infrastructure_error"
            self.manifest["stop_reason"] = self.manifest.get("stop_reason") or "infrastructure"
            self.manifest["error"] = self.redactor.text(exc)
            self.save()
            raise
        finally:
            if self.sandbox:
                self.sandbox.close()

    def finish(self):
        self.manifest["status"] = "grading"
        self.state["phase"] = "grading"
        self.save()
        att.export_and_grade(self.instance, self.run_dir, self.workspace, self.options, self.manifest,
                             self.redactor, save=lambda manifest: self.save())
        att.apply_validity(self.manifest, bool(
            self.manifest["requests"] > 0 and self.manifest["tool_calls"]
            and self.manifest["stop_reason"] in ("budget_wall", "budget_cost", "budget_tokens", "model_final")))
        self.state["phase"] = "complete"
        self.save()
        return self.manifest
