# Licensing

Oracle Radio is **dual-scoped on purpose**: the *format* is free to implement so it can
spread; the *engine and apps* are copyleft so they can't be taken closed.

| Scope | Path | License | Why |
|---|---|---|---|
| **The format / spec** | `spec/` (`*.oradio`, `*.loom`, `ORADIO_FORMAT.md`, `ORADIO_SCHEMA_V2.md`) | **Apache-2.0** (`spec/LICENSE`) | A format only wins if anyone can implement it freely — patent grant + no copyleft friction. |
| **Everything else** | the engine (`oradio_engine/`), the booth (`loom_booth.py`), tools, players, data | **AGPL-3.0** (`LICENSE`) | The reference implementation stays open even when run as a service — no closed SaaS forks. |

## What this means for you
- **Implement the `.oradio` / `.loom` format** in your own project, any license, commercial or not — that's the Apache-scoped spec.
- **Use, modify, self-host the engine/booth** under AGPL-3.0: if you distribute it *or run a
  modified version as a network service*, you must publish your source under AGPL-3.0.
- **Want to embed the engine in a closed product?** A separate commercial license is available
  — the copyright is held in one place (see below), so dual-licensing is possible.

## Copyright
> Copyright (C) 2026 Evan (evengineer1ng) &lt;evanap19@gmail.com&gt;

Holding 100% of the copyright is what keeps the commercial dual-license lane open; require a CLA
from any outside contributor to preserve it.

Full texts: AGPL-3.0 in [`LICENSE`](LICENSE), Apache-2.0 in [`spec/LICENSE`](spec/LICENSE).
