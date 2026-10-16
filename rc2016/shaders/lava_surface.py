"""Dedicated source-bound lava rules; never native-ready translations."""
import hashlib
import json

from rc2016.shaders import lava_ocean

RULES = {lava_ocean.GRAPH: lava_ocean}
GRAPH_HASHES = {path: module.GRAPH_SHA256 for path, module in RULES.items()}


def _digest(receipt):
    return hashlib.sha256(json.dumps({k:v for k,v in receipt.items() if k != 'translation_sha256'},
        sort_keys=True, separators=(',',':')).encode()).hexdigest()


def prepare_rule(path, graph_data, material_data):
    receipt = RULES[path].surface_rule(graph_data, material_data)
    receipt['code_sha256'] = receipt['writer_sha256']
    receipt['translation_sha256'] = _digest(receipt)
    if not valid_contract(receipt):
        raise ValueError('Invalid partial lava source receipt')
    return receipt


def valid_contract(receipt):
    if not isinstance(receipt, dict): return False
    module = RULES.get(receipt.get('graph_path'))
    return bool(module and module.valid_contract(receipt)
        and receipt.get('translation_sha256') == _digest(receipt))
