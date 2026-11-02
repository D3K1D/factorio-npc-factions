"""Private host CLI. 'live' is the only command that sends a paid request."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from ._reference_contract import ContractError, load_json
from .artifacts import export_outcome, write_json
from .context import ContextError, canonical_bytes, validate_context
from .memory import DesignerMemory, MemoryError
from .private_config import (MaskedCredentialSupplier, PrivateConfigError,
                             ResponsesConfig, DEFAULT_RESPONSES_ENDPOINT, load_private_config)
from .proposal_schema import proposal_schema
from .request import make_request
from .workflow import propose


def read_context(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(128_001)
    if len(raw) > 128_000:
        raise ContractError("Context exceeds byte limit")
    return validate_context(load_json(raw.decode("utf-8")))


def _configure(args):
    print("Choose an OpenAI API model that supports Responses structured outputs.")
    model = input("Exact model identifier (no default): ").strip()
    limit_text = input("Maximum output tokens [2048]: ").strip()
    limit = int(limit_text) if limit_text else 2048
    config = ResponsesConfig(model=model, max_output_tokens=limit, max_retries=0,
                             max_request_bytes=96_000)
    target = args.config.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    settings = {"model": config.model, "endpoint": config.endpoint,
                "timeout_s": config.timeout_s, "total_deadline_s": config.total_deadline_s,
                "max_output_tokens": config.max_output_tokens, "max_retries": 0,
                "max_request_bytes": config.max_request_bytes,
                "max_response_bytes": config.max_response_bytes}
    write_json(target, settings)
    print("Settings saved without a key. The live command prompts for it locally with masking.")
    return 0


def _generate(args):
    config = load_private_config(args.config)
    if config.model.upper().startswith(("CHOOSE_", "SET_EXPLICIT", "YOUR_MODEL", "REPLACE_")):
        raise PrivateConfigError("example_model_must_be_replaced")
    if config.max_retries != 0:
        raise PrivateConfigError("first_live_checkpoint_requires_zero_retries")
    if config.endpoint != DEFAULT_RESPONSES_ENDPOINT:
        raise PrivateConfigError("live_checkpoint_requires_official_openai_endpoint")
    context = read_context(args.context)
    if context["evidence_mode"] != "fixture":
        # Native exporter authentication is deliberately not implemented by this
        # first host checkpoint. Do not substitute a verifier that trusts input.
        raise ContextError("CLI checkpoint requires declared fixture state; native bridge is pending")
    request = make_request(context, args.goal, request_id=args.request_id,
                           max_entities=args.max_entities, max_stages=args.max_stages)
    summary = {
        "mode": args.command, "provider": "openai-responses", "model": config.model,
        "state_source": context["evidence_mode"], "request_id": request["request_id"],
        "context_sha256": request["context_sha256"], "snapshot_sha256": request["snapshot_sha256"],
        "max_output_tokens": config.max_output_tokens, "max_input_bytes": args.max_input_bytes,
        "timeout_s": config.timeout_s, "total_deadline_s": config.total_deadline_s,
        "maximum_requests": 1, "retries": 0,
        "max_entities": args.max_entities, "max_stages": args.max_stages,
        "native_flow_qualified": False,
    }
    # All local failures that can be detected are checked before credential entry
    # or an external request. Output must be new and the parent writable.
    output = args.out.resolve()
    if output.exists():
        raise ContractError("Output directory already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    memory_path = args.memory.resolve()
    with DesignerMemory(memory_path, context["identity"]["world_id"],
                        context["identity"]["force_id"], evidence_mode="fixture") as memory:
        # Bound conversation/summary context before paying for a proposal.
        history_budget = max(1024, args.max_input_bytes - len(canonical_bytes(context))
                             - len(canonical_bytes(request)) - 1024)
        history = memory.context(context, max_bytes=min(history_budget, 16_000))
        envelope_bytes = len(canonical_bytes({"request": request, "context": context, "memory": history}))
        if envelope_bytes > args.max_input_bytes:
            raise ContractError("Request exceeds selected input budget")
        summary["input_json_bytes"] = envelope_bytes
        from .providers import prepare_request
        payload = prepare_request(config, {"request": request, "context": context, "memory": history},
                                  proposal_schema())
        summary["http_payload_bytes"] = len(canonical_bytes(payload))
        print(json.dumps(summary, indent=2))
        if args.command == "preflight":
            print("Preflight passed. No credential was read and no provider request was sent.")
            return 0
        # The command explicitly requests a paid call. Token and time ceilings
        # bound it; dollar cost depends on the configured model's current pricing.
        print("One paid request will be sent. Token limits are not a dollar spending cap.")
        from .providers import OpenAIHTTPTransport, ResponsesProvider
        # Human entry time is outside the bounded provider request deadline.
        credential = MaskedCredentialSupplier()()
        provider = ResponsesProvider(config, OpenAIHTTPTransport(), lambda: credential)
        outcome = propose(request, context, provider, latest_context=lambda: read_context(args.context),
                          memory=memory, max_input_bytes=args.max_input_bytes)
        # Persist the request/provenance for refusals/errors as well, without any
        # credential. Rejected proposals never receive an accepted-plan artifact.
        outcome.evidence["request"] = request
        outcome.evidence["context_sha256"] = request["context_sha256"]
        directory = export_outcome(outcome, output)
        memory.add_message("user", args.goal, context["identity"]["observed_tick"])
        memory.add_message("assistant", "Designer outcome: " + outcome.status + "; " + outcome.reason,
                           context["identity"]["observed_tick"])
        print(json.dumps({"status": outcome.status, "reason": outcome.reason,
                          "output_directory": str(directory), "native_flow_qualified": False}))
        return 0 if outcome.accepted else 2


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("configure", help="Save model/token settings locally, without credentials")
    setup.add_argument("--config", type=Path, default=Path(".host/private-config.json"))
    for mode in ("preflight", "live"):
        g = sub.add_parser(mode, help="Check locally" if mode == "preflight" else "Send one bounded paid OpenAI request")
        g.add_argument("--config", type=Path, required=True)
        g.add_argument("--context", type=Path, required=True)
        g.add_argument("--goal", required=True)
        g.add_argument("--out", type=Path, required=True)
        g.add_argument("--memory", type=Path, default=Path(".host/designer-memory.sqlite3"))
        g.add_argument("--request-id", default="request-1")
        g.add_argument("--max-entities", type=int, default=4)
        g.add_argument("--max-stages", type=int, default=2)
        g.add_argument("--max-input-bytes", type=int, choices=range(32_000, 128_001), default=64_000,
                       metavar="32000..128000")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return _configure(args) if args.command == "configure" else _generate(args)
    except (ContractError, ContextError, MemoryError, PrivateConfigError, OSError, ValueError,
            sqlite3.Error, UnicodeError, EOFError):
        # Avoid echoing arbitrary inputs, provider bodies, file contents or keys.
        print("Local validation/setup failed. Check explicit model, context, bounds and fresh output path.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
