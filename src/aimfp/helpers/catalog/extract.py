"""
AIMFP Helper Functions - Source Entity Extraction

Pure extraction of function and type signatures from source text. No I/O: callers
pass file content in and get immutable records back.

Two fidelity levels, deliberately:

- **Python** uses the stdlib ``ast`` module and yields full signatures — parameter
  names with annotations and defaults, return annotation, docstring-derived purpose,
  line number, and a purity hint.
- **Every other language** falls back to the regex ``FUNCTION_PATTERNS`` already
  maintained in ``watchdog/config.py`` and yields names and line numbers only.
  Parameters and returns come back empty for the AI to fill in.

The asymmetry is honest rather than unfortunate: a regex that reliably parses
parameter lists across JS/TS/Rust/Go does not exist, and pretending otherwise would
put wrong signatures in the database, which is worse than absent ones.

Extraction never raises on malformed input. A file that fails to parse comes back as
an ``ExtractedEntities`` carrying ``parse_error``; the caller decides whether to skip
it or register the file with zero entities.
"""

import ast
import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from ...database.connection import decode_json_layers
from ...watchdog.config import get_function_pattern


# Languages for which full signature extraction is available.
FULL_EXTRACTION_LANGUAGES: Tuple[str, ...] = ('python',)

# Base classes that identify a class as a data type rather than behavior.
_TYPE_BASE_KINDS: Dict[str, str] = {
    'Enum': 'enum',
    'IntEnum': 'enum',
    'StrEnum': 'enum',
    'Flag': 'enum',
    'IntFlag': 'enum',
    'TypedDict': 'typed_dict',
    'NamedTuple': 'named_tuple',
    'Protocol': 'protocol',
}


# ============================================================================
# Immutable Records
# ============================================================================

@dataclass(frozen=True)
class ExtractedFunction:
    """A function signature recovered from source text."""
    name: str
    line: int
    purpose: Optional[str] = None
    parameters: Tuple[Dict[str, Any], ...] = ()
    returns: Optional[Dict[str, Any]] = None
    is_async: bool = False
    is_private: bool = False
    is_effect: bool = False
    is_nested: bool = False
    body_hash: Optional[str] = None


@dataclass(frozen=True)
class ExtractedType:
    """A type definition recovered from source text."""
    name: str
    line: int
    kind: str
    definition: Dict[str, Any] = field(default_factory=dict)
    description: Optional[str] = None
    is_private: bool = False


@dataclass(frozen=True)
class ExtractedEntities:
    """Everything recovered from a single source file."""
    functions: Tuple[ExtractedFunction, ...] = ()
    types: Tuple[ExtractedType, ...] = ()
    module_docstring: Optional[str] = None
    fidelity: str = 'none'
    parse_error: Optional[str] = None


# ============================================================================
# Pure Helpers
# ============================================================================

def annotation_text(node: Optional[ast.AST]) -> Optional[str]:
    """
    Pure: Render an annotation node back to source text.

    Args:
        node: Annotation AST node, or None when unannotated

    Returns:
        Source text of the annotation, or None when absent or unrenderable
    """
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def docstring_purpose(docstring: Optional[str]) -> Optional[str]:
    """
    Pure: Reduce a docstring to a one-line purpose.

    Takes the first paragraph (up to a blank line), collapses internal whitespace,
    and drops a leading ``Pure:`` / ``Effect:`` marker only when more text follows,
    so the AIMFP convention does not consume the whole summary.

    Args:
        docstring: Raw docstring, or None

    Returns:
        Single-line purpose, or None when the docstring is absent or empty
    """
    if not docstring:
        return None

    paragraph = docstring.strip().split('\n\n', 1)[0]
    collapsed = ' '.join(paragraph.split())
    if not collapsed:
        return None

    for marker in ('Pure:', 'Effect:'):
        if collapsed.startswith(marker):
            remainder = collapsed[len(marker):].strip()
            if remainder:
                return remainder
    return collapsed


def parameter_records(args: ast.arguments) -> Tuple[Dict[str, Any], ...]:
    """
    Pure: Build parameter records from an AST argument list.

    Covers positional-only, positional, ``*args``, keyword-only, and ``**kwargs``
    forms. Defaults are aligned to their parameters from the right, since Python
    packs them that way.

    Args:
        args: The ``args`` node of a function definition

    Returns:
        Tuple of {name, type, default, kind} dicts in declaration order
    """
    records: list = []

    positional = list(args.posonlyargs) + list(args.args)
    default_offset = len(positional) - len(args.defaults)

    for index, arg in enumerate(positional):
        default_index = index - default_offset
        default = (
            annotation_text(args.defaults[default_index])
            if default_index >= 0 else None
        )
        records.append({
            'name': arg.arg,
            'type': annotation_text(arg.annotation),
            'default': default,
            'kind': 'positional_only' if index < len(args.posonlyargs) else 'positional',
        })

    if args.vararg is not None:
        records.append({
            'name': f'*{args.vararg.arg}',
            'type': annotation_text(args.vararg.annotation),
            'default': None,
            'kind': 'var_positional',
        })

    for arg, default_node in zip(args.kwonlyargs, args.kw_defaults):
        records.append({
            'name': arg.arg,
            'type': annotation_text(arg.annotation),
            'default': annotation_text(default_node),
            'kind': 'keyword_only',
        })

    if args.kwarg is not None:
        records.append({
            'name': f'**{args.kwarg.arg}',
            'type': annotation_text(args.kwarg.annotation),
            'default': None,
            'kind': 'var_keyword',
        })

    return tuple(records)


def is_effect_function(name: str, docstring: Optional[str]) -> bool:
    """
    Pure: Decide whether a function is an effect boundary.

    Reads declared intent only — the AIMFP effect naming convention and the
    ``Effect:`` docstring marker. Deliberately does not inspect the body: guessing
    purity from call sites produces false confidence, and this value is a hint for
    the AI, never a compliance verdict.

    Both the prefix form (``_effect_read_file``) and the suffix form
    (``_reserve_file_effect``) are recognized, since real AIMFP codebases use both.

    Args:
        name: Function name
        docstring: Raw docstring, or None

    Returns:
        True when the function declares itself effect-bearing
    """
    if '_effect_' in name or name.endswith('_effect'):
        return True
    return bool(docstring) and docstring.lstrip().startswith('Effect:')


def class_type_kind(node: ast.ClassDef) -> Optional[str]:
    """
    Pure: Classify a class as a data type, or reject it as behavior.

    Recognizes dataclasses (frozen or not) and the standard typing base classes.
    A plain class with methods is not a type and returns None — AIMFP forbids OOP,
    so such a class is a finding for the caller, not something to catalog.

    Args:
        node: Class definition node

    Returns:
        Type kind string, or None when the class is not a data type
    """
    for base in node.bases:
        base_name = base.id if isinstance(base, ast.Name) else (
            base.attr if isinstance(base, ast.Attribute) else None
        )
        if base_name in _TYPE_BASE_KINDS:
            return _TYPE_BASE_KINDS[base_name]

    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = target.id if isinstance(target, ast.Name) else (
            target.attr if isinstance(target, ast.Attribute) else None
        )
        if name == 'dataclass':
            return 'frozen_dataclass' if _has_frozen_keyword(decorator) else 'dataclass'

    return None


def _has_frozen_keyword(decorator: ast.AST) -> bool:
    """
    Pure: Detect ``frozen=True`` on a dataclass decorator.

    Args:
        decorator: Decorator node, either a bare name or a call

    Returns:
        True when the decorator explicitly sets frozen=True
    """
    if not isinstance(decorator, ast.Call):
        return False
    for keyword in decorator.keywords:
        if keyword.arg == 'frozen' and isinstance(keyword.value, ast.Constant):
            return bool(keyword.value.value)
    return False


def class_field_records(node: ast.ClassDef) -> Tuple[Dict[str, Any], ...]:
    """
    Pure: Extract annotated fields and enum variants from a class body.

    Annotated assignments become {name, type, default} field records. Bare
    assignments become variant records, which is how Enum members appear.

    Args:
        node: Class definition node

    Returns:
        Tuple of field/variant records in declaration order
    """
    records: list = []

    for statement in node.body:
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            records.append({
                'name': statement.target.id,
                'type': annotation_text(statement.annotation),
                'default': annotation_text(statement.value),
            })
        elif isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    records.append({
                        'name': target.id,
                        'value': annotation_text(statement.value),
                    })

    return tuple(records)


def function_body_hash(node: ast.AST) -> str:
    """
    Pure: A short, stable hash of a function's code.

    Built from the AST, so formatting and comments never change it, and the
    docstring is left out, so editing the description in the code does not
    either. The name is left out too: a rename alone does not make a
    description wrong. Signature, decorators and body all count.

    Args:
        node: A FunctionDef or AsyncFunctionDef node

    Returns:
        16 hex characters of a SHA-256 over the normalized code
    """
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], 'value', None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    parts = (
        [type(node).__name__, ast.dump(node.args)]
        + [ast.dump(node.returns) if node.returns is not None else '']
        + [ast.dump(d) for d in node.decorator_list]
        + [ast.dump(statement) for statement in body]
    )
    return hashlib.sha256('\n'.join(parts).encode('utf-8')).hexdigest()[:16]


def python_function_hashes(source: str) -> Dict[str, str]:
    """
    Pure: Body hash of every top-level function in Python source.

    Args:
        source: Full text of a Python file

    Returns:
        Dict of function name -> function_body_hash; empty when the source
        does not parse
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    return {
        node.name: function_body_hash(node)
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def bare_parameter_name(name: Any) -> str:
    """Pure: A parameter name without '*' / '**' and surrounding space."""
    return str(name).strip().lstrip('*').strip()


def recorded_parameter_facts(recorded: Any) -> Optional[Tuple[Tuple[str, Optional[bool]], ...]]:
    """
    Pure: (name, required) for each recorded parameter; required is None when
    the record does not say.

    Recorded parameters come in several shapes. A dict with 'required' says so
    directly. A dict with 'kind' is a catalog record, where a null 'default'
    means no default. Any other dict with a 'default' key has a default (null
    meaning None). A string 'name: type = value' has a default when it has '='.
    A '*' / '**' parameter is never required.

    Args:
        recorded: Decoded functions.parameters value

    Returns:
        Tuple of facts, or None when the value is not a list of parameters
    """
    if not isinstance(recorded, list):
        return None
    facts: list = []
    for item in recorded:
        if isinstance(item, dict) and item.get('name'):
            name = bare_parameter_name(item['name'])
            if str(item['name']).lstrip().startswith('*') or item.get('kind') in ('var_positional', 'var_keyword'):
                required: Optional[bool] = False
            elif isinstance(item.get('required'), bool):
                required = item['required']
            elif 'kind' in item:
                required = item.get('default') is None
            elif 'default' in item:
                required = False
            else:
                required = None
            facts.append((name, required))
        elif isinstance(item, str) and item.strip():
            head = item.split('=', 1)[0].split(':', 1)[0]
            facts.append((bare_parameter_name(head), False if '=' in item or head.lstrip().startswith('*') else None))
        else:
            return None
    return tuple(facts)


def parameter_kind_of(name: Any, kind: Any = None) -> Optional[str]:
    """Pure: The recorded kind, else 'var_positional' / 'var_keyword' read off a '*' / '**' name."""
    if kind:
        return str(kind)
    text = str(name).strip()
    if text.startswith('**'):
        return 'var_keyword'
    if text.startswith('*'):
        return 'var_positional'
    return None


def parse_parameter_text(text: str) -> Dict[str, Any]:
    """Pure: {name, type, default} from a 'name: type = value' string (missing parts None)."""
    head, has_default, default = text.partition('=')
    name, _, type_text = head.partition(':')
    return {'name': name, 'type': type_text.strip() or None,
            'default': default.strip() if has_default else None}


def normalize_recorded_parameters(recorded: Any) -> Any:
    """
    Pure: Recorded parameters in one shape, whatever shape they were stored in.

    Every parameter becomes {name, type, required, default, description, kind}:
    name without '*' (kind says var_positional / var_keyword), required as
    recorded_parameter_facts reads it (None when the record does not say),
    default as recorded (a 'name: type = value' string gives 'value'), other
    fields None when absent. JSON wrapped in JSON strings is decoded first.

    Args:
        recorded: Raw functions.parameters value (JSON text, any layers deep, or decoded)

    Returns:
        List of parameter dicts; None for no record; the decoded value
        unchanged when it is not a list of parameters (nothing is dropped)
    """
    decoded = decode_json_layers(recorded)
    facts = recorded_parameter_facts(decoded)
    if facts is None:
        return decoded

    return [
        {
            'name': name,
            'type': fields.get('type'),
            'required': required,
            'default': fields.get('default'),
            'description': fields.get('description'),
            'kind': parameter_kind_of(fields.get('name'), fields.get('kind')),
        }
        for fields, (name, required) in zip(
            (item if isinstance(item, dict) else parse_parameter_text(item) for item in decoded), facts)
    ]


def source_parameter_facts(parameters: Tuple[Dict[str, Any], ...]) -> Tuple[Tuple[str, bool], ...]:
    """Pure: (name, required) for each parameter_records entry."""
    return tuple(
        (bare_parameter_name(p['name']),
         p.get('default') is None and p.get('kind') not in ('var_positional', 'var_keyword'))
        for p in parameters
    )


def parameter_mismatches(recorded: Any, parameters: Tuple[Dict[str, Any], ...]) -> Tuple[str, ...]:
    """
    Pure: How recorded parameters disagree with the code's signature.

    Compares names, their order, and — where the record says — whether each
    parameter is required (the code gives no default). Empty when they agree.

    Args:
        recorded: Decoded functions.parameters value
        parameters: parameter_records of the function in the source

    Returns:
        One message per disagreement
    """
    facts = recorded_parameter_facts(recorded)
    if facts is None:
        return ("recorded parameters are not a list of parameters",)
    code = source_parameter_facts(parameters)
    recorded_names = tuple(name for name, _ in facts)
    code_names = tuple(name for name, _ in code)
    problems: list = []
    absent = tuple(n for n in code_names if n not in recorded_names)
    extra = tuple(n for n in recorded_names if n not in code_names)
    if absent:
        problems.append(f"not recorded: {', '.join(absent)}")
    if extra:
        problems.append(f"recorded but not in the code: {', '.join(extra)}")
    shared = tuple(n for n in code_names if n in recorded_names)
    if not absent and not extra and recorded_names != code_names:
        problems.append(f"order differs: code ({', '.join(code_names)}), recorded ({', '.join(recorded_names)})")
    elif shared != tuple(n for n in recorded_names if n in code_names):
        problems.append("order differs among shared parameters")
    required_in_code = dict(code)
    for name, required in facts:
        if required is not None and name in required_in_code and required != required_in_code[name]:
            problems.append(
                f"'{name}' recorded as {'required' if required else 'optional'} but the code "
                f"{'gives no default' if required_in_code[name] else 'gives a default'}"
            )
    return tuple(problems)


# ============================================================================
# Python Extraction (full fidelity)
# ============================================================================

def extract_python_functions(tree: ast.Module) -> Tuple[ExtractedFunction, ...]:
    """
    Pure: Extract every function definition from a parsed Python module.

    Walks nested definitions too, flagging them via ``is_nested`` so callers can
    catalog module-level functions only if they prefer.

    Args:
        tree: Parsed module AST

    Returns:
        Tuple of ExtractedFunction records in source order
    """
    top_level = {id(node) for node in tree.body}
    functions: list = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        docstring = ast.get_docstring(node)
        return_annotation = annotation_text(node.returns)

        functions.append(ExtractedFunction(
            name=node.name,
            line=node.lineno,
            purpose=docstring_purpose(docstring),
            parameters=parameter_records(node.args),
            returns={'type': return_annotation} if return_annotation else None,
            is_async=isinstance(node, ast.AsyncFunctionDef),
            is_private=node.name.startswith('_'),
            is_effect=is_effect_function(node.name, docstring),
            is_nested=id(node) not in top_level,
            body_hash=function_body_hash(node),
        ))

    return tuple(sorted(functions, key=lambda f: f.line))


def extract_python_types(tree: ast.Module) -> Tuple[ExtractedType, ...]:
    """
    Pure: Extract data-type definitions from a parsed Python module.

    Recognizes dataclasses and typing base classes via class_type_kind. Classes that
    are neither are skipped rather than cataloged as types.

    Args:
        tree: Parsed module AST

    Returns:
        Tuple of ExtractedType records in source order
    """
    types: list = []

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue

        kind = class_type_kind(node)
        if kind is None:
            continue

        fields = class_field_records(node)
        key = 'variants' if kind == 'enum' else 'fields'

        types.append(ExtractedType(
            name=node.name,
            line=node.lineno,
            kind=kind,
            definition={'kind': kind, key: list(fields)},
            description=docstring_purpose(ast.get_docstring(node)),
            is_private=node.name.startswith('_'),
        ))

    return tuple(types)


def extract_python(source: str) -> ExtractedEntities:
    """
    Pure: Extract functions and types from Python source text.

    Args:
        source: Full text of a Python file

    Returns:
        ExtractedEntities with fidelity 'full', or fidelity 'none' plus parse_error
        when the source does not parse
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ExtractedEntities(
            fidelity='none',
            parse_error=f"SyntaxError at line {exc.lineno}: {exc.msg}",
        )

    return ExtractedEntities(
        functions=extract_python_functions(tree),
        types=extract_python_types(tree),
        module_docstring=docstring_purpose(ast.get_docstring(tree)),
        fidelity='full',
    )


# ============================================================================
# Pattern Extraction (names only)
# ============================================================================

def extract_by_pattern(source: str, language: str) -> ExtractedEntities:
    """
    Pure: Extract function names from source using the language's regex pattern.

    Reuses FUNCTION_PATTERNS from watchdog/config.py rather than maintaining a
    second pattern registry. Yields names and line numbers only; parameters and
    returns are left empty for the AI to supply.

    Args:
        source: Full text of a source file
        language: Language key (e.g. 'typescript', 'rust', 'go')

    Returns:
        ExtractedEntities with fidelity 'names_only', or 'none' when the language
        has no registered pattern
    """
    pattern = get_function_pattern(language)
    if pattern is None:
        return ExtractedEntities(fidelity='none')

    functions: list = []
    for line_number, line in enumerate(source.splitlines(), start=1):
        for match in pattern.finditer(line):
            for group in match.groups():
                if group:
                    functions.append(ExtractedFunction(
                        name=group,
                        line=line_number,
                        is_private=group.startswith('_'),
                    ))

    return ExtractedEntities(functions=tuple(functions), fidelity='names_only')


def extract_entities(source: str, language: Optional[str]) -> ExtractedEntities:
    """
    Pure: Extract entities from source, choosing fidelity by language.

    Args:
        source: Full text of a source file
        language: Detected language key, or None when unrecognized

    Returns:
        ExtractedEntities at the best fidelity available for the language
    """
    if not language:
        return ExtractedEntities(fidelity='none')

    normalized = language.strip().lower()
    if normalized in FULL_EXTRACTION_LANGUAGES:
        return extract_python(source)

    return extract_by_pattern(source, normalized)
