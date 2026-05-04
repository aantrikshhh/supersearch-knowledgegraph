"""Centralized Codex CLI LLM wrapper.

All core LLM calls route through this module so model selection, timeout
handling, subprocess invocation, and future production replacement are managed
in one place instead of scattered across intent extraction, SQL generation, and
response formatting.
"""

import os
import subprocess
import tempfile

from config import LLM_MODEL, LLM_TIMEOUT


def call_llm(prompt, system_prompt=None, timeout=None):
    """Call the configured Codex CLI and return the final assistant message.

    Codex CLI writes noisy session logs to stdout. The -o flag gives us the
    final message in a file, which is the only content callers should parse.
    """
    full_prompt = prompt
    if system_prompt:
        full_prompt = f"{system_prompt}\n\n{prompt}"

    timeout = timeout or LLM_TIMEOUT
    with tempfile.NamedTemporaryFile(prefix="kg_llm_", suffix=".txt", delete=False) as tmp:
        output_path = tmp.name

    cmd = [
        "codex", "-a", "never", "exec",
        "--skip-git-repo-check",
        "--sandbox", "read-only",
        "-m", LLM_MODEL,
        "-o", output_path,
        "-",
    ]

    try:
        result = subprocess.run(
            cmd,
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        output = ""
        if os.path.exists(output_path):
            with open(output_path, "r") as f:
                output = f.read().strip()

        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or output).strip()
            raise RuntimeError(f"LLM CLI error: {error_text[:500]}")

        return output
    finally:
        try:
            os.unlink(output_path)
        except OSError:
            pass
