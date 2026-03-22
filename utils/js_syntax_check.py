"""
Checks JavaScript source code for syntax errors using `node --check`.
"""

import subprocess
import tempfile
import os
import re


def check_js_syntax(source: str) -> tuple[bool, str]:
    """
    Runs node --check on the given JS source string.
    Returns (ok: bool, error_message: str).
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
        f.write(source)
        tmp_path = f.name

    try:
        result = subprocess.run(
            ["node", "--check", tmp_path],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return True, ""
        else:
            # Strip the temp file path from error for cleaner messages
            error = result.stderr.replace(tmp_path, "<module>")
            return False, error.strip()
    except subprocess.TimeoutExpired:
        return False, "node --check timed out"
    except FileNotFoundError:
        return False, "node not found in PATH"
    finally:
        os.unlink(tmp_path)


def extract_js_from_response(response_text: str) -> str:
    """
    Extracts JavaScript code from an LLM response.
    Strips markdown fences if present, otherwise returns raw text.
    Handles partial fences that appear when module separators split a fenced block.
    """
    text = response_text.strip()

    # Try to find a complete ```js or ```javascript or ``` fence
    patterns = [
        r"```(?:javascript|js)\n(.*?)```",
        r"```\n(.*?)```",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()

    # Strip dangling opening fence (e.g., chunk starts with ```js due to split)
    text = re.sub(r'^```(?:javascript|js)?\s*\n?', '', text)
    # Strip dangling closing fence (e.g., chunk ends with ``` due to split)
    text = re.sub(r'\n?```\s*$', '', text)
    # Strip YAML/markdown frontmatter or horizontal rule (--- lines at start)
    text = re.sub(r'^(-{3,})\s*\n', '', text)

    return text.strip()


def validate_module_source(source: str, module_name: str) -> list[str]:
    """
    Runs all static checks on a generated module source.
    Returns list of violation messages (empty = clean).
    """
    issues = []

    # Syntax check
    ok, err = check_js_syntax(source)
    if not ok:
        issues.append(f"Syntax error: {err}")
        return issues  # No point checking further if syntax is broken

    # Must have export default
    if "export default" not in source:
        issues.append("Missing 'export default class'")

    # Must not use import or require
    if re.search(r'^\s*import\s', source, re.MULTILINE):
        issues.append("Must not use 'import' statements — use globals (THREE, RAPIER, etc.)")
    if re.search(r'\brequire\s*\(', source):
        issues.append("Must not use 'require()' — use globals (THREE, RAPIER, etc.)")

    # name field must match module_name
    name_match = re.search(r"name\s*=\s*['\"]([^'\"]+)['\"]", source)
    if not name_match:
        issues.append(f"Missing 'name' field in class — must be name = '{module_name}'")
    elif name_match.group(1) != module_name:
        issues.append(
            f"Class 'name' field is '{name_match.group(1)}' but must be '{module_name}'"
        )

    # Must not access ctx.modules inside build()
    # Simple heuristic: find build() method body and scan for ctx.modules
    build_match = re.search(
        r'async\s+build\s*\(\s*ctx\s*\)\s*\{(.*?)\n\s*\}',
        source, re.DOTALL
    )
    if build_match:
        build_body = build_match.group(1)
        # Strip single-line comments before checking to avoid false positives
        build_body_no_comments = re.sub(r'//[^\n]*', '', build_body)
        if re.search(r'ctx\.modules\b', build_body_no_comments):
            issues.append(
                "Must not access ctx.modules inside build() — use start() or update() instead"
            )

    # Must not use requestAnimationFrame
    if "requestAnimationFrame" in source:
        issues.append(
            "Must not call requestAnimationFrame — the harness drives update(dt) automatically"
        )

    # Top-level await (outside async function)
    if re.search(r'^await\s', source, re.MULTILINE):
        issues.append("Top-level 'await' is not supported — use await inside async build()")

    # Forbidden ctx variable names — must use canonical names
    if re.search(r'\bctx\.scoreMap\b', source):
        issues.append(
            "Forbidden: ctx.scoreMap — use ctx.scoreState (Map<playerId, score>) instead"
        )
    if re.search(r'\bctx\.localSessionId\b', source):
        issues.append(
            "Forbidden: ctx.localSessionId — use ctx.localPlayerId instead"
        )

    # Score writes outside the network module — only network may call ctx.scoreState.set()
    if 'network' not in module_name.lower():
        if re.search(r'ctx\.scoreState\.set\s*\(', source):
            issues.append(
                "Forbidden: only the network module may call ctx.scoreState.set() — "
                "remove this call; scores must be updated from server-authoritative messages only"
            )

    return issues
