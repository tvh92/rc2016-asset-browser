"""The compiler's fast paths must give exactly what the plain versions gave."""
import random
import threading
import time
import unittest
from unittest.mock import patch

from rc2016.shaders import bindings
from rc2016.shaders.translator import Compiler, Registers, project_table


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


class RegisterLogTests(unittest.TestCase):
    def test_log_records_first_previous_value_and_order(self):
        registers = Registers({3: 30, 1: 10})
        registers.log = {}
        registers[1] = 11; registers[1] = 12; registers[5] = 50
        self.assertEqual(registers.log, {1: 10, 5: None})
        self.assertEqual(sorted(registers.log, key=registers.order.__getitem__), [1, 5])
        registers.log = None
        registers[3] = 31
        self.assertIsNone(registers.log)
        self.assertEqual(list(registers.order), list(registers))

    def test_order_follows_dict_order_through_every_mutation(self):
        registers = Registers()
        registers.update({4: 1, 2: 2}); registers.setdefault(9, 3); registers.setdefault(4, 0)
        registers.pop(2); registers[2] = 5; del registers[4]; registers[4] = 6
        self.assertEqual(registers, {9: 3, 2: 5, 4: 6})
        self.assertEqual(registers.order, {9: 0, 2: 1, 4: 2})

    def test_predicated_step_merges_writes_like_the_full_comparison(self):
        def compiler(plain):
            c = Compiler(b'', [])
            for r in range(6): c.v[r] = c.ir.node('input', name=f'v{r}', kind='test')
            for r in range(4): c.s[r] = c.ir.node('input', name=f's{r}', kind='test')
            if plain: c.v, c.s = dict(c.v), dict(c.s)
            c.bp = c.ir.node('input', name='flag', kind='runtime_flag')
            def instruction(_i):
                c.v[4] = c.ir.const(2.); c.v[1] = c.ir.const(3.); c.v[1] = c.ir.const(4.)
                c.v[9] = c.ir.const(5.); c.s[2] = c.ir.const(6.); c.v[0] = c.v[0]
            c.instruction = instruction
            c.step(None)
            return c.ir.nodes, dict(c.v), dict(c.s)
        self.assertEqual(compiler(False), compiler(True))


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
