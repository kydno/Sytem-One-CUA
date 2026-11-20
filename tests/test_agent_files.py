"""The reviewer agent carries its own copy of the AGENTS.md machine rule, since AGENTS.md says every
subagent gets it word for word and the reviewer's prompt is one line. Keep the two equal."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADING = "## Never take over the machine without explicit approval"
END = "If you run subagents, give them this rule word for word."


def machine_rule(text: str) -> str:
    start = text.index(HEADING)
    return text[start : text.index(END, start) + len(END)]


def test_reviewer_carries_the_machine_rule_word_for_word():
    agents = machine_rule((ROOT / "AGENTS.md").read_text())
    quoted = machine_rule((ROOT / ".claude" / "agents" / "pr-reviewer.md").read_text())
    unquoted = "\n".join(line.removeprefix("> ").removeprefix(">") for line in quoted.splitlines())
    assert unquoted == agents
