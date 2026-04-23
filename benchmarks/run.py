import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from io import StringIO

# Make the PlainLang interpreter importable from this subdir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lexer import tokenize
from parser import Parser
from evaluator import execute_program


# ---------- timing helpers ----------

def _find_c_compiler() -> str | None:
    for name in ("cc", "gcc", "clang"):
        path = shutil.which(name)
        if path:
            return path
    return None


def time_plainlang(source: str) -> float:
    tokens = tokenize(source)
    stmts = Parser(source, tokens).parse_program()
    buf = StringIO()
    start = time.perf_counter()
    with redirect_stdout(buf):
        execute_program(stmts)
    return time.perf_counter() - start


def time_python(source: str) -> float:
    buf = StringIO()
    start = time.perf_counter()
    with redirect_stdout(buf):
        exec(compile(source, "<bench>", "exec"), {"__name__": "__main__"})
    return time.perf_counter() - start


def time_c(source: str) -> float | None:
    cc = _find_c_compiler()
    if cc is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        src_path = os.path.join(tmp, "bench.c")
        exe_path = os.path.join(tmp, "bench.out")
        with open(src_path, "w") as f:
            f.write(source)
        subprocess.run([cc, "-O2", src_path, "-o", exe_path], check=True, stderr=subprocess.PIPE)
        start = time.perf_counter()
        subprocess.run([exe_path], check=True, stdout=subprocess.DEVNULL)
        return time.perf_counter() - start


# ---------- benchmark definitions ----------

LOOP_N = 100_000

LOOP_PLAIN = f"""
set total to 0
repeat for i from 1 to {LOOP_N}
    add i to total
end
print total
"""

LOOP_PYTHON = f"""
total = 0
for i in range(1, {LOOP_N} + 1):
    total += i
print(total)
"""

LOOP_C = f"""#include <stdio.h>
int main(void) {{
    long long total = 0;
    for (long long i = 1; i <= {LOOP_N}; i++) total += i;
    printf("%lld\\n", total);
    return 0;
}}
"""


FIB_N = 22

FIB_PLAIN = f"""
define function fib
    input n as number
    output as number

    if n is less than 2
        return n
    end
    return (call fib with (n minus 1)) plus (call fib with (n minus 2))
end

print call fib with {FIB_N}
"""

FIB_PYTHON = f"""
def fib(n):
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)

print(fib({FIB_N}))
"""

FIB_C = f"""#include <stdio.h>
long fib(long n) {{
    if (n < 2) return n;
    return fib(n - 1) + fib(n - 2);
}}
int main(void) {{
    printf("%ld\\n", fib({FIB_N}));
    return 0;
}}
"""


# ---------- runner ----------

def _fmt_ms(t: float | None) -> str:
    if t is None:
        return "   (skipped)"
    return f"{t * 1000:10.2f} ms"


def compare(name: str, plain: str, py: str, c: str) -> None:
    print(f"\n=== {name} ===")

    try:
        t_plain = time_plainlang(plain)
    except Exception as e:
        t_plain = None
        print(f"  PlainLang: ERROR {e}")
    else:
        print(f"  PlainLang: {_fmt_ms(t_plain)}")

    try:
        t_py = time_python(py)
    except Exception as e:
        t_py = None
        print(f"  Python:    ERROR {e}")
    else:
        print(f"  Python:    {_fmt_ms(t_py)}")

    try:
        t_c = time_c(c)
    except subprocess.CalledProcessError as e:
        t_c = None
        print(f"  C (-O2):   compile/run failed: {e}")
    except Exception as e:
        t_c = None
        print(f"  C (-O2):   ERROR {e}")
    else:
        print(f"  C (-O2):   {_fmt_ms(t_c)}")

    if t_plain is not None and t_py is not None and t_py > 0:
        print(f"  → PlainLang is {t_plain / t_py:7.1f}x slower than Python")
    if t_plain is not None and t_c is not None and t_c > 0:
        print(f"  → PlainLang is {t_plain / t_c:7.1f}x slower than C")
    if t_py is not None and t_c is not None and t_c > 0:
        print(f"  → Python    is {t_py / t_c:7.1f}x slower than C")


def main() -> None:
    print(f"PlainLang vs Python vs C — single run, no warmup")
    compare(f"Sum loop 1..{LOOP_N}", LOOP_PLAIN, LOOP_PYTHON, LOOP_C)
    compare(f"Recursive fibonacci({FIB_N})", FIB_PLAIN, FIB_PYTHON, FIB_C)


if __name__ == "__main__":
    main()
