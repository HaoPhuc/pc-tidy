"""Reading answers from the console. The end of input (a closed window, stdin closed by the task
scheduler) always means "no": pc-tidy never waits for an answer nobody will give."""


def ask(prompt):
    """The typed line, or None at the end of input."""
    try:
        return input(prompt)
    except EOFError:
        print()
        return None


def ask_yes(prompt="Type yes to continue: "):
    """True only for 'yes'."""
    return (ask(prompt) or "").strip().lower() == "yes"
