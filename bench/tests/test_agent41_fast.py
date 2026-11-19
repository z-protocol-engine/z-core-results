"""Offline tests for agent41_fast: no model call, no network."""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("a41f", os.path.join(HERE, "..", "agents", "agent41_fast.py"))
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)
tr = A.tolerant_replace


def test_exact():
    assert tr("a\nb\nc\n", "b\n", "B\n") == ("a\nB\nc\n", "")


def test_ambiguous_lists_lines():
    out, err = tr("x\ny\nx\n", "x", "z")
    assert out is None and "2 times" in err and "lines 1, 3" in err


def test_trailing_spaces():
    out, note = tr("def f():   \n    return 1\n", "def f():\n    return 1", "def f():\n    return 2")
    assert out == "def f():\n    return 2\n" and "trailing" in note


def test_indentation_reindented():
    text = "class C:\n    def f(self):\n        x = 1\n        return x\n"
    out, note = tr(text, "def f(self):\n    x = 1\n    return x", "def f(self):\n    x = 2\n    return x")
    assert out == "class C:\n    def f(self):\n        x = 2\n        return x\n" and "indentation" in note


def test_crlf_kept():
    out, _ = tr("a\r\nb\r\nc\r\n", "a\nb\n", "a\nB\n")
    assert out == "a\r\nB\r\nc\r\n"


def test_not_found_hint():
    out, err = tr("def compute_total(x):\n    return x\n", "def compute_totl(x):\n    pass", "")
    assert out is None and "closest line 1" in err


def test_relief_branch_keeps_driver_settings():
    budget = A.Budget()
    model = A.Model(budget)
    body = model.body([{"role": "user", "content": "x"}], [], A.DRIVER_MODEL)
    assert body["reasoning"].get("effort") in ("high", "medium")
    assert body["reasoning"].get("context") == "all_turns"
    assert body["seed"] == A.REQUEST_SEED and body["temperature"] == 0.0
    assert body["provider"]["only"] and body["max_tokens"] > A.RELIEF_MODEL_TOKENS


def test_giving_up_original_phrases():
    for text in ("I could not get it to work", "partial support", "unable to reproduce"):
        assert A.GIVING_UP.search(text)


def test_missing_paths_ignore_urls_and_tracebacks(tmp_path):
    agent = A.Agent.__new__(A.Agent)
    agent.statement = ("see https://github.com/x/y/issues/1.html and File \"/usr/lib/python3/x.py\"; "
                       "write the result to /out/answer.txt")
    agent.root = str(tmp_path)
    import re
    found = [p for p in sorted(set(re.findall(r"(?<![\w.:/~-])(/[A-Za-z0-9_.\-/]+\.[A-Za-z0-9]{1,8})\b", agent.statement)))
             if not p.startswith(("//", "/dev/", "/proc/", "/usr/", "/bin/", "/etc/", "/lib", "/opt/", "/home/"))]
    assert found == ["/out/answer.txt"]


def _repo(tmp_path, files):
    import subprocess
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    subprocess.run("git init -q && git add -A && git -c user.email=a@b -c user.name=a commit -qm base",
                   shell=True, cwd=tmp_path, check=True)


def _diff(tmp_path):
    import subprocess
    return subprocess.run(["git", "diff"], cwd=tmp_path, capture_output=True, text=True).stdout


def test_sweep_regex_class_left_behind(tmp_path):
    _repo(tmp_path, {"m.py": "import re\nP = re.compile(r'^([a-zA-Z]+)([0-9]+)$')\n"})
    (tmp_path / "m.py").write_text("import re\nP = re.compile(r'^([^\\W\\d_]+)([0-9]+)$')\n")
    notes = A.sibling_notes(str(tmp_path), _diff(tmp_path))
    assert len(notes) == 1 and "[0-9] is unchanged" in notes[0]


def test_sweep_same_name_sibling(tmp_path):
    src = ("class A:\n    def _latex(self, p):\n        return 'b^x_{%s}' % 1\n\n"
           "class B:\n    def _latex(self, p):\n        return 'a^x_{%s}' % 1\n")
    _repo(tmp_path, {"m.py": src})
    (tmp_path / "m.py").write_text(src.replace("'b^x_{%s}'", "'{b^x_{%s}}'"))
    notes = A.sibling_notes(str(tmp_path), _diff(tmp_path))
    assert len(notes) == 1 and "m.py:6" in notes[0]


def test_sweep_quiet_on_plain_fix(tmp_path):
    _repo(tmp_path, {"m.py": "def f(x):\n    return x + 1\n", "tests/test_m.py": "def f():\n    pass\n"})
    (tmp_path / "m.py").write_text("def f(x):\n    return x + 2\n")
    assert A.sibling_notes(str(tmp_path), _diff(tmp_path)) == []


def test_stage3_keeps_relative_indent():
    text = "    if a:\n        x = 1\n        y = 2\n    z = 3\n"
    out, _ = tr(text, "  x = 1\n  y = 2\nz = 3", "  x = 1\nw = 9\nz = 3")
    assert out == "    if a:\n        x = 1\n    w = 9\n    z = 3\n"


def test_stage2_no_extra_blank_line():
    out, _ = tr("a  \nb\nc\n", "a\nb", "a\nB\n")
    assert out == "a\nB\nc\n"


def test_crlf_new_text_with_crlf():
    out, _ = tr("a\r\nb\r\n", "a\n", "A\r\n")
    assert out == "A\r\nb\r\n"


def test_sweep_ignores_class_the_change_added(tmp_path):
    _repo(tmp_path, {"m.py": "import re\nR = re.compile(r'[0-9]+')\n"})
    (tmp_path / "m.py").write_text("import re\nR = re.compile(r'[0-9_]+')\n")
    assert A.sibling_notes(str(tmp_path), _diff(tmp_path)) == []


def test_sweep_owner_must_contain_line(tmp_path):
    src = "def helper():\n    pass\n\nclass A:\n    x = 1\n\ndef helper2():\n    pass\n\nclass B:\n    def helper(self):\n        pass\n"
    _repo(tmp_path, {"m.py": src})
    (tmp_path / "m.py").write_text(src.replace("x = 1", "x = 2"))
    assert A.sibling_notes(str(tmp_path), _diff(tmp_path)) == []


def test_valid_arguments_lenient():
    assert A.valid_arguments('{"content": "a\tb"}') == '{"content": "a\tb"}'
    assert A.valid_arguments({"a": 1}) == '{"a": 1}'
    assert A.valid_arguments('{"summary": "half') == "{}"
