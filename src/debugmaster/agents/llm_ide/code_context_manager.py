from dataclasses import dataclass
from typing import Callable

from debugmaster.agents.llm_ide import ts_utils


@dataclass
class CodeChunk:
    file_path: str
    class_name: str
    function: str
    whole_function: bool
    lines: list[int]
    eof: bool = False


class CodeContextManager:
    def __init__(self, get_file_fn: Callable[[str], str], cwd: str = ""):
        self.get_file_fn = get_file_fn
        self.cwd = cwd
        self._file_cache: dict[str, str] = {}
        self._parse_cache: dict[str, object] = {}

    def _resolve_path(self, file_path: str) -> str:
        if self.cwd and not file_path.startswith("/"):
            return f"{self.cwd}/{file_path}"
        return file_path

    def _read_file(self, file_path: str) -> str:
        if file_path not in self._file_cache:
            self._file_cache[file_path] = self.get_file_fn(file_path)
        return self._file_cache[file_path]

    def _parse_file(self, file_path: str) -> object:
        content = self._read_file(file_path)
        if file_path not in self._parse_cache:
            self._parse_cache[file_path] = ts_utils.parse_python(content)
        return self._parse_cache[file_path]

    def get_nearby_code_context(self, file_path: str, line_number: int, window_size: int = 100) -> CodeChunk:
        """Core tool: loads code surrounding a line into the code context (agent-facing)."""
        read_path = self._resolve_path(file_path)
        content = self._read_file(read_path)
        if not content:
            return CodeChunk(file_path=file_path, class_name="", function="", whole_function=False, lines=[])
        total_lines = len(content.splitlines())
        root = self._parse_file(read_path)

        func_node = ts_utils.enclosing_node(root, line_number, "function_definition")
        class_node = ts_utils.enclosing_node(root, line_number, "class_definition")
        class_name = ts_utils.node_name(class_node) if class_node else ""
        func_name = ts_utils.node_name(func_node) if func_node else ""

        if not func_node:
            whole_function = False
            lines = sorted(range(max(1, line_number - window_size // 2), min(total_lines, line_number + window_size // 2) + 1))
        else:
            func_start, func_end = ts_utils.node_lines(func_node)
            func_len = func_end - func_start + 1
            if func_len <= window_size:
                whole_function = True
                lines = sorted(range(func_start, func_end + 1))
            else:
                whole_function = False
                win_start = max(func_start, line_number - window_size // 2)
                win_end = min(func_end, line_number + window_size // 2)
                lines = sorted(range(win_start, win_end + 1))

        return CodeChunk(
            file_path=file_path,
            class_name=class_name,
            function=func_name,
            whole_function=whole_function,
            lines=lines,
        )

    def get_code_lines(self, file_path: str, start: int, end: int) -> CodeChunk:
        """Core tool: loads an explicit line range into the code context (agent-facing)."""
        read_path = self._resolve_path(file_path)
        content = self._read_file(read_path)
        if not content:
            return CodeChunk(file_path=file_path, class_name="", function="", whole_function=False, lines=[])
        total = len(content.splitlines())
        clamped_end = min(end, total)
        eof = end > total
        lines = list(range(max(1, start), clamped_end + 1))
        return CodeChunk(
            file_path=file_path, class_name="", function="",
            whole_function=False, lines=lines, eof=eof,
        )

    def render(self, chunks: list[CodeChunk]) -> str:
        merged = self._merge_chunks(chunks)
        if not merged:
            return ""

        files: dict[str, list[CodeChunk]] = {}
        for chunk in merged:
            files.setdefault(chunk.file_path, []).append(chunk)

        sections = []
        for file_path, file_chunks in files.items():
            full_path = self._resolve_path(file_path)
            content = self._read_file(full_path)
            file_lines = content.splitlines()
            if not file_lines:
                continue
            needed_lines = self._collect_needed_lines(file_chunks, content)
            if not needed_lines:
                continue
            eof = any(c.eof for c in file_chunks)
            rendered = self._render_lines(file_lines, sorted(needed_lines), eof=eof)
            sections.append(f"## File: `{file_path}`\n{rendered}")
        return "\n\n".join(sections)

    def _merge_chunks(self, chunks: list[CodeChunk]) -> list[CodeChunk]:
        by_key: dict[tuple[str, str, str], CodeChunk] = {}
        for chunk in chunks:
            key = (chunk.file_path, chunk.class_name, chunk.function)
            if key not in by_key:
                by_key[key] = CodeChunk(
                    file_path=chunk.file_path,
                    class_name=chunk.class_name,
                    function=chunk.function,
                    whole_function=chunk.whole_function,
                    lines=sorted(set(chunk.lines)),
                    eof=chunk.eof,
                )
                continue
            existing = by_key[key]
            existing.whole_function = existing.whole_function or chunk.whole_function
            existing.eof = existing.eof or chunk.eof
            existing.lines = sorted(set(existing.lines + chunk.lines))
        return list(by_key.values())

    def _collect_needed_lines(self, chunks: list[CodeChunk], content: str) -> set[int]:
        needed: set[int] = set()
        root = self._parse_file_content(content)

        sig_map, range_map = self._build_signature_map(root)
        block_map = self._build_block_parents(root)

        for chunk in chunks:
            needed.update(self._get_signature_lines(sig_map, chunk.class_name, chunk.function))
            if chunk.whole_function:
                needed.update(self._get_function_range(range_map, chunk.class_name, chunk.function))
                continue
            needed.update(chunk.lines)
            for line in chunk.lines:
                needed.update(self._get_block_declaration_lines(block_map, line))
        return needed

    def _parse_file_content(self, content: str) -> object:
        """Parse content directly (for render paths where we already have content)."""
        return ts_utils.parse_python(content)

    def _build_signature_map(
        self,
        root,
    ) -> tuple[dict[tuple[str, str], list[int]], dict[tuple[str, str], set[int]]]:
        signatures: dict[tuple[str, str], list[int]] = {}
        ranges: dict[tuple[str, str], set[int]] = {}

        def add_signature(class_name: str, function_name: str, node, decorator_start: int | None = None) -> None:
            body = ts_utils.find_first_child(node, "block")
            start = node.start_point[0] + 1
            if decorator_start:
                start = min(start, decorator_start)
            end = body.start_point[0] if body else node.end_point[0] + 1
            signatures[(class_name, function_name)] = ts_utils.line_range(start, max(start, end))
            ranges[(class_name, function_name)] = set(ts_utils.line_range(node.start_point[0] + 1, node.end_point[0] + 1))

        def walk(node, class_name: str = "", decorator_start: int | None = None) -> None:
            if node.type == "decorated_definition":
                decorators = ts_utils.find_children(node, "decorator")
                inherited_start = decorator_start
                if decorators:
                    inherited_start = min(deco.start_point[0] + 1 for deco in decorators)
                for child in node.children:
                    if child.type != "decorator":
                        walk(child, class_name, inherited_start)
                return

            if node.type == "class_definition":
                name_node = node.child_by_field_name("name")
                class_text = ts_utils.node_text(name_node)
                body = ts_utils.find_first_child(node, "block")
                start = node.start_point[0] + 1
                if decorator_start:
                    start = min(start, decorator_start)
                end = body.start_point[0] if body else node.end_point[0] + 1
                signatures[("", class_text)] = ts_utils.line_range(start, max(start, end))
                signatures[(class_text, "")] = ts_utils.line_range(start, max(start, end))
                if body:
                    for child in body.children:
                        walk(child, class_text)
                return

            if node.type == "function_definition":
                add_signature(class_name, ts_utils.node_text(node.child_by_field_name("name")), node, decorator_start)
                return

            for child in node.children:
                walk(child, class_name)

        walk(root)
        return signatures, ranges

    def _get_signature_lines(self, sig_map: dict[tuple[str, str], list[int]], class_name: str, function: str) -> list[int]:
        lines: list[int] = []
        if class_name:
            lines.extend(sig_map.get((class_name, ""), []))
        if function:
            lines.extend(sig_map.get((class_name, function), []))
        elif class_name:
            lines.extend(sig_map.get(("", class_name), []))
        return lines

    def _get_function_range(
        self,
        range_map: dict[tuple[str, str], set[int]],
        class_name: str,
        function: str,
    ) -> set[int]:
        return range_map.get((class_name, function), set())

    def _build_block_parents(self, root) -> dict[int, set[int]]:
        statement_blocks = {
            "if_statement",
            "for_statement",
            "while_statement",
            "with_statement",
            "try_statement",
            "match_statement",
        }
        clause_blocks = {
            "elif_clause",
            "else_clause",
            "except_clause",
            "finally_clause",
            "case_clause",
        }
        parents: dict[int, set[int]] = {}

        def collect_declarations(node) -> list[int]:
            decls: list[int] = []
            if node.type in statement_blocks or node.type in clause_blocks:
                decls.append(node.start_point[0] + 1)
            for child in node.children:
                if child.type in clause_blocks:
                    decls.extend(collect_declarations(child))
                elif child.type == "block":
                    for grandchild in child.children:
                        if grandchild.type in clause_blocks:
                            decls.extend(collect_declarations(grandchild))
            return decls

        def walk(node, enclosing: set[int]) -> None:
            local = set(enclosing)
            if node.type in statement_blocks:
                local.update(collect_declarations(node))
                for ln in ts_utils.line_range(node.start_point[0] + 1, node.end_point[0] + 1):
                    parents.setdefault(ln, set()).update(local)
            for child in node.children:
                walk(child, local)

        walk(root, set())
        return parents

    def _get_block_declaration_lines(self, block_map: dict[int, set[int]], line: int) -> set[int]:
        return block_map.get(line, set())

    def _render_lines(self, file_lines: list[str], line_numbers: list[int], *, eof: bool = False) -> str:
        if not line_numbers:
            return ""
        width = len(str(max(line_numbers))) + 1
        parts = []
        prev_line = None
        for line_number in line_numbers:
            if line_number < 1 or line_number > len(file_lines):
                continue
            if prev_line is not None and line_number > prev_line + 1:
                parts.append("...")
            parts.append(f"{line_number:>{width}} {file_lines[line_number - 1]}")
            prev_line = line_number
        if eof:
            parts.append("  [EOF]")
        return "\n".join(parts)
