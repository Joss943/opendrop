# Contributing

```bash
git clone https://github.com/Joss943/opendrop.git
cd opendrop
python -m venv .venv
# macOS/Linux: source .venv/bin/activate     Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Before opening a pull request:

```bash
ruff check .          # lint
ruff format .         # format
pytest -v             # tests
```

The tests do not need AWDL or any special hardware. `tests/test_end_to_end.py` runs a real
sender and receiver over TLS on localhost; `tests/test_client.py` starts mDNS on the
loopback interface (skipped where multicast on loopback is unavailable).

To debug the protocol, run with `-d`: every request/response is written to
`~/.opendrop/debug/` as a binary plist (`plutil -p file.plist` on macOS, or
`python -c "import plistlib,sys,pprint; pprint.pprint(plistlib.load(open(sys.argv[1],'rb')))" file.plist`).

CI (`.github/workflows/ci.yml`) runs the tests on Linux, macOS and Windows.
