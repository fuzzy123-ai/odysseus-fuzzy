import ast
from pathlib import Path


class _MimeTypesRegistry:
    def __init__(self, initial_types):
        self.types_map = dict(initial_types)

    def add_type(self, media_type, suffix):
        self.types_map[suffix] = media_type


def _load_register_static_mime_types(mime_registry):
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"), filename=str(app_path))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "register_static_mime_types")
    module = ast.Module(body=[fn], type_ignores=[])
    ns = {"mimetypes": mime_registry}
    exec(compile(module, str(app_path), "exec"), ns)
    return ns["register_static_mime_types"]


def test_register_static_mime_types_restores_js_module_types():
    mime_registry = _MimeTypesRegistry({".js": "text/plain"})
    register_static_mime_types = _load_register_static_mime_types(mime_registry)

    register_static_mime_types()

    assert mime_registry.types_map[".js"] == "text/javascript"
    assert mime_registry.types_map[".mjs"] == "application/javascript"
