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
from compiler import compile_program
from vm import execute


# ---------- timing helpers ----------

def _find_c_compiler() -> str | None:
    for name in ("cc", "gcc", "clang"):
        path = shutil.which(name)
        if path:
            return path
    return None


def time_plainlang(source: str) -> float:
    """Measures execution time only — parsing and compilation are done
    up-front and excluded, matching how the language would be used in
    practice (compile once, run many times)."""
    tokens = tokenize(source)
    program = Parser(source, tokens).parse_program()
    module = compile_program(program.stmts)
    buf = StringIO()
    start = time.perf_counter()
    with redirect_stdout(buf):
        execute(module)
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
    input n as i64
    output as i64

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


# ---------- list benchmarks ----------

LIST_BUILD_N = 20_000

LIST_BUILD_PLAIN = f"""
set xs to empty list of i64
repeat for i from 1 to {LIST_BUILD_N}
    append i to xs
end
set total to 0
repeat for each v in xs
    add v to total
end
print total
"""

LIST_BUILD_PYTHON = f"""
xs = []
for i in range(1, {LIST_BUILD_N} + 1):
    xs.append(i)
total = 0
for v in xs:
    total += v
print(total)
"""

LIST_BUILD_C = f"""#include <stdio.h>
#include <stdlib.h>
int main(void) {{
    int n = {LIST_BUILD_N};
    long long *xs = (long long*)malloc(sizeof(long long) * n);
    for (int i = 0; i < n; i++) xs[i] = i + 1;
    long long total = 0;
    for (int i = 0; i < n; i++) total += xs[i];
    printf("%lld\\n", total);
    free(xs);
    return 0;
}}
"""


BUBBLE_N = 200

BUBBLE_PLAIN = f"""
set n to {BUBBLE_N}
set arr to empty list of i64

set v to n
repeat n times
    append v to arr
    subtract 1 from v
end

repeat for i from 1 to n
    repeat for j from 0 to n minus i minus 1
        if arr[j] is greater than arr[j plus 1]
            set tmp to arr[j]
            set arr[j] to arr[j plus 1]
            set arr[j plus 1] to tmp
        end
    end
end

print arr[0], arr[n minus 1]
"""

BUBBLE_PYTHON = f"""
n = {BUBBLE_N}
arr = list(range(n, 0, -1))
for i in range(1, n + 1):
    for j in range(0, n - i):
        if arr[j] > arr[j + 1]:
            arr[j], arr[j + 1] = arr[j + 1], arr[j]
print(arr[0], arr[-1])
"""

BUBBLE_C = f"""#include <stdio.h>
int main(void) {{
    int n = {BUBBLE_N};
    int arr[{BUBBLE_N}];
    for (int i = 0; i < n; i++) arr[i] = n - i;
    for (int i = 1; i <= n; i++) {{
        for (int j = 0; j < n - i; j++) {{
            if (arr[j] > arr[j + 1]) {{
                int t = arr[j];
                arr[j] = arr[j + 1];
                arr[j + 1] = t;
            }}
        }}
    }}
    printf("%d %d\\n", arr[0], arr[n - 1]);
    return 0;
}}
"""


SIEVE_N = 10_000

SIEVE_PLAIN = f"""
set n to {SIEVE_N}
# is_prime has length n+1; index k represents the number k
set is_prime to empty list of i64
repeat n plus 1 times
    append 1 to is_prime
end
set is_prime[0] to 0
set is_prime[1] to 0

set i to 2
repeat while i times i is at most n
    if is_prime[i] is equal to 1
        set j to i times i
        repeat while j is at most n
            set is_prime[j] to 0
            add i to j
        end
    end
    add 1 to i
end

set count to 0
repeat for k from 2 to n
    if is_prime[k] is equal to 1
        add 1 to count
    end
end

print count
"""

SIEVE_PYTHON = f"""
n = {SIEVE_N}
is_prime = [1] * (n + 1)
is_prime[0] = 0
is_prime[1] = 0
i = 2
while i * i <= n:
    if is_prime[i]:
        j = i * i
        while j <= n:
            is_prime[j] = 0
            j += i
    i += 1
count = 0
for k in range(n + 1):
    if is_prime[k]:
        count += 1
print(count)
"""

SIEVE_C = f"""#include <stdio.h>
int main(void) {{
    int n = {SIEVE_N};
    int is_prime[{SIEVE_N} + 1];
    for (int i = 0; i <= n; i++) is_prime[i] = 1;
    is_prime[0] = 0;
    is_prime[1] = 0;
    for (int i = 2; i * i <= n; i++) {{
        if (is_prime[i]) {{
            for (int j = i * i; j <= n; j += i) {{
                is_prime[j] = 0;
            }}
        }}
    }}
    int count = 0;
    for (int k = 0; k <= n; k++) if (is_prime[k]) count++;
    printf("%d\\n", count);
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
    compare(f"List build + sum ({LIST_BUILD_N} appends)", LIST_BUILD_PLAIN, LIST_BUILD_PYTHON, LIST_BUILD_C)
    compare(f"Bubble sort (N={BUBBLE_N})", BUBBLE_PLAIN, BUBBLE_PYTHON, BUBBLE_C)
    compare(f"Sieve of Eratosthenes (N={SIEVE_N})", SIEVE_PLAIN, SIEVE_PYTHON, SIEVE_C)


if __name__ == "__main__":
    main()
