"""
Test integration in isolation, resuming from a saved game output folder.

Usage:
    # Run integration on existing specialist outputs (no LLM re-run)
    python test_integration.py --game-dir output/51595951

    # Run integration using a plan + specific JS module files
    python test_integration.py --plan test_out/plan.json --modules-dir test_out/modules

    # Just run static checks without Playwright
    python test_integration.py --game-dir output/51595951 --static-only

    # Re-run with more fix rounds
    python test_integration.py --game-dir output/51595951 --fix-rounds 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from integration import run_integration
from utils.schema_validator import validate_contract, validate_module_graph
from utils.js_syntax_check import validate_module_source


async def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--game-dir", metavar="PATH",
                        help="Existing game output dir (e.g. output/51595951)")
    source.add_argument("--plan", metavar="PATH",
                        help="plan.json from test_planner.py (needs --modules-dir too)")

    parser.add_argument("--modules-dir", metavar="PATH", default=None,
                        help="Directory of .js module files (used with --plan)")
    parser.add_argument("--fix-rounds", type=int, default=2,
                        help="Max integration fix rounds (default: 2)")
    parser.add_argument("--static-only", action="store_true",
                        help="Run static checks only, skip Playwright")
    args = parser.parse_args()

    # --- Load contract + module_map ---
    if args.game_dir:
        game_dir = Path(args.game_dir)
        contract_path = game_dir / "contract.json"
        if not contract_path.exists():
            print(f"ERROR: no contract.json in {game_dir}", file=sys.stderr)
            sys.exit(1)

        contract = json.loads(contract_path.read_text())

        # Load modules from modules/ subdir or root .js files
        module_map = {}
        modules_dir = game_dir / "modules"
        search_dir = modules_dir if modules_dir.exists() else game_dir
        for js_file in search_dir.glob("*.js"):
            module_map[js_file.stem] = js_file.read_text()

        # Try to load module_graph from trace or a saved plan
        plan_path = game_dir / "plan.json"
        module_graph = {}
        if plan_path.exists():
            plan_data = json.loads(plan_path.read_text())
            module_graph = plan_data.get("module_graph", {})

        output_path = str(game_dir)

    elif args.plan:
        if not args.modules_dir:
            parser.error("--modules-dir is required with --plan")
        data = json.loads(Path(args.plan).read_text())
        contract = data["contract"]
        module_graph = data.get("module_graph", {})

        module_map = {}
        for js_file in Path(args.modules_dir).glob("*.js"):
            module_map[js_file.stem] = js_file.read_text()

        output_path = str(Path(args.plan).parent / "integration_out")
        Path(output_path).mkdir(parents=True, exist_ok=True)

    else:
        parser.error("provide --game-dir or --plan")

    print(f"Contract game_id: {contract.get('game_id')}")
    print(f"Modules loaded:   {sorted(module_map.keys())}")

    # Validate contract
    errs = validate_contract(contract)
    if module_graph:
        errs += validate_module_graph(module_graph, contract)
    if errs:
        print(f"\nContract validation warnings ({len(errs)}):")
        for e in errs:
            print(f"  {e}")

    # Run per-module static checks
    print("\n--- Static module checks ---")
    all_clean = True
    for name, source in module_map.items():
        issues = validate_module_source(source, name)
        if issues:
            all_clean = False
            print(f"  FAIL {name}:")
            for i in issues:
                print(f"       • {i}")
        else:
            print(f"  OK   {name}")

    if args.static_only:
        sys.exit(0 if all_clean else 1)

    print("\n--- Running integration ---")
    result = await run_integration(
        module_map=module_map,
        contract=contract,
        module_graph=module_graph,
        output_path=output_path,
        max_fix_rounds=args.fix_rounds,
    )

    print(f"\nIntegration success: {result['success']}")
    if result.get("playwright_log"):
        print("Playwright log:")
        print(result["playwright_log"][:2000])
    if result.get("errors_attributed"):
        print("Attributed errors:")
        for e in result["errors_attributed"]:
            print(f"  {e}")

    sys.exit(0 if result["success"] else 1)


asyncio.run(main())
