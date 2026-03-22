"""
Test planner in isolation.

Usage:
    # Run planner and save output
    python test_planner.py "collect coins on floating islands"

    # Re-validate an existing contract (no LLM call)
    python test_planner.py --validate output/51595951/contract.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from planner import run_planner
from utils.schema_validator import validate_contract, validate_module_graph


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="?", help="Game prompt")
    parser.add_argument("--validate", metavar="CONTRACT_JSON", help="Validate existing contract file only")
    parser.add_argument("--save", metavar="PATH", default="./test_out/plan.json", help="Where to save result")
    args = parser.parse_args()

    if args.validate:
        # Offline mode: validate existing contract
        data = json.loads(Path(args.validate).read_text())
        contract = data.get("contract", data)   # accept bare contract or {contract, module_graph}
        module_graph = data.get("module_graph", {})
        errs = validate_contract(contract) + validate_module_graph(module_graph, contract)
        if errs:
            print(f"FAIL — {len(errs)} validation errors:")
            for e in errs:
                print(f"  {e}")
            sys.exit(1)
        else:
            print("OK — contract is valid")
            print("specialists:", [s["type"] for s in contract.get("specialists", [])])
            sys.exit(0)

    if not args.prompt:
        parser.error("provide a prompt or --validate PATH")

    print(f"Running planner: {args.prompt!r}")
    result = await run_planner(args.prompt)

    out = Path(args.save)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))

    contract = result["contract"]
    module_graph = result["module_graph"]
    print(f"\ngame_id:     {contract['game_id']}")
    print(f"specialists: {[s['type'] for s in contract['specialists']]}")
    print(f"waves:       {[[a['name'] for a in w['assignments']] for w in module_graph['waves']]}")
    warnings = contract.get("contract_warnings", [])
    if warnings:
        print(f"\nwarnings ({len(warnings)}):")
        for w in warnings:
            print(f"  • {w}")
    print(f"\nSaved to: {out}")


asyncio.run(main())
