from typing import Any

_ts_parser = None


def _get_ts_parser():
    global _ts_parser
    if _ts_parser is None:
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser

        _ts_parser = Parser()
        _ts_parser.set_language(Language(tspython.language(), "python"))
    return _ts_parser


def parse_python(source: str):
    return _get_ts_parser().parse(source.encode()).root_node


def enclosing_node(root: Any, line_num: int, target_type: str):
    idx = line_num - 1
    result = None

    def walk(node: Any) -> None:
        nonlocal result
        if node.start_point[0] <= idx <= node.end_point[0]:
            if node.type == target_type:
                result = node
            for child in node.children:
                walk(child)

    walk(root)
    return result


def node_name(node: Any) -> str:
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode()
    return ""


def node_lines(node: Any) -> tuple[int, int]:
    return node.start_point[0] + 1, node.end_point[0] + 1


def node_text(node: Any) -> str:
    return node.text.decode() if node else ""


def find_children(node: Any, child_type: str) -> list[Any]:
    return [child for child in node.children if child.type == child_type]


def find_first_child(node: Any, child_type: str):
    for child in node.children:
        if child.type == child_type:
            return child
    return None


def line_range(start: int, end: int) -> list[int]:
    return list(range(start, end + 1)) if start <= end else []
