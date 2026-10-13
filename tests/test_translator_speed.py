"""The compiler's fast paths must give exactly what the plain versions gave."""
import random
import threading
import time
import unittest
from unittest.mock import patch

from rc2016.shaders import bindings
from rc2016.shaders.translator import Compiler, project_table


def reference_projection(table, n, used):
    """The original per-assignment loop."""
    reduced = 0
    for k in range(1 << len(used)):
        source = sum(1 << used[q] for q in range(len(used)) if (k >> q) & 1)
        reduced |= ((table >> source) & 1) << k
    return reduced


class ProjectionTests(unittest.TestCase):
    def test_bit_parallel_projection_matches_the_loop(self):
        rng = random.Random(7)
        for n in range(1, 11):
            for _ in range(25):
                table = rng.getrandbits(1 << n)
                used = sorted(rng.sample(range(n), rng.randint(0, n)))
                self.assertEqual(project_table(table, n, used), reference_projection(table, n, used), (n, used))


class ChangedRegisterTests(unittest.TestCase):
    def test_changed_registers_keep_insertion_order(self):
        compiler = Compiler(b'', [])
        registers = {7: 1, 2: 2, 9: 3}
        before = dict(registers)
        registers[9] = 30; registers[7] = 10; registers[4] = 40   # 4 is new, added last
        self.assertEqual(compiler.changed(registers, before), [(7, 10), (9, 30), (4, 40)])
        before = dict(registers)
        self.assertEqual(compiler.changed(registers, before), [])
        registers[1] = 5; registers[2] = 20
        self.assertEqual(compiler.changed(registers, before), [(2, 20), (1, 5)])

    def test_unhashable_values_fall_back_to_every_item(self):
        compiler = Compiler(b'', [])
        registers = {1: [1], 2: [2]}
        self.assertEqual(compiler.changed(registers, {}), [(1, [1]), (2, [2])])


class CompiledGraphTests(unittest.TestCase):
    def test_concurrent_callers_compile_a_graph_once(self):
        calls = []

        def compile_graph(data):
            calls.append(data); time.sleep(0.05); return dict(graph=data)
        bindings._compiled.cache_clear()
        self.addCleanup(bindings._compiled.cache_clear)
        results = []
        with patch.object(bindings, 'compile_graph', side_effect=compile_graph):
            threads = [threading.Thread(target=lambda: results.append(bindings.compiled(b'graph'))) for _ in range(6)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
        self.assertEqual(calls, [b'graph'])
        self.assertEqual(results, [dict(graph=b'graph')] * 6)


if __name__ == '__main__':
    unittest.main()
