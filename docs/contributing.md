# Contributing

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q          # offline unit tests
.venv/bin/python -m cryptospeed --selftest   # live data-source check
```

Never commit tokens or `*.db` files.
