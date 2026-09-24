# TaskFlow QA

A deliberately small application — log in, log out, CRUD some tasks — wrapped in
the test architecture I would actually build for a real one.

The application is small on purpose. The interesting part is not the domain, it
is **where each test lives and why**, and a simple domain keeps that visible
instead of hiding it behind business complexity.

```
107 tests · 4 layers · every layer under its time budget
```

---

## The idea in one rule

> **Every defect class should be caught by the leftmost layer capable of
> catching it.**

Cost roughly multiplies by ten at each step right:

| Caught at | Relative cost | Feedback |
|---|---|---|
| Editor / lint / types | 1× | seconds |
| Unit | 2× | seconds |
| Contract | 5× | seconds |
| Component | 10× | a minute |
| Journey (browser) | 50× | minutes |
| Production | 1000× | an incident |

So the design question for any new test is never "does this pass?" but
**"what is the cheapest layer that could have caught this?"**

---

## The layers

| Layer | What it proves | I/O allowed | Budget | Count |
|---|---|---|---|---|
| **Unit** (`suites/unit`) | Domain rules in isolation | None | 15s | 39 |
| **Contract** (`suites/contract`) | Implementation matches `contracts/openapi.yaml` | In-process | 30s | 15 |
| **Component** (`suites/component`) | Endpoints apply the rules, persist, isolate tenants | In-process + DB | 60s | 44 |
| **Journey** (`suites/journey`) | Real browser, real navigation, real storage | Full stack | 300s | 9 |

The shape is a pyramid by construction, not by intention — and it is **enforced**
(see "Time budgets" below).

### Unit — `suites/unit`

`app/rules.py` holds every domain decision as a pure function. No database, no
HTTP, no framework. That is what makes the rules testable in milliseconds.

It also includes **property-based tests** (Hypothesis) over the invariants:

```python
@given(st.text())
def test_normalising_is_idempotent(raw):
    once = normalise_title(raw)
    assert normalise_title(once) == once
```

Example-based tests check the cases you thought of. Property tests check the
ones you didn't — and they are very good at unicode whitespace and boundary
lengths.

### Contract — `suites/contract`

`contracts/openapi.yaml` is **hand-authored and authoritative**. It is not
generated from the code. That inversion is the point: the contract is written
first, the implementation is verified against it, and **a breaking API change
shows up as a red diff in a pull request** rather than as a mystery failure
downstream.

Two kinds of check:

- **Drift** — an endpoint in the code but not the contract is an unreviewed API
  change; one in the contract but not the code breaks any generated client.
- **Provider verification** — real response payloads validated against the
  declared JSON schemas, including every documented error path.

This is the highest-leverage layer here. It costs under a second and replaces a
whole category of integration tests whose only real job was noticing that a
field changed shape.

### Component — `suites/component`

The app plus real persistence, driven in-process. No server, no browser, no
port. This is where things that *need* state live: token revocation is a
database fact, and so is cross-user isolation.

The rules themselves are not re-tested here — only that the endpoints
**apply** them.

### Journey — `suites/journey`

Nine tests. Each earns its place by covering something no cheaper layer
structurally can: real navigation, real `localStorage`, real rendering.

Selectors come from `contracts/ui-contract.yaml` via generated constants, so a
renamed `data-testid` is an **import-time error naming the attribute**, not a
thirty-second timeout in a browser.

---

## `libs/testkit` — the shared substrate

One installed, versioned package holding everything the suites share. It is the
answer to the most common way a test framework rots: the same helper
copy-pasted into six suites, which then drift.

```
testkit/
  transport.py   InProcessTransport | HttpTransport behind one interface
  factories.py   UserFactory, TaskFactory — written once, work at every layer
  selectors.py   generated from contracts/ui-contract.yaml
  fixtures.py    a pytest plugin, registered via entry point
  config.py      settings and repo-root resolution
```

Two design choices carry most of the value.

**The transport abstraction** means the factories are written once and work
everywhere. A component test and a journey test create a user the *same way*:

```python
user = users.create()  # in-process in component, over HTTP in journey
task = tasks.create("Buy milk")
```

**Registration as a pytest plugin** (`[project.entry-points.pytest11]`) means
`pip install -e libs/testkit` puts the fixtures in every suite. There is no
`conftest.py` boilerplate and no `sys.path` manipulation anywhere in the repo.

A suite declares which layer it is by overriding one fixture:

```python
# suites/journey/conftest.py
@pytest.fixture(scope="session")
def testkit_transport() -> str:
    return "http"
```

---

## Time budgets

Every layer has a budget, enforced in CI by `tooling/check_budget.py`:

```
unit: 39 tests in 4.74s (budget 15s, 32% used)
```

A test pyramid does not collapse because someone decides to write slow tests. It
collapses because **nobody owns the clock** — each new test adds a second, no
single commit looks unreasonable, and a year later the "fast" suite takes nine
minutes.

Budgets make that a build failure with a name attached, which turns "should this
be an end-to-end test?" into a question with an enforced answer.

---

## Running it

```bash
python -m venv .venv && .venv/Scripts/activate      # Windows
python -m venv .venv && source .venv/bin/activate   # macOS / Linux

pip install -e . -e libs/testkit ".[test]"

pytest suites/unit          # 39 tests, ~5s
pytest suites/contract      # 15 tests, ~2s
pytest suites/component     # 44 tests, ~15s
```

For the browser layer:

```bash
pip install ".[journey]"
python -m playwright install chromium
pytest suites/journey       # 9 tests, ~10s — starts its own server
```

The journey suite starts a server on a free port automatically. Point it at a
deployed instance instead with `TASKFLOW_BASE_URL=https://... pytest suites/journey`.

To explore by hand:

```bash
ENABLE_TEST_ENDPOINTS=true uvicorn app.main:app --reload
# UI at http://127.0.0.1:8000/app/index.html  (demo / demo1234)
# API docs at http://127.0.0.1:8000/docs
```

---

## Notable decisions

**Logout revokes server-side.** A client dropping a token it could still use is
not a logout. `RevokedToken` makes it a database fact, which makes it testable —
and there is a test proving one session logging out leaves another working.

**Cross-tenant access returns 404, not 403.** 403 confirms the id exists, which
lets an attacker enumerate other people's task ids.

**Unknown username and wrong password return the identical error.** Any
difference is a user-enumeration oracle.

**The test-data factory is gated and the gate is tested.** `POST /test/users`
mints a user without authentication, so it 404s unless `ENABLE_TEST_ENDPOINTS`
is exactly `"true"` — including for `"True"` and `"1"`, which are the values
most likely to be set by accident. That guard is one line in `main.py`, which is
exactly the kind of line that gets refactored away by someone who doesn't know
why it is there.

**Every 4xx uses one error shape** — `{code, detail}`. Clients branch on `code`,
never on prose. A contract test enforces it across every documented error path.

---

## Three bugs found while building this

Kept because they are more instructive than the passing tests.

**An empty bearer token reported the wrong error.** `Authorization: Bearer `
with nothing after it returned `invalid_token`. It is a *missing* credential,
and saying so points the caller at the actual bug. Found by a parametrised
component test; fixed in the application rather than the test.

**SQLite serialised readers against writers.** Under the browser suite, a page
polling `GET /tasks` blocked a concurrent write until the client gave up. Fixed
with WAL mode and a busy timeout — the sort of thing a single-threaded test
suite never surfaces.

**The journey suite hung the server it started.** Uvicorn's output went to
`subprocess.PIPE` with nothing draining it; once the OS pipe buffer filled with
access logs, the server **blocked on write** and stopped answering. The suite
went from 5 passing and 4 timing out, to 9 passing in 10 seconds instead of 128.
Server output now goes to a file, which also means the log survives for
diagnosis. Worth knowing: a hung server with no error message is the signature
of an undrained pipe.

---

## Layout

```
contracts/       openapi.yaml + ui-contract.yaml — the source of truth
app/             the application under test (FastAPI + a small vanilla-JS UI)
  rules.py       every domain decision, pure and I/O-free
libs/testkit/    the shared test substrate, installed as a package
suites/          unit · contract · component · journey
tooling/         time-budget enforcement
.github/         CI — the pyramid executed left to right
```
