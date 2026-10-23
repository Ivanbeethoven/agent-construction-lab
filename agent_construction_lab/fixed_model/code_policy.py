"""Conservative AST policy for public AppWorld APIs and ordinary data.

Validation precedes execution of the entire block. This limits accidental host
access; it is not an operating-system sandbox. The process boundary provides
deadlines and credential isolation, not protection from a compromised package.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass


class CodePolicyError(ValueError):
    pass


@dataclass(frozen=True)
class CodePolicyState:
    data_names: frozenset = frozenset()
    imports: tuple = ()
    functions: frozenset = frozenset()


_BUILTINS = set("abs all any bool bytes chr dict divmod enumerate filter float format frozenset hex int isinstance issubclass len list max min next oct ord pow print range repr reversed round set slice sorted str sum tuple zip".split()) - {"filter", "format", "issubclass"}
_ERRORS = set("Exception ValueError TypeError KeyError IndexError ZeroDivisionError ArithmeticError AssertionError RuntimeError StopIteration".split())
_TYPES = {"str", "int", "float", "bool", "list", "dict", "tuple", "set", "frozenset", "bytes"}
_FORBIDDEN = set("apis world requester task evaluator evaluation score tracker builtins __builtins__ os sys open eval exec compile globals locals vars getattr setattr delattr type object input help dir breakpoint memoryview".split())
_IMPORTS = {
    "json": set("loads dumps".split()),
    "re": set("compile escape findall finditer fullmatch match search split sub subn ASCII IGNORECASE MULTILINE DOTALL VERBOSE".split()),
    "math": set("ceil floor trunc fabs factorial gcd lcm isclose isfinite isinf isnan exp expm1 log log1p log2 log10 pow sqrt cbrt sin cos tan asin acos atan atan2 sinh cosh tanh asinh acosh atanh degrees radians hypot dist fsum prod remainder fmod modf frexp ldexp copysign comb perm pi e tau inf nan".split()),
    "datetime": set("date datetime time timedelta timezone MINYEAR MAXYEAR".split()),
    "collections": {"Counter", "defaultdict", "deque", "OrderedDict"},
    "decimal": {"Decimal", "ROUND_HALF_UP", "ROUND_HALF_EVEN", "ROUND_DOWN", "ROUND_UP"},
    "itertools": set("chain combinations combinations_with_replacement permutations product repeat islice count cycle accumulate".split()),
    "functools": set(),
    "statistics": set("mean fmean geometric_mean harmonic_mean median median_low median_high median_grouped mode multimode pstdev pvariance stdev variance quantiles".split()),
    "random": set("seed random randint randrange uniform choice choices sample shuffle getrandbits triangular".split()),
}
_CONSTANTS = {"pi", "e", "tau", "inf", "nan", "ASCII", "IGNORECASE", "MULTILINE", "DOTALL", "VERBOSE", "MINYEAR", "MAXYEAR", "ROUND_HALF_UP", "ROUND_HALF_EVEN", "ROUND_DOWN", "ROUND_UP"}
_METHODS = set("append extend insert pop remove clear copy count index sort reverse add update discard difference intersection union symmetric_difference issubset issuperset isdisjoint keys values items get setdefault popitem lower upper title casefold capitalize swapcase strip lstrip rstrip split rsplit splitlines join startswith endswith replace find rfind isalpha isdigit isnumeric isdecimal isalnum isspace islower isupper istitle zfill ljust rjust center encode decode partition rpartition removeprefix removesuffix isoformat strftime strptime fromisoformat date time replace total_seconds weekday isoweekday isocalendar timestamp quantize normalize as_tuple sqrt exp ln log10 to_integral_value to_integral_exact findall finditer match fullmatch search group groups groupdict start end span sub subn elements most_common subtract rotate appendleft extendleft popleft".split())
_FIELDS = set("year month day hour minute second microsecond days seconds real imag numerator denominator".split())


def _fail(message):
    raise CodePolicyError(message)


def _clean_name(name):
    if not isinstance(name, str) or not name.isidentifier() or name.startswith("_") or name in _FORBIDDEN:
        _fail("Host, private, or invalid name is not allowed")


class _Scope:
    def __init__(self, state=None):
        self.data = set(state.data_names) if state else set()
        self.imports = dict(state.imports) if state else {}
        self.functions = set(state.functions) if state else set()

    def clone(self):
        return _Scope(self.state())

    def state(self):
        return CodePolicyState(frozenset(self.data), tuple(sorted(self.imports.items())), frozenset(self.functions))

    def bind(self, name):
        _clean_name(name)
        if name in _BUILTINS | _ERRORS | self.functions or name in self.imports:
            _fail("Callable or imported names cannot be shadowed")
        self.data.add(name)


class _Validator:
    def __init__(self):
        self.function_depth = 0

    def target(self, node, scope):
        if isinstance(node, ast.Name):
            scope.bind(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for item in node.elts:
                self.target(item, scope)
        elif isinstance(node, ast.Subscript):
            self.expr(node.value, scope)
            self.expr(node.slice, scope)
        else:
            _fail("Assignment target must be local data, never an attribute")

    def imported(self, node, scope):
        """Resolve only statically declared module/member paths."""
        parts = []
        while isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                _fail("Private attributes are forbidden")
            parts.insert(0, node.attr)
            node = node.value
        if not isinstance(node, ast.Name) or node.id not in scope.imports:
            return None
        path = scope.imports[node.id].split(".") + parts
        module = path[0]
        if len(path) > 1 and path[1] not in _IMPORTS[module]:
            _fail("Imported member is outside the public data allowlist")
        if len(path) > 2 and (module != "datetime" or len(path) != 3 or path[2] not in {"strptime", "fromisoformat", "combine", "today", "now", "utcnow", "fromtimestamp", "utcfromtimestamp"}):
            _fail("Nested imported member is not permitted")
        return path

    def call(self, node, scope):
        if any(isinstance(arg, ast.Starred) for arg in node.args) or any(kw.arg is None for kw in node.keywords):
            _fail("Argument unpacking is not allowed")
        function = node.func
        if isinstance(function, ast.Name):
            if function.id in _BUILTINS | _ERRORS | scope.functions:
                pass
            elif function.id in scope.imports and len(scope.imports[function.id].split(".")) == 2:
                self.imported(function, scope)
            else:
                _fail("Only explicit public functions may be called")
            if function.id == "print" and any(kw.arg not in {"sep", "end", "flush"} for kw in node.keywords):
                _fail("print may only write to the captured output")
        elif isinstance(function, ast.Attribute):
            if function.attr.startswith("_"):
                _fail("Private calls are forbidden")
            # Exactly apis.<application>.<public endpoint>, with no requester hops.
            app = function.value
            if isinstance(app, ast.Attribute) and isinstance(app.value, ast.Name) and app.value.id == "apis":
                if app.attr.startswith("_") or app.attr in _FORBIDDEN or function.attr in {"requester", "evaluate"}:
                    _fail("Only public application endpoints are permitted")
            elif self.imported(function, scope):
                pass
            else:
                if function.attr not in _METHODS:
                    _fail("Method is outside the ordinary-data allowlist")
                self.expr(function.value, scope)
        else:
            _fail("Extracted, indexed, or returned callables cannot be invoked")
        for index, argument in enumerate(node.args):
            if isinstance(function, ast.Name) and function.id == "isinstance" and index == 1:
                types = argument.elts if isinstance(argument, ast.Tuple) else [argument]
                if any(not isinstance(item, ast.Name) or item.id not in _TYPES for item in types):
                    _fail("isinstance accepts only built-in data types")
            else:
                self.expr(argument, scope)
        for keyword in node.keywords:
            self.expr(keyword.value, scope)

    def expr(self, node, scope):
        if isinstance(node, ast.Constant):
            return
        if isinstance(node, ast.Name):
            _clean_name(node.id)
            if node.id in scope.data:
                return
            if node.id in scope.imports and scope.imports[node.id].split(".")[-1] in _CONSTANTS:
                return
            _fail("Unknown host data or a callable used as a data value")
        elif isinstance(node, ast.Call):
            self.call(node, scope)
        elif isinstance(node, ast.Attribute):
            path = self.imported(node, scope)
            if path and path[-1] in _CONSTANTS:
                return
            if node.attr not in _FIELDS:
                _fail("Methods cannot be extracted and private attributes are forbidden")
            self.expr(node.value, scope)
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for item in node.elts:
                self.expr(item, scope)
        elif isinstance(node, ast.Dict):
            if any(key is None for key in node.keys):
                _fail("Dictionary unpacking is forbidden")
            for item in node.keys + node.values:
                self.expr(item, scope)
        elif isinstance(node, ast.Subscript):
            self.expr(node.value, scope)
            self.expr(node.slice, scope)
        elif isinstance(node, ast.Slice):
            for item in (node.lower, node.upper, node.step):
                if item is not None:
                    self.expr(item, scope)
        elif isinstance(node, (ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare)):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    self.expr(child, scope)
        elif isinstance(node, ast.IfExp):
            for item in (node.test, node.body, node.orelse):
                self.expr(item, scope)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            local = scope.clone()
            for generator in node.generators:
                if generator.is_async:
                    _fail("Async code is forbidden")
                self.expr(generator.iter, local)
                self.target(generator.target, local)
                for predicate in generator.ifs:
                    self.expr(predicate, local)
            for item in ([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]):
                self.expr(item, local)
        elif isinstance(node, ast.JoinedStr):
            for item in node.values:
                if isinstance(item, ast.FormattedValue):
                    self.expr(item.value, scope)
                    if item.format_spec:
                        self.expr(item.format_spec, scope)
        else:
            _fail(f"Unsupported expression: {type(node).__name__}")

    def block(self, statements, scope, *, top_level=False):
        for node in statements:
            if isinstance(node, ast.Expr):
                self.expr(node.value, scope)
            elif isinstance(node, ast.Assign):
                self.expr(node.value, scope)
                for target in node.targets:
                    self.target(target, scope)
            elif isinstance(node, ast.AugAssign):
                self.expr(node.target, scope)
                self.expr(node.value, scope)
                self.target(node.target, scope)
            elif isinstance(node, ast.If):
                self.expr(node.test, scope)
                left, right = scope.clone(), scope.clone()
                self.block(node.body, left)
                self.block(node.orelse, right)
                scope.data = left.data & right.data
            elif isinstance(node, (ast.For, ast.While)):
                local = scope.clone()
                if isinstance(node, ast.For):
                    self.expr(node.iter, local)
                    self.target(node.target, local)
                else:
                    self.expr(node.test, local)
                self.block(node.body, local)
                self.block(node.orelse, scope.clone())
            elif isinstance(node, ast.Try):
                outcomes = []
                local = scope.clone()
                self.block(node.body, local)
                self.block(node.orelse, local)
                outcomes.append(local)
                for handler in node.handlers:
                    local = scope.clone()
                    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
                    if any(not isinstance(item, ast.Name) or item.id not in _ERRORS for item in types):
                        _fail("Only explicit ordinary exceptions may be handled")
                    if handler.name:
                        local.bind(handler.name)
                    self.block(handler.body, local)
                    if handler.name:
                        local.data.discard(handler.name)
                    outcomes.append(local)
                scope.data = set.intersection(*(item.data for item in outcomes))
                self.block(node.finalbody, scope)
            elif isinstance(node, ast.Assert):
                self.expr(node.test, scope)
                if node.msg:
                    self.expr(node.msg, scope)
            elif isinstance(node, ast.FunctionDef):
                if not top_level or self.function_depth or node.decorator_list or node.returns or node.type_comment:
                    _fail("Only undecorated top-level local helpers are allowed")
                _clean_name(node.name)
                if node.name in scope.data | scope.functions | _BUILTINS | _ERRORS or node.name in scope.imports:
                    _fail("Functions cannot shadow existing names")
                if node.args.vararg or node.args.kwarg or any(arg.annotation for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs):
                    _fail("Dynamic parameters and annotations are forbidden")
                for value in node.args.defaults + [x for x in node.args.kw_defaults if x is not None]:
                    self.expr(value, scope)
                scope.functions.add(node.name)
                local = scope.clone()
                for argument in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                    local.bind(argument.arg)
                self.function_depth += 1
                self.block(node.body, local)
                self.function_depth -= 1
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if not top_level or self.function_depth:
                    _fail("Imports must be unconditional top-level declarations")
                if isinstance(node, ast.ImportFrom) and (node.level or node.module not in _IMPORTS):
                    _fail("Import is outside the pure-data allowlist")
                for alias in node.names:
                    path = alias.name if isinstance(node, ast.Import) else node.module + "." + alias.name
                    parts = path.split(".")
                    if parts[0] not in _IMPORTS or (isinstance(node, ast.Import) and len(parts) != 1) or (len(parts) > 1 and parts[1] not in _IMPORTS[parts[0]]):
                        _fail("Import is outside the pure-data allowlist")
                    name = alias.asname or alias.name
                    _clean_name(name)
                    if name in scope.data | scope.functions | _BUILTINS | _ERRORS or name in scope.imports:
                        _fail("Imported names cannot shadow existing names")
                    scope.imports[name] = path
            elif isinstance(node, ast.Return):
                if not self.function_depth:
                    _fail("return outside local function")
                if node.value:
                    self.expr(node.value, scope)
            elif isinstance(node, (ast.Pass, ast.Break, ast.Continue)):
                pass
            else:
                _fail(f"Unsupported statement: {type(node).__name__}")


def validated_public_code(code, state=None):
    if state is None:
        state = CodePolicyState()
    elif isinstance(state, (set, frozenset)):
        state = CodePolicyState(data_names=frozenset(state))
    if not isinstance(state, CodePolicyState):
        _fail("Invalid policy state")
    for name in state.data_names | state.functions:
        _clean_name(name)
        if name in _BUILTINS | _ERRORS:
            _fail("Invalid callable name in supplied state")
    aliases = set()
    for alias, module in state.imports:
        _clean_name(alias)
        parts = module.split(".")
        if (alias in aliases | state.data_names | state.functions | _BUILTINS | _ERRORS or
                parts[0] not in _IMPORTS or len(parts) > 2 or (len(parts) == 2 and parts[1] not in _IMPORTS[parts[0]])):
            _fail("Invalid import in supplied state")
        aliases.add(alias)
    if not isinstance(code, str) or not code.strip():
        _fail("Code must be nonempty")
    try:
        tree = ast.parse(code, mode="exec")
    except (SyntaxError, ValueError) as error:
        raise CodePolicyError("Invalid Python syntax") from error
    scope = _Scope(state)
    _Validator().block(tree.body, scope, top_level=True)
    return scope.state()
