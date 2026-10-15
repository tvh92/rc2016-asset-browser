"""The polynomial fast paths must give exactly what the plain versions gave."""
import random
import unittest
from unittest.mock import patch

from rc2016.shaders import interpolants
from rc2016.shaders.interpolants import FLAG, _flag_mul, _stream_terms, linear_form
from rc2016.shaders.ir import DAG


def reference_flag_mul(p, q, limit=None):
    """The original product."""
    if limit is not None and len(p) * len(q) > 64 * limit:
        return None
    result = {}
    for a, x in p.items():
        for b, y in q.items():
            flags = sorted({f for f in a + b if f.startswith(FLAG)})
            key = tuple(sorted(flags + [f for f in a + b if not f.startswith(FLAG)]))
            result[key] = result.get(key, 0.) + x * y
    return {k: v for k, v in result.items() if v}


def random_dag(seed, size=60):
    rng = random.Random(seed)
    ir = DAG()
    leaves = [ir.node('input', name=f'{FLAG}{k}', kind='runtime_flag') for k in range(4)]
    leaves += [ir.node('input', name=f'vertex.semantic{s}.{c}', kind='vertex_input') for s in (1, 2) for c in 'xyz']
    leaves += [ir.node('input', name=f'CB1[{w}]', kind='cb') for w in range(6)]
    leaves += [ir.const(v) for v in (0.0, 1.0, 2.0, -0.5)]
    nodes = list(leaves)
    flags = leaves[:4]
    for _ in range(size):
        op = rng.choice(['add', 'sub', 'mul', 'mul', 'neg', 'select', 'max', 'rsqrt'])
        if op == 'neg' or op == 'rsqrt':
            nodes.append(ir.node(op, [rng.choice(nodes)]))
        elif op == 'select':
            nodes.append(ir.node(op, [rng.choice(flags), rng.choice(nodes), rng.choice(nodes)]))
        elif op == 'max' and rng.random() < 0.5:
            nodes.append(ir.node(op, [rng.choice(flags), rng.choice(flags)]))
        else:
            nodes.append(ir.node(op, [rng.choice(nodes), rng.choice(nodes)]))
    return ir, nodes


class FlagMulTests(unittest.TestCase):
    def test_product_matches_the_original(self):
        rng = random.Random(3)
        names = [f'{FLAG}{k}' for k in range(4)] + ['vertex.semantic1.x', 'CB1[0]', 'CB1[1]', 'op#7']
        def poly():
            return {tuple(sorted(set(rng.sample(names, rng.randint(0, 3))))): rng.choice((1., -1., .5, 2.))
                    for _ in range(rng.randint(0, 6))}
        for _ in range(400):
            p, q = poly(), poly()
            for limit in (None, 1, 4):
                self.assertEqual(_flag_mul(p, q, limit), reference_flag_mul(p, q, limit))
                self.assertEqual(list(_flag_mul(p, q, limit) or ()), list(reference_flag_mul(p, q, limit) or ()))


class SharedFormTests(unittest.TestCase):
    def test_forms_kept_on_the_dag_equal_fresh_expansions(self):
        for seed in range(30):
            ir, nodes = random_dag(seed)
            order = list(range(len(ir.nodes)))
            random.Random(seed).shuffle(order)
            for limit in (16, 256):
                for i in order:   # every node, through one shared memo, in random order
                    with patch.object(interpolants, '_flag_mul', reference_flag_mul):
                        fresh = linear_form(DAG(ir.nodes), i, limit)
                    self.assertEqual(linear_form(ir, i, limit), fresh, (seed, i, limit))

    def test_stream_terms_reuse_rsqrt_results(self):
        for seed in range(30):
            ir, nodes = random_dag(seed)
            for i in range(len(ir.nodes)):
                form = linear_form(ir, i, 256)
                with patch.object(interpolants, '_flag_mul', reference_flag_mul):
                    copy = DAG(ir.nodes)
                    expected = _stream_terms(copy, linear_form(copy, i, 256), 1)
                self.assertEqual(_stream_terms(ir, form, 1), expected, (seed, i))


class GraphStreamEvidenceTests(unittest.TestCase):
    def setUp(self):
        from rc2016.shaders import bindings
        self.bindings = bindings
        bindings._stream_evidence.cache_clear()
        self.addCleanup(bindings._stream_evidence.cache_clear)
        self.addCleanup(bindings.use_compile_processes, 0)

    def test_concurrent_materials_analyse_a_graph_once(self):
        import threading, time
        calls = []
        def rotated(data):
            calls.append(data); time.sleep(0.05); return {1: 3}
        results = []
        self.bindings.use_compile_processes(0)
        with patch.object(interpolants, 'rotated_stream_attributes', side_effect=rotated), \
             patch.object(interpolants, 'squared_stream_evidence', return_value=({(0, 1): (2, 0)}, {})):
            threads = [threading.Thread(target=lambda: results.append(self.bindings.graph_stream_evidence(b'graph')))
                       for _ in range(5)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
        self.assertEqual(calls, [b'graph'])
        self.assertEqual(results, [({1}, {(0, 1): (2, 0)})] * 5)
        results[0][0].add(9)   # callers get their own copies
        self.assertEqual(self.bindings.graph_stream_evidence(b'graph'), ({1}, {(0, 1): (2, 0)}))

    def test_halves_are_analysed_only_when_asked_for(self):
        self.bindings._rotated_evidence.cache_clear()
        self.bindings._squared_evidence.cache_clear()
        self.addCleanup(self.bindings._rotated_evidence.cache_clear)
        self.addCleanup(self.bindings._squared_evidence.cache_clear)
        self.bindings.use_compile_processes(0)
        common, by_header = {(0, 1): (2, 0)}, {0x100: {(0, 1): (2, 0)}}
        with patch.object(interpolants, 'rotated_stream_attributes', return_value={1: 3}) as rotated,              patch.object(interpolants, 'squared_stream_evidence', return_value=(common, by_header)) as squared:
            self.assertEqual(self.bindings.graph_stream_evidence(b'g', normals=False), (set(), common))
            self.assertEqual(rotated.call_count, 0)
            self.assertEqual(self.bindings.graph_stream_evidence(b'g', {'header_offset': 0x100}, normals=False),
                             (set(), by_header[0x100]))
            self.assertEqual(self.bindings.graph_stream_evidence(b'g', channels=False), ({1}, {}))
            self.assertEqual((rotated.call_count, squared.call_count), (1, 1))

    def test_lazy_evidence_loads_on_first_read_and_only_once(self):
        loads = []
        evidence = self.bindings._LazyEvidence(lambda: loads.append(1) or {(0, 1): (2, 0)})
        self.assertEqual(loads, [])
        self.assertTrue(evidence)
        self.assertEqual(evidence.get((0, 1)), (2, 0))
        self.assertIsNone(evidence.get((9, 9)))
        self.assertIn((0, 1), evidence)
        self.assertEqual(loads, [1])

    def test_worker_processes_return_what_this_process_computes(self):
        graph = b'not a graph'   # analysis failures give empty evidence, in a worker as here
        expected = self.bindings.stream_evidence(graph)[:2]
        self.bindings.use_compile_processes(2)
        self.assertEqual(self.bindings.graph_stream_evidence(graph), expected)
        self.assertIsNotNone(self.bindings._POOL)


if __name__ == '__main__':
    unittest.main()
