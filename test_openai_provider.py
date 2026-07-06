"""Small manual smoke script for the OpenCloset OpenAI provider path.

Usage:
    d:/openclaw/.venv/Scripts/python.exe test_openai_provider.py

This is intentionally not part of the automated pytest suite. It is a
manual sanity check when you want to confirm that:
1. the OpenAI SDK is installed,
2. OPENAI_API_KEY is present,
3. the OpenCloset provider wrapper can talk to the configured model.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, r"D:\openclaw\opencloset")

from api.provider.base import ProviderConfig, create_provider


def main() -> int:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model_name = os.environ.get("OPENCLOSET_OPENAI_MODEL", "gpt-4.1-mini")
    base_url = os.environ.get("OPENCLOSET_OPENAI_BASE_URL", "https://api.openai.com/v1")

    print("OpenCloset OpenAI provider smoke test")
    print(f"OPENAI_API_KEY present: {bool(api_key)}")
    print(f"Model: {model_name}")
    print(f"Base URL: {base_url}")

    if not api_key:
        print("No OPENAI_API_KEY is configured. Set it first, then rerun this script.")
        return 1

    provider = create_provider(
        "openai",
        ProviderConfig(
            server_url=base_url,
            model_name=model_name,
            api_key=api_key,
            timeout=60,
        ),
    )

    result = provider.run(
        [{"role": "user", "content": "Reply with exactly: ok"}],
        temperature=0,
        max_tokens=8,
    )
    print("Finish reason:", result.finish_reason)
    print("Input tokens:", result.input_tokens)
    print("Output tokens:", result.output_tokens)
    print("Text:", result.text.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
