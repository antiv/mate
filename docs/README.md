# MATE documentation

This folder is what the dashboard's **Documentation** page serves. It lives in the
repo so that a pull request changes the code and its documentation together.

```
docs/
  user/        Guides for people using the dashboard.
  dev/         Guides for people changing or integrating with MATE.
  reference/   Generated from the code. Never edited by hand.
```

```bash
python scripts/gen_docs.py                        # regenerate docs/reference/
python scripts/gen_docs.py --check                # what CI runs
python scripts/gen_docs.py drift --base origin/main   # guides whose code changed
python scripts/gen_docs.py coverage               # source files no guide covers
```

How generation, the drift check, the dashboard page and search work, and how to
write a guide: [dev/documentation.md](dev/documentation.md).
