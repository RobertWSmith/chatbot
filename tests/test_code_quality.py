import ast
from collections.abc import Iterator
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
SOURCE_DIRECTORIES = ("app", "migrations", "tests")
DEFINITION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _python_paths() -> Iterator[Path]:
    """Yield every maintained Python source file in the project."""
    yield PROJECT_ROOT / "run.py"
    for directory in SOURCE_DIRECTORIES:
        yield from sorted((PROJECT_ROOT / directory).rglob("*.py"))


def _definitions(path: Path) -> Iterator[ast.AST]:
    """Yield every function, method, and class definition in a source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    yield from (node for node in ast.walk(tree) if isinstance(node, DEFINITION_TYPES))


def test_every_python_callable_has_a_docstring():
    """Ensure all functions, methods, and classes remain documented."""
    missing = []
    for path in _python_paths():
        for node in _definitions(path):
            if ast.get_docstring(node) is None:
                missing.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} {node.name}")

    assert not missing, "Missing docstrings:\n" + "\n".join(missing)


def test_docstrings_follow_basic_google_style():
    """Ensure docstrings use summaries and Google-style section formatting."""
    invalid = []
    for path in _python_paths():
        for node in _definitions(path):
            docstring = ast.get_docstring(node, clean=True)
            if docstring is None:
                continue
            lines = docstring.splitlines()
            summary_has_punctuation = lines[0].endswith((".", "!", "?"))
            summary_is_separated = len(lines) == 1 or not lines[1]
            avoids_rest_fields = not any(
                marker in docstring for marker in (":param", ":return", ":raises")
            )
            if not (summary_has_punctuation and summary_is_separated and avoids_rest_fields):
                invalid.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} {node.name}")

    assert not invalid, "Non-Google-style docstrings:\n" + "\n".join(invalid)
