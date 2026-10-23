import unittest

from agent_construction_lab.fixed_model.code_policy import (
    CodePolicyError,
    CodePolicyState,
    validated_public_code,
)


class CodePolicyTests(unittest.TestCase):
    def rejected(self, *programs, state=None):
        for code in programs:
            with self.subTest(code=code):
                with self.assertRaises(CodePolicyError):
                    validated_public_code(code, state)

    def test_login_email_comprehension_and_pagination(self):
        code = '''
"""Only public APIs and data transformations."""
account = apis.supervisor.show_account_passwords()
response = apis.gmail.login(username="alice@example.test", password=account["gmail"])
token = response["access_token"]
messages = []
for page in range(1, 5):
    rows = apis.gmail.search_emails(access_token=token, page_index=page)
    if not rows:
        break
    messages.extend(rows)
ids = [row["email_id"] for row in messages if row.get("subject", "").lower().startswith("invoice")]
for email_id in ids:
    apis.gmail.mark_email_as_read(access_token=token, email_id=email_id)
print({"processed": len(ids)})
'''
        state = validated_public_code(code)
        self.assertTrue({"account", "response", "token", "messages", "ids"} <= state.data_names)
        self.assertNotIn("row", state.data_names)
        self.assertNotIn("page", state.data_names)

    def test_safe_imports_dates_and_local_functions(self):
        state = validated_public_code('''
import json
import re
import math as maths
from datetime import datetime, timedelta
from collections import Counter
from decimal import Decimal
from math import pi
import itertools, functools, statistics, random
def normalize(values):
    """A local helper over ordinary data."""
    return [str(value).strip().lower() for value in values]
values = normalize([" A ", "B"])
counts = Counter(values)
day = datetime.strptime("2026-10-23", "%Y-%m-%d") + timedelta(days=1)
amount = Decimal("1.20").quantize(Decimal("0.01"))
matches = re.compile("a+").findall("aaa")
print(json.dumps({"day": day.isoformat(), "root": maths.sqrt(9), "counts": dict(counts), "pi": pi}))
''')
        self.assertIn("normalize", state.functions)
        self.assertIn(("maths", "math"), state.imports)
        self.assertNotIn("values", state.functions)

    def test_cross_block_public_data_imports_and_functions(self):
        first = validated_public_code('''
import json as js
response = apis.gmail.login(username="a", password="b")
token = response["access_token"]
def count_rows(rows):
    return len(rows)
''')
        second = validated_public_code('''
rows = apis.gmail.search_emails(access_token=token, page_index=0)
subjects = [row.get("subject", "") for row in rows]
print(js.dumps({"count": count_rows(rows), "subjects": subjects}))
''', first)
        self.assertIn("subjects", second.data_names)
        self.assertNotIn("subjects", first.data_names)
        self.rejected('apis.gmail.search_emails(access_token=token)')
        self.rejected('requester.get("/tmp/score")', state=first)
        self.rejected('print(requester)', state={"requester"})
        self.rejected('print(world)', state=CodePolicyState(data_names=frozenset({"world"})))

    def test_filesystem_imports_and_reflection_rejected(self):
        self.rejected(
            'import os', 'import pathlib', 'from pathlib import Path',
            'from . import math', 'from math import *', 'import json.tool',
            'open("/tmp/score").read()', 'Path("/tmp/score").read_text()',
            'x = (1).__class__', 'x = [].__class__.__base__.__subclasses__()',
            'x = globals()', 'x = locals()', 'x = vars()',
            'getattr(apis, "gmail")', 'setattr(apis, "gmail", {})',
            '__import__("os")', 'eval("1 + 1")', 'exec("print(1)")',
            'compile("1", "x", "eval")', 'dir(apis)', 'type(1)',
            'object()', 'input()', 'help()', 'breakpoint()', 'memoryview(b"a")',
            'import json\nx = json.__dict__',
            'from datetime import datetime\nx = datetime.__new__',
            'apis.gmail.requester.get("/tmp/score")',
            'apis.file_system._read_file(path="/tmp/score")',
        )

    def test_calls_cannot_be_extracted_forwarded_or_shadowed(self):
        self.rejected(
            'f = apis.gmail.login\nf(username="a", password="b")',
            'a = apis\na.gmail.login()',
            'f = len\nf([])',
            'functions = [len]\nfunctions[0]([])',
            '(apis.gmail.login)(**{"username": "a"})',
            'args = [1]\nprint(*args)',
            'x = {"func": "x"}\nx["func"]()',
            'def forward(callback):\n    return callback()\nforward(1)',
            'def identity(x):\n    return x\nidentity(len)',
            'def outer():\n    def inner():\n        return 1\n    return inner\nouter()()',
            'len = 3\nlen([])',
            'import math as m\nm = {}\nm.sqrt(4)',
            'import math as m\nimport json as m',
            'def f(len):\n    return len([])',
            'import functools\nfunctools.partial(print, "x")()',
            'class X:\n    pass',
            'f = lambda: 1',
            '@print\ndef f():\n    return 1',
            'x = []\nx.unknown_method()',
            'x = []\nf = x.append',
            'x = [1]\nsorted(x, key=str)',
            'print("x", file="/tmp/file")',
        )

    def test_host_globals_and_ambiguous_control_flow_are_rejected(self):
        self.rejected(
            'print(unknown_host_global)',
            'unknown_host_global.get("score")',
            'requester = {}',
            'if False:\n    hidden = {}\nhidden.get("score")',
            'for row in []:\n    hidden = row\nprint(hidden)',
            'import json as requester',
            'def requester():\n    return 1',
            'x = []\nx.year = 2020',
            'x = []\nx.__dict__ = {}',
        )
        state = validated_public_code('''
flag = True
if flag:
    rows = []
else:
    rows = [1]
print(rows)
''')
        self.assertIn("rows", state.data_names)

    def test_ordinary_computation_error_handling_and_public_file_api(self):
        validated_public_code('''
rows = [{"id": 1}, {"id": 2}]
counts = {row["id"]: index for index, row in enumerate(rows)}
result = 0
while result < 3:
    result += 1
try:
    value = int("2")
except (ValueError, TypeError) as error:
    value = 0
    print(str(error))
assert isinstance(value, (int, float))
print(sum(counts.values()), value, result)
apis.file_system.read_file(file_path="/notes/public.txt")
''')

    def test_invalid_state_and_whole_block_validation(self):
        self.rejected('print("x")', state=CodePolicyState(imports=(("m", "os"),)))
        self.rejected('print("x")', state=CodePolicyState(imports=(("apis", "json"),)))
        self.rejected('print("x")', state={"len"})
        self.rejected('print("x")', state=CodePolicyState(functions=frozenset({"open"})))
        self.rejected('apis.gmail.send_email(to="a", subject="b")\nimport os')
        self.rejected('', 'if', 'x = ')
        self.assertEqual(validated_public_code('"docstring"\npass'), CodePolicyState())


if __name__ == "__main__":
    unittest.main()
