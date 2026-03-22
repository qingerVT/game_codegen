"""
Test a single specialist in isolation.

Usage:
    # Run one specialist using an existing contract
    python test_specialist.py --contract output/51595951/contract.json --type collectibles

    # Resume from existing plan file (saved by test_planner.py)
    python test_specialist.py --plan test_out/plan.json --type player

    # List available specialist types from a contract
    python test_specialist.py --contract output/51595951/contract.json --list

    # Validate an existing module JS file without LLM
    python test_specialist.py --validate output/51595951/modules/coin_spawner.js --name coin_spawner
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from specialist import run_specialist, HARNESS_SPEC
from utils.js_syntax_check import validate_module_source


async def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--contract", metavar="PATH", help="Path to contract.json")
    source.add_argument("--plan", metavar="PATH", help="Path to plan.json (from test_planner.py)")

    parser.add_argument("--type", metavar="SPECIALIST", help="Specialist type to run")
    parser.add_argument("--list", action="store_true", help="List specialist types from contract")
    parser.add_argument("--validate", metavar="JS_FILE", help="Validate an existing JS file")
    parser.add_argument("--name", metavar="MODULE", help="Module name for --validate")
    parser.add_argument("--save-dir", metavar="DIR", default="./test_out/modules", help="Where to save JS output")
    args = parser.parse_args()

    # --- Validate mode ---
    if args.validate:
        if not args.name:
            parser.error("--name is required with --validate")
        source_text = Path(args.validate).read_text()
        issues = validate_module_source(source_text, args.name)
        if issues:
            print(f"FAIL — {len(issues)} issues in {args.name}:")
            for i in issues:
                print(f"  • {i}")
            sys.exit(1)
        else:
            print(f"OK — {args.name} passes all checks")
        return

    # --- Load contract ---
    if args.plan:
        data = json.loads(Path(args.plan).read_text())
        contract = data["contract"]
    elif args.contract:
        path = Path(args.contract)
        # Support bare contract.json or {contract, module_graph} envelope
        data = json.loads(path.read_text())
        contract = data.get("contract", data)
    else:
        parser.error("provide --contract or --plan")

    if args.list:
        print("Specialists in contract:")
        for s in contract.get("specialists", []):
            mods = ", ".join(s.get("assigned_modules", []))
            print(f"  {s['type']:25s}  modules: {mods}")
        return

    if not args.type:
        parser.error("provide --type SPECIALIST or --list")

    # Find specialist info
    spec_info = next((s for s in contract["specialists"] if s["type"] == args.type), None)
    if not spec_info:
        available = [s["type"] for s in contract["specialists"]]
        print(f"ERROR: specialist '{args.type}' not found. Available: {available}", file=sys.stderr)
        sys.exit(1)

    print(f"Running specialist: {args.type}")
    print(f"  modules: {spec_info['assigned_modules']}")

    result = await run_specialist(
        specialist_type=spec_info["type"],
        specialist_description=spec_info.get("specialist_description", ""),
        assigned_modules=spec_info["assigned_modules"],
        contract=contract,
        harness_spec=HARNESS_SPEC,
    )

    if result.get("error"):
        print(f"\nFAIL: {result['error']}", file=sys.stderr)
        sys.exit(1)

    save_dir = Path(args.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nProduced {len(result['modules'])} module(s) in {result['duration_s']:.1f}s ({result['iterations']} iteration(s)):")
    for name, source in result["modules"].items():
        out_path = save_dir / f"{name}.js"
        out_path.write_text(source)
        issues = validate_module_source(source, name)
        status = "OK" if not issues else f"WARN ({len(issues)} issues)"
        print(f"  {name:30s} {status}  → {out_path}")
        for issue in issues:
            print(f"      • {issue}")


asyncio.run(main())
