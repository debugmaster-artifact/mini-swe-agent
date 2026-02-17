import textwrap

from debugmaster.agents.llm_ide.code_context_manager import CodeChunk, CodeContextManager


SAMPLE_SHORT_FUNC = textwrap.dedent("""\
class MyClass:
    def short_method(self, x):
        a = x + 1
        b = a * 2
        return b

    def other_method(self):
        pass
""")

SAMPLE_LONG_FUNC = textwrap.dedent(
    "def long_func(x):\n" + "".join(f"    line_{i} = {i}\n" for i in range(120)) + "    return x\n"
)

SAMPLE_NO_FUNC = textwrap.dedent("""\
import os
import sys

x = 1
y = 2
z = x + y
print(z)
""")


def _make_manager(content: str) -> CodeContextManager:
    return CodeContextManager(get_file_fn=lambda _path: content)


class TestTreeSitterParsing:
    def test_parse_and_find_short_function(self):
        mgr = _make_manager(SAMPLE_SHORT_FUNC)
        chunk = mgr.get_nearby_code_context("test.py", 3)
        assert chunk.function == "short_method"
        assert chunk.whole_function

    def test_parse_long_function(self):
        mgr = _make_manager(SAMPLE_LONG_FUNC)
        chunk = mgr.get_nearby_code_context("test.py", 60)
        assert not chunk.whole_function
        assert 60 in chunk.lines


class TestFunctionSizeHandling:
    def test_short_function_whole(self):
        mgr = _make_manager(SAMPLE_SHORT_FUNC)
        chunk = mgr.get_nearby_code_context("test.py", 3)
        assert chunk.whole_function
        assert chunk.function == "short_method"
        assert chunk.class_name == "MyClass"
        assert 2 in chunk.lines and 5 in chunk.lines

    def test_long_function_window(self):
        mgr = _make_manager(SAMPLE_LONG_FUNC)
        chunk = mgr.get_nearby_code_context("test.py", 60)
        assert not chunk.whole_function
        assert 60 in chunk.lines
        assert 35 in chunk.lines and 85 in chunk.lines
        assert 1 not in chunk.lines

    def test_no_function_window(self):
        mgr = _make_manager(SAMPLE_NO_FUNC)
        chunk = mgr.get_nearby_code_context("test.py", 4)
        assert not chunk.whole_function
        assert chunk.function == ""
        assert 1 in chunk.lines and 7 in chunk.lines


class TestRender:
    def test_render_aggregates_chunks_once(self):
        mgr = _make_manager(SAMPLE_SHORT_FUNC)
        chunk1 = mgr.get_nearby_code_context("test.py", 3)
        chunk2 = mgr.get_nearby_code_context("test.py", 4)
        rendered = mgr.render([chunk1, chunk2])
        assert "## File: `test.py`" in rendered
        assert "def short_method" in rendered

    def test_render_adds_block_lines_for_partial_chunk(self):
        mgr = _make_manager(
            textwrap.dedent(
                """\
                def f(x):
                    if x > 0:
                        y = x + 1
                    return y
                """
            )
        )
        rendered = mgr.render(
            [CodeChunk(file_path="test.py", class_name="", function="f", whole_function=False, lines=[3])],
        )
        assert "if x > 0:" in rendered

    def test_render_handles_decorated_and_match_blocks(self):
        mgr = _make_manager(
            textwrap.dedent(
                """\
                class C:
                    @staticmethod
                    def f(x):
                        match x:
                            case 1:
                                return 1
                            case _:
                                return 0
                """
            )
        )
        rendered = mgr.render(
            [CodeChunk(file_path="test.py", class_name="C", function="f", whole_function=False, lines=[6])],
        )
        assert "@staticmethod" in rendered
        assert "match x:" in rendered
        assert "case 1:" in rendered


class TestGetCodeLines:
    def test_range_within_file(self):
        mgr = _make_manager(SAMPLE_SHORT_FUNC)
        chunk = mgr.get_code_lines("test.py", 2, 5)
        assert chunk.lines == [2, 3, 4, 5]
        assert not chunk.eof
        assert not chunk.whole_function
        assert chunk.class_name == ""
        assert chunk.function == ""

    def test_end_exceeds_file_length(self):
        mgr = _make_manager(SAMPLE_NO_FUNC)
        total = len(SAMPLE_NO_FUNC.splitlines())
        chunk = mgr.get_code_lines("test.py", 3, total + 10)
        assert chunk.lines == list(range(3, total + 1))
        assert chunk.eof

    def test_eof_marker_in_rendered_output(self):
        mgr = _make_manager(SAMPLE_NO_FUNC)
        total = len(SAMPLE_NO_FUNC.splitlines())
        chunk = mgr.get_code_lines("test.py", 1, total + 5)
        rendered = mgr.render([chunk])
        assert "[EOF]" in rendered

    def test_no_eof_marker_when_within_bounds(self):
        mgr = _make_manager(SAMPLE_SHORT_FUNC)
        chunk = mgr.get_code_lines("test.py", 1, 3)
        rendered = mgr.render([chunk])
        assert "[EOF]" not in rendered
