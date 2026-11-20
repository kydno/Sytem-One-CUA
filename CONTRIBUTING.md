# Ground rules

The design rules the code holds to, how to write a task the loop cannot do yet, and the checks a
change passes. `AGENTS.md` points here.

- Keep the action set mutually exclusive. Two options that mean the same thing
  split the vote and read as low confidence.
- The classifier (Jev, TypeSafe's decision model) picks; code decides facts. Anything the model would have to
  compute (dates, URL validity, whether a field is focused) is computed in code
  and handed over as state.
- Free text only ever comes from `writer.py`, with a structured reply and a
  code-side guard.
- The writer never picks a click. When the classifier stops it may name a focus,
  ask the user, or have the run wait for a screen still loading, and the classifier
  takes every step from there. Watch the
  `calls:` line: a change that moves work to the writer should say why.
- Platform calls live in the adapters, `macos.py` and `windows.py`, only. Everything else
  reaches them through `platform_adapter.desktop`, and a new adapter call goes into the
  `Desktop` protocol and both adapters together. The accessibility-tree walk is shared, in
  `ax_walk.py`.
- Never add a path that types a password.

## A task the loop cannot do

Write it as a scenario in `tests/test_scenarios.py`: a page graph on the simulated
computer in `tests/world.py`, a policy standing in for the classifier, and assertions on
the outcome, the final page, and the actions the world received. Leave it failing under
`xfail` until the loop can do it, then fix the loop rather than the scenario. The
scenarios run the real `runner.run`, so a failure there is an architecture finding, not
a harness one. Every stop rule in the loop was found or fixed that way.

## Before a pull request

```
uv run ruff check . && uv run ruff format .
scripts/ci
```

There is no hosted CI. The merge gate is `scripts/ci`, run on the branch tip. Ruff and the tests
always run. The other checks turn on by what the branch changed since `origin/main`:

| Scope | On when the branch changes | Runs |
|---|---|---|
| menubar | `menubar/` | `swift build`, and `bash -n` on its scripts. A change there also needs a run by hand |
| infra | `infra/gcp/`, `scripts/osworld-gcp` | `terraform fmt -check`, `init -backend=false` (which needs registry.terraform.io or a provider mirror), `validate` and `test` against mock providers, `shellcheck` (on `scripts/ci` too), a `--dry-run status` |
| mock, linux | `typesafe_computer_use/`, `tests/`, `pyproject.toml`, `uv.lock`, `sandbox/`, `compose.yaml`, `scripts/sandbox` | `clicker-mock check` (every oracle, no model) and the tests again on Linux. On a Mac both run in `scripts/sandbox`, after `scripts/sandbox up` builds and starts it. On Linux the mock check runs directly, and the pytest row is the Linux run |

`scripts/ci --all` turns every scope on, and so does a change to the script itself. A rename counts
for both its old and new path. It ends with a table: each check, whether it passed, failed or was not
run, and the commit, marked when the tree held changes the commit does not. A line under it names the
scopes that were on: a scope that was off has no rows. Put both in the pull request body. A check whose tool is missing (Docker, terraform, shellcheck) is "not run", with
the reason, and the pull request says so: it is never a pass. A sandbox that fails to build or sync
is a failure, not a missing tool. The sandbox is one container for every checkout: when it is serving
another one, the sandbox checks are "not run" until `scripts/sandbox down` there, and two runs at once
take turns with it. A push after the run needs the checks again. The tests are pure
logic, and `tests/conftest.py` stands in for the platform modules where they cannot be installed,
which is why they also run on Linux.

On a Mac the mock check runs in the sandbox, never on the screen you are using. In a cloud session
or the sandbox itself, which are Linux, it runs directly. `scripts/ci --mock-here` runs it on the
Mac, which launches a Chromium there, so an agent asks first ([AGENTS.md](AGENTS.md)).

Add a replay-based note to the PR when a change alters what the model sees:
which run folder, which step, what the decision was before and after. `clicker-eval size` gives the
change in request bytes by part, and `clicker-eval decisions` scores the model's picks on saved steps;
see [improvement loop](docs/improvement-loop.md). Neither touches the machine.
