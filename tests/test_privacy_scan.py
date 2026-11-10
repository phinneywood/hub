"""The commit-time privacy scan. Every sample value is fictional and assembled at run time, so this
file does not trip the scan itself."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "privacy_scan.py"
if not SCRIPT.exists():  # the installed-package check carries tests but not repository tools
    pytest.skip("tools/privacy_scan.py is not part of the installed package", allow_module_level=True)
spec = importlib.util.spec_from_file_location("privacy_scan", SCRIPT)
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)

AT = "@"


@pytest.mark.parametrize("text,kind", [
    ("write to alex" + AT + "gmail.com", "email address"),
    ("cd /Users/" + "alex/Developer", "home path"),
    ("call 212-867-" + "5309", "phone number"),
    ("open hub.tail1234" + ".ts.net", "private hostname"),
    ("-----BEGIN OPENSSH " + "PRIVATE KEY-----", "private key"),
    ("key = 'sk-" + "or-v1-" + "a" * 32 + "'", "API key"),
    ("token ghp_" + "A" * 36, "GitHub token"),
    ("AKIA" + "ABCDEFGHIJKLMNOP", "AWS key"),
    ("AIza" + "B" * 35, "Google key or token"),
    ("1234567890:" + "C" * 35, "bot token"),
])
def test_generic_patterns_are_caught(text, kind):
    assert kind in scan.scan_line(text)


@pytest.mark.parametrize("text", [
    "sam" + AT + "school.example and alex" + AT + "example.com and x" + AT + "mail.invalid",
    "calendar-notification" + AT + "google.com",
    "Co-Authored-By: Claude <noreply" + AT + "anthropic.com>",
    "12345+alex" + AT + "users.noreply.github.com",
    "fictional phone 206-555-" + "0123",
    "see /Users/.../Developer",
    "@pytest.fixture",
    "synthetic alex" + AT + "gmail.com  # " + scan.MARKER,
])
def test_fictional_and_marked_values_pass(text):
    assert scan.scan_line(text) == []


def test_private_terms_match_case_insensitively_and_allowed_phrases_are_masked():
    terms, allowed = ["maple hill", "rivera"], ["maple hill/"]
    assert scan.scan_line("Meet at Maple Hill on Friday", terms, allowed) == ["private word-list term"]
    assert scan.scan_line("github.com/maple hill/hub", terms, allowed) == []
    # The opt-out marker never exempts private terms.
    assert scan.scan_line("Rivera  # " + scan.MARKER, terms, allowed) == ["private word-list term"]


def test_denylist_file_format(tmp_path):
    path = tmp_path / "list.txt"
    path.write_text("# comment\n\nMaple Hill\n!maple hill/\n")
    assert scan.load_denylist(path) == (["maple hill"], ["maple hill/"])


def run(repo, *args):
    return subprocess.run(["python3", "-I", str(ROOT / "tools" / "privacy_scan.py"), *args],
                          cwd=repo, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "list.txt").write_text("maple hill\n")
    return tmp_path


def test_staged_scan_reports_location_without_the_term(repo):
    (repo / "notes.md").write_text("A fine line\nPickup at Maple Hill\n")
    subprocess.run(["git", "add", "notes.md"], cwd=repo, check=True)
    result = run(repo, "--staged", "--denylist", str(repo / "list.txt"))
    assert result.returncode == 1
    assert "notes.md:2: private word-list term" in result.stderr
    assert "maple" not in result.stderr.lower()


def test_staged_scan_checks_file_names_and_passes_clean_changes(repo):
    (repo / "maple-hill.md").write_text("ok\n")
    subprocess.run(["git", "add", "maple-hill.md"], cwd=repo, check=True)
    assert run(repo, "--staged", "--denylist", str(repo / "list.txt")).returncode == 0  # hyphen differs
    (repo / "maple hill.md").write_text("ok\n")
    subprocess.run(["git", "add", "maple hill.md"], cwd=repo, check=True)
    result = run(repo, "--staged", "--denylist", str(repo / "list.txt"))
    assert result.returncode == 1 and "(file name)" in result.stderr


def test_missing_word_list_fails_closed_unless_generic_only(repo):
    (repo / "a.txt").write_text("fine\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    assert run(repo, "--staged", "--denylist", str(repo / "missing.txt")).returncode == 2
    assert run(repo, "--staged", "--no-denylist").returncode == 0


def test_commit_message_scan_skips_comment_lines(repo):
    message = repo / "MSG"
    message.write_text("Fix pickup at Maple Hill\n")
    assert run(repo, "--message", str(message), "--denylist", str(repo / "list.txt")).returncode == 1
    message.write_text("Fix pickup times\n# Maple Hill in a comment is ignored\n")
    assert run(repo, "--message", str(message), "--denylist", str(repo / "list.txt")).returncode == 0
