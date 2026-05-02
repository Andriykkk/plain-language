"""Tests for the import-graph loader.

All tests pass an in-memory `read_source` function — sources live in
a dict and the lambda just looks them up. The loader treats this
exactly like a disk read; if the lambda raises (KeyError on a missing
path), the loader translates it to a LoadError. No filesystem touched.
"""

import unittest

from loader import Loader, LoadError


def reader_for(sources: dict[str, str]):
    """Build a `read_source` callable backed by an in-memory dict."""
    def read(abs_path: str) -> str:
        return sources[abs_path]
    return read


class TestLoaderLinearChain(unittest.TestCase):
    def test_no_imports_loads_one_file(self):
        files = Loader(reader_for({
            "/virt/main.plang": "set x to 1\nprint x\n",
        })).load_program("/virt/main.plang")
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].abs_path, "/virt/main.plang")

    def test_chain_a_imports_b_imports_c(self):
        # main → utils → math.  Post-order: math, utils, main.
        files = Loader(reader_for({
            "/virt/main.plang":  'import "utils"\nset x to 1\n',
            "/virt/utils.plang": 'import "math"\nset y to 2\n',
            "/virt/math.plang":  "set pi to 3.14\n",
        })).load_program("/virt/main.plang")

        paths = [f.abs_path for f in files]
        self.assertEqual(paths, [
            "/virt/math.plang",
            "/virt/utils.plang",
            "/virt/main.plang",
        ])


class TestLoaderDiamond(unittest.TestCase):
    def test_diamond_dedupes_shared_dependency(self):
        # main imports a and b; both import shared.  shared loads once.
        files = Loader(reader_for({
            "/virt/main.plang":   'import "a"\nimport "b"\nset x to 1\n',
            "/virt/a.plang":      'import "shared"\nset y to 2\n',
            "/virt/b.plang":      'import "shared"\nset z to 3\n',
            "/virt/shared.plang": "set common to 42\n",
        })).load_program("/virt/main.plang")

        paths = [f.abs_path for f in files]
        # shared comes first (deepest); a/b before main; either order
        # for a vs b is fine because they don't depend on each other.
        self.assertEqual(paths[0], "/virt/shared.plang")
        self.assertEqual(paths[-1], "/virt/main.plang")
        self.assertEqual(set(paths[1:3]), {"/virt/a.plang", "/virt/b.plang"})
        # And shared is loaded exactly once.
        self.assertEqual(paths.count("/virt/shared.plang"), 1)


class TestLoaderCycles(unittest.TestCase):
    def test_self_import_is_a_cycle(self):
        loader = Loader(reader_for({
            "/virt/main.plang": 'import "main"\nset x to 1\n',
        }))
        with self.assertRaises(LoadError) as cm:
            loader.load_program("/virt/main.plang")
        self.assertIn("circular import", str(cm.exception))

    def test_two_file_cycle(self):
        loader = Loader(reader_for({
            "/virt/a.plang": 'import "b"\nset x to 1\n',
            "/virt/b.plang": 'import "a"\nset y to 2\n',
        }))
        with self.assertRaises(LoadError) as cm:
            loader.load_program("/virt/a.plang")
        self.assertIn("circular import", str(cm.exception))

    def test_three_file_cycle_lists_full_chain(self):
        loader = Loader(reader_for({
            "/virt/a.plang": 'import "b"\nset x to 1\n',
            "/virt/b.plang": 'import "c"\nset y to 2\n',
            "/virt/c.plang": 'import "a"\nset z to 3\n',
        }))
        with self.assertRaises(LoadError) as cm:
            loader.load_program("/virt/a.plang")
        msg = str(cm.exception)
        self.assertIn("circular import", msg)
        # Cycle message should list all three files.
        for name in ("a.plang", "b.plang", "c.plang"):
            self.assertIn(name, msg)


class TestLoaderResolution(unittest.TestCase):
    def test_subdirectory_path(self):
        files = Loader(reader_for({
            "/virt/main.plang": 'import "lib/strings"\nset x to 1\n',
            "/virt/lib/strings.plang": "set s to 0\n",
        })).load_program("/virt/main.plang")
        paths = [f.abs_path for f in files]
        self.assertEqual(paths, [
            "/virt/lib/strings.plang",
            "/virt/main.plang",
        ])

    def test_imports_resolve_relative_to_importer_directory(self):
        # When lib/strings imports "helpers", it should look in lib/ —
        # not the directory of main.
        files = Loader(reader_for({
            "/virt/main.plang": 'import "lib/strings"\nset x to 1\n',
            "/virt/lib/strings.plang": 'import "helpers"\nset s to 0\n',
            "/virt/lib/helpers.plang": "set h to 0\n",
        })).load_program("/virt/main.plang")
        paths = [f.abs_path for f in files]
        self.assertEqual(paths, [
            "/virt/lib/helpers.plang",
            "/virt/lib/strings.plang",
            "/virt/main.plang",
        ])

    def test_missing_file_error(self):
        loader = Loader(reader_for({
            "/virt/main.plang": 'import "nope"\nset x to 1\n',
        }))
        with self.assertRaises(LoadError) as cm:
            loader.load_program("/virt/main.plang")
        # The message should mention the file that couldn't be loaded.
        self.assertIn("nope.plang", str(cm.exception))


class TestLoadedFileShape(unittest.TestCase):
    def test_loaded_file_carries_source_and_program(self):
        files = Loader(reader_for({
            "/virt/main.plang": 'set x to 5\nprint x\n',
        })).load_program("/virt/main.plang")
        self.assertEqual(len(files), 1)
        f = files[0]
        self.assertEqual(f.source, 'set x to 5\nprint x\n')
        # Program AST has both fields, even with no imports.
        self.assertEqual(f.program.imports, [])
        self.assertEqual(len(f.program.stmts), 2)

    def test_loaded_file_program_imports_populated(self):
        files = Loader(reader_for({
            "/virt/main.plang": 'import "u"\nset x to 1\n',
            "/virt/u.plang":    "set y to 2\n",
        })).load_program("/virt/main.plang")
        # Last file is main; it has one import.
        main_file = files[-1]
        self.assertEqual(len(main_file.program.imports), 1)
        self.assertEqual(main_file.program.imports[0].path, "u")


if __name__ == "__main__":
    unittest.main()
