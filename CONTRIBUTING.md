# Ground rules

The design rules the code holds to, how to write a task the loop cannot do yet, the checks a
change passes, and the review loop every PR goes through. `AGENTS.md` points here.

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
uv run pytest -q
```

There is no CI, so run these yourself. The tests are pure logic, and `tests/conftest.py` stands in
for the platform modules where they cannot be installed, so they also run on Linux. A change to
`infra/gcp` or `scripts/osworld-gcp` should pass `terraform fmt -check`, `terraform validate`,
`terraform test` and `shellcheck` locally. A change to the menu bar app in `menubar/` should pass
`swift build` and also needs a run by hand.

Add a replay-based note to the PR when a change alters what the model sees:
which run folder, which step, what the decision was before and after. `clicker-eval size` gives the
change in request bytes by part, and `clicker-eval decisions` scores the model's picks on saved steps;
see [improvement loop](docs/improvement-loop.md). Neither touches the machine.

## PR review loop

Every push to an open PR gets a review from a separate reviewer agent, and the session that wrote
the PR fixes what it finds and pushes again, round after round, until the reviewer calls the PR
ready. The reviewer is [`.claude/agents/pr-reviewer.md`](.claude/agents/pr-reviewer.md): Sonnet at
extra high effort, read-only, with a triage rubric (skip docs and copy, quick pass on tests and
tooling, deep pass on the loop, the platform layer, the test guard, and anything that reaches the
machine, a model, or a cloud bill). Tune the rubric in that file.

It runs inside the authoring Claude Code session, not in GitHub Actions: there is no CI, and an
action would need an API key in repo secrets. The review is not a test run and does not replace the
checks above. The reviewer does not run them and does not report what they would catch.

### Steps, after each push

1. **Wait about two minutes.** The owner sometimes merges right after a push. Note the head commit
   you just pushed, then start the wait in the background so the turn is not blocked:

   ```bash
   sleep 120 && gh api repos/kydno/Sytem-One-CUA/pulls/<n> --jq '[.state, (.merged | tostring), .head.sha] | @tsv'
   ```

   In Claude Code, run it with `run_in_background`, and the session wakes when it exits. Use the
   REST API: GraphQL (`gh pr view`) is blocked in cloud sessions. Where `gh` is not available, run
   only `sleep 120` in the background and read the PR with the GitHub MCP `pull_request_read`.
2. **Check before reviewing.** If the PR is closed or merged, stop: no review. If the head is not
   the commit you pushed, stop: the wait for the newer push covers it. A burst of pushes gets one
   review, for the last one.
3. **Run the reviewer.** Agent tool, `subagent_type: pr-reviewer`. The frontmatter picks the model
   and effort, so do not override them. The prompt is exactly `Review PR #<n> @ <head sha>.` and
   nothing else: no summary of the diff, no "I fixed X", no focus areas, no earlier rounds pasted
   in. A sub agent starts fresh and knows only its prompt, so anything you add there frames the
   review. It reads the diff and the earlier rounds on the PR itself, and treats your PR body and
   replies as claims to verify. If the session started before the agent file existed and
   `pr-reviewer` is not in its agent list, use a general-purpose agent on Sonnet at extra high
   effort whose prompt is `Act as the agent in .claude/agents/pr-reviewer.md. Review PR #<n> @ <head sha>.`
   followed by an optional note on where the checkout is and which tool reads GitHub, and then the `AGENTS.md` rule "Never take over the machine without explicit approval", word
   for word, since that agent has not read the file yet. The reviewer reads both as process, not
   notes. That agent also does not get the file's `tools` allowlist, so its read-only rule is a
   prompt rule only: start a new session, which picks up the agent file, when you can.
4. **Post the round on the PR.** One issue comment per round, headed
   `Claude review, round <N> @ <short sha>`, with the reviewer's output as it came back. It is a
   comment, not a GitHub review, because the PR author's account cannot request changes on or
   approve its own PR.
5. **Act on the verdict.**
   - `ready`: the loop is done. Say so in your report. Merge only if the owner said merge.
   - `changes`: fix every `must-fix`, and every `should-fix` you agree with. Nits are optional. For
     a finding you think is wrong, write why in a reply comment on the PR, so the next round reads
     it there. That comment is the only place your side of a finding goes. Rerun the checks the fix
     touches, update the PR body, push, and go back to step 1.
   - `owner`: stop and put the `owner call` items to the owner in one short list. Once they
     answer, post one comment per answer headed `Owner decision on round <N> #<finding>`, quoting
     their words, make any change they asked for, push, and go back to step 1. The reviewer drops
     an item an `Owner decision` comment settles; your own replies do not settle it, because the
     owner and you may post from the same account.

### When it stops

There is no cap on rounds. The loop ends at `ready`, pauses at `owner` until the owner answers, or
ends when the owner closes, merges,
or says stop. If the reviewer repeats a finding after your reply without a new reason, do not push a
third answer: put it to the owner with the `owner call` items, and record their answer the same
way, as an `Owner decision` comment naming the round and finding number.

### Notes

- A PR pushed from outside a Claude Code session gets no automatic review. Any Claude Code session
  runs the same loop on "review PR #<n>".
- The loop is the authoring session's job for as long as the PR is open. A session that subscribes
  to the PR's activity also wakes on the owner's comments between rounds.
- Changing the reviewer's model, effort, or rubric: edit `.claude/agents/pr-reviewer.md`. Changing
  these steps: edit this section and the line in `AGENTS.md` that points here.
