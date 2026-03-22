"""
D3: Orchestrator
Runs the full pipeline: planner → wave-parallel specialists → integration.
Writes trace.json. Enforces 10-minute budget.

Usage:
    python orchestrator.py "a coin platformer on floating islands"
    python orchestrator.py "arena shooter" --output-dir ./output
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

from planner import run_planner
from specialist import run_specialist, HARNESS_SPEC
from integration import run_integration
from utils.schema_validator import validate_contract, validate_module_graph
from utils.trace import TraceEntry, write_trace

PIPELINE_TIMEOUT_S = 590   # just under 10 minutes
SKIP_FIX_ROUNDS_AFTER_S = 480   # 8 min: skip integration fix rounds
SKIP_INTEGRATION_AFTER_S = 540  # 9 min: skip integration entirely
MAX_CONCURRENT_LLM = 5


async def run_pipeline(
    prompt: str,
    output_dir: str = "./output",
    game_id: str | None = None,
) -> dict:
    """
    Runs the full game_codegen pipeline.
    Returns PipelineResult dict.
    """
    pipeline_start = time.monotonic()

    if game_id is None:
        game_id = str(uuid.uuid4())[:8]

    output_path = Path(output_dir) / game_id
    output_path.mkdir(parents=True, exist_ok=True)

    trace_entries: list[TraceEntry] = []
    llm_semaphore = asyncio.Semaphore(MAX_CONCURRENT_LLM)

    print(f"\n[orchestrator] game_id={game_id}")
    print(f"[orchestrator] output={output_path}")
    print(f"[orchestrator] prompt=\"{prompt}\"\n")

    # ─────────────────────────────────────────
    # PHASE 1: Planning
    # ─────────────────────────────────────────
    print("[orchestrator] Phase 1: Planning...")
    t0 = time.monotonic()

    try:
        plan = await asyncio.wait_for(
            run_planner(prompt, game_id=game_id),
            timeout=300
        )
    except asyncio.TimeoutError:
        raise RuntimeError("Planner timed out after 300 seconds")

    module_graph = plan["module_graph"]
    contract = plan["contract"]

    # Write contract.json and plan.json (for test resumption)
    contract_path = output_path / "contract.json"
    contract_path.write_text(json.dumps(contract, indent=2))
    (output_path / "plan.json").write_text(json.dumps(plan, indent=2))

    t1 = time.monotonic()
    print(f"[orchestrator] Planning done in {t1-t0:.1f}s")
    print(f"[orchestrator] Specialists: {[s['type'] for s in contract['specialists']]}")

    # Human editing window (only if warnings exist)
    warnings = contract.get("contract_warnings", [])
    if warnings:
        print(f"\n[CONTRACT WARNINGS]")
        for w in warnings:
            print(f"  • {w}")
        print(f"\nContract written to: {contract_path}")
        print("Edit the file to resolve warnings, then press Enter to continue.")
        print("Auto-proceeding in 60 seconds...\n")

        try:
            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, input),
                timeout=60.0
            )
        except (asyncio.TimeoutError, EOFError):
            pass

        # Re-read and re-validate after potential edits
        updated = json.loads(contract_path.read_text())
        errors = validate_contract(updated) + validate_module_graph(module_graph, updated)
        if errors:
            print(f"[orchestrator] WARNING: edited contract has validation errors:")
            for e in errors[:5]:
                print(f"  {e}")
            print("[orchestrator] Proceeding with original contract...")
        else:
            contract = updated
            print("[orchestrator] Using edited contract.")

    # Pre-install server deps in background while specialists run
    server_dir = output_path / "server"
    server_dir.mkdir(exist_ok=True)
    (server_dir / "package.json").write_text(
        '{"name":"game-server","version":"1.0.0","main":"index.js",'
        '"dependencies":{"colyseus":"^0.15.0"}}'
    )
    npm_proc = subprocess.Popen(
        ["npm", "install", "--silent"],
        cwd=str(server_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # ─────────────────────────────────────────
    # PHASE 2: Parallel Specialist Waves
    # ─────────────────────────────────────────
    print("\n[orchestrator] Phase 2: Specialist waves...")

    # Build readiness map: module_name → asyncio.Event
    all_module_names = [
        a["name"]
        for wave in module_graph["waves"]
        for a in wave["assignments"]
    ]
    readiness_map = {name: asyncio.Event() for name in all_module_names}

    module_map: dict[str, str] = {}
    failed_modules: set[str] = set()

    async def run_one_specialist(assignment: dict, wave_label: str) -> None:
        """Runs a single specialist assignment, respecting depends_on."""
        name = assignment["name"]
        specialist_type = assignment["specialist"]
        depends_on = assignment.get("depends_on", [])

        # Wait for dependencies
        if depends_on:
            dep_events = [readiness_map[dep] for dep in depends_on if dep in readiness_map]
            await asyncio.gather(*[e.wait() for e in dep_events])

            # Check if any dependency failed
            blocking = [dep for dep in depends_on if dep in failed_modules]
            if blocking:
                print(f"[specialist:{specialist_type}] SKIPPED — blocked by failed: {blocking}")
                trace_entries.append(TraceEntry(
                    specialist_type=specialist_type,
                    started_at=_now(),
                    ended_at=_now(),
                    duration_s=0,
                    modules_produced=[],
                    blocked_by=str(blocking),
                ))
                readiness_map[name].set()
                return

        # Find specialist info from contract
        spec_info = next(
            (s for s in contract["specialists"] if s["type"] == specialist_type),
            None,
        )
        if not spec_info:
            print(f"[specialist:{specialist_type}] ERROR: not found in contract", file=sys.stderr)
            failed_modules.add(name)
            readiness_map[name].set()
            return

        started_at = _now()
        print(f"[specialist:{specialist_type}] starting (wave {wave_label})")

        result = await run_specialist(
            specialist_type=specialist_type,
            specialist_description=spec_info.get("specialist_description", ""),
            assigned_modules=spec_info.get("assigned_modules", [name]),
            contract=contract,
            harness_spec=HARNESS_SPEC,
            _semaphore=llm_semaphore,
        )

        ended_at = _now()

        if result.get("error") or not result.get("modules"):
            print(f"[specialist:{specialist_type}] FAILED: {result.get('error')}", file=sys.stderr)
            failed_modules.add(name)
        else:
            produced = list(result["modules"].keys())
            print(f"[specialist:{specialist_type}] done — produced: {produced}")
            module_map.update(result["modules"])

        trace_entries.append(TraceEntry(
            specialist_type=specialist_type,
            started_at=started_at,
            ended_at=ended_at,
            duration_s=result.get("duration_s", 0),
            modules_produced=list(result.get("modules", {}).keys()),
            iterations=result.get("iterations", 0),
            error=result.get("error"),
        ))

        readiness_map[name].set()

    # Launch all waves — fine-grained parallelism via readiness_map
    all_tasks = []
    for wave in module_graph["waves"]:
        for assignment in wave["assignments"]:
            task = asyncio.create_task(
                run_one_specialist(assignment, wave["wave"])
            )
            all_tasks.append(task)

    await asyncio.gather(*all_tasks, return_exceptions=True)

    elapsed = time.monotonic() - pipeline_start
    print(f"\n[orchestrator] Phase 2 done. {len(module_map)} modules produced in {elapsed:.1f}s")

    if not module_map:
        raise RuntimeError("No modules produced — all specialists failed")

    # ─────────────────────────────────────────
    # PHASE 3: Integration
    # ─────────────────────────────────────────
    elapsed = time.monotonic() - pipeline_start
    if elapsed > SKIP_INTEGRATION_AFTER_S:
        print("[orchestrator] WARNING: budget exhausted, skipping integration", file=sys.stderr)
        _write_partial_output(output_path, module_map, contract, trace_entries)
        return _build_result(game_id, output_path, False, "Budget exhausted before integration")

    max_fix_rounds = 0 if elapsed > SKIP_FIX_ROUNDS_AFTER_S else 2
    if max_fix_rounds == 0:
        print("[orchestrator] Budget tight — skipping integration fix rounds")

    print(f"\n[orchestrator] Phase 3: Integration (max_fix_rounds={max_fix_rounds})...")

    npm_proc.wait()  # Ensure server deps installed

    try:
        integration_result = await asyncio.wait_for(
            run_integration(
                module_map=module_map,
                contract=contract,
                module_graph=module_graph,
                output_path=str(output_path),
                max_fix_rounds=max_fix_rounds,
            ),
            timeout=300,
        )
    except asyncio.TimeoutError:
        integration_result = {"success": False, "playwright_log": "", "errors_attributed": []}
        print("[orchestrator] Integration timed out", file=sys.stderr)

    # Write trace.json
    trace_path = output_path / "trace.json"
    write_trace(trace_entries, str(trace_path))

    total = time.monotonic() - pipeline_start
    success = integration_result.get("success", False)
    print(f"\n[orchestrator] Pipeline complete in {total:.1f}s — success={success}")
    print(f"[orchestrator] Output: {output_path}")

    return _build_result(
        game_id, output_path,
        success=success,
        error=None if success else "Integration checks failed",
    )


def _write_partial_output(
    output_path: Path, module_map: dict, contract: dict, trace_entries: list
) -> None:
    modules_dir = output_path / "modules"
    modules_dir.mkdir(exist_ok=True)
    for name, source in module_map.items():
        (modules_dir / f"{name}.js").write_text(source)
    write_trace(trace_entries, str(output_path / "trace.json"))


def _build_result(game_id, output_path, success, error=None):
    return {
        "game_id": game_id,
        "output_path": str(output_path.resolve()),
        "trace_path": str((output_path / "trace.json").resolve()),
        "success": success,
        "error": error,
    }


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


async def _main():
    parser = argparse.ArgumentParser(description="game_codegen orchestrator")
    parser.add_argument("prompt", nargs="+", help="Game description prompt")
    parser.add_argument("--output-dir", default="./output", help="Output directory")
    parser.add_argument("--game-id", default=None, help="Optional game ID")
    args = parser.parse_args()

    prompt = " ".join(args.prompt)

    try:
        result = await asyncio.wait_for(
            run_pipeline(prompt, output_dir=args.output_dir, game_id=args.game_id),
            timeout=PIPELINE_TIMEOUT_S,
        )
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["success"] else 1)
    except asyncio.TimeoutError:
        print(f"[orchestrator] PIPELINE TIMEOUT ({PIPELINE_TIMEOUT_S}s)", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[orchestrator] FATAL: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    asyncio.run(_main())
