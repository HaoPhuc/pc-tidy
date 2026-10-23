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


def examples(doc):
    """The end of a command's --help: the examples from the Usage block of its docstring."""
    usage = doc.split("Usage:", 1)[1].split("\n\n", 1)[0] if "Usage:" in doc else ""
    return (f"examples:{usage}\n\nAdd --debug to see the details if something goes wrong. "
            "Everything pc-tidy writes for itself is in %LOCALAPPDATA%\\pc-tidy.")
