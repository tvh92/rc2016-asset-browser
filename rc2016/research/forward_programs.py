"""Resource usage of forward pixel programs (read-only research, no rendering).

The forward colour programs of a materialgraph (the ones the translator reports as
"forward-pass program") load every texture and sampler descriptor from the resource and sampler
tables with `S_LOAD_DWORDX8` / `S_LOAD_DWORDX4` and sample it with an `IMAGE_*` instruction. The
table slot of a descriptor is its dword offset divided by 8 (textures) or 4 (samplers), which is
the `api` index of the pass contract (`rc2016.shaders.bindings.pass_contracts`): material textures
are API 5 and up, the pass's own textures sit below 5 and at the literal offsets from 256.

``forward_resource_usage`` tracks the SGPR holding each descriptor linearly through the instruction
stream, so it reports what each sample instruction reads and nothing about branches or loops. A
descriptor whose base register or offset is not a plain immediate/literal load is left out and counted.
"""
from __future__ import annotations

import struct

from rc2016.shaders import gcn_fields
from rc2016.shaders.translator import pixel_programs

# Resource-table pointer and sampler-table pointer as SMRD sbase pair indices (s[12:13], s[14:15]).
RESOURCE_TABLE_PAIR, SAMPLER_TABLE_PAIR = 6, 7
# First pass-contract API index that carries a material texture.
FIRST_MATERIAL_API = 5


def forward_resource_usage(graph_data: bytes, shader_id: str) -> dict:
    """{'texture_apis', 'sampler_apis', 'unresolved', 'samples'} for one pixel program of a graph.

    ``texture_apis`` maps each resource-table index to the sorted names of the image instructions that read it;
    ``sampler_apis`` does the same for the sampler table. ``unresolved`` counts image instructions whose
    descriptor load could not be tied to the tables.
    """

    want = shader_id.lower()
    for program in pixel_programs(graph_data):
        if program['shader_id'].lower() != want:
            continue
        code = program['code']
        loads = {}
        textures, samplers, unresolved, samples = {}, {}, 0, 0
        for instruction in gcn_fields.decode_stream(code, 0):
            name, fields = instruction['name'], instruction['fields']
            if name in ('GCN_S_LOAD_DWORDX8', 'GCN_S_LOAD_DWORDX4'):
                base, offset = fields['src']
                if fields['imm'][0]:
                    dwords = offset
                elif offset == 255:
                    dwords = struct.unpack_from('<I', code, instruction['offset'] + 4)[0]
                else:
                    dwords = None
                loads[fields['dst'][0]] = (base, dwords)
            elif name.startswith('GCN_IMAGE_'):
                samples += 1
                _, texture_register, sampler_register = fields['src'][:3]
                texture = loads.get(texture_register)
                sampler = loads.get(sampler_register)
                if not texture or texture[0] != RESOURCE_TABLE_PAIR or texture[1] is None or texture[1] % 8:
                    unresolved += 1
                    continue
                textures.setdefault(texture[1] // 8, set()).add(name[len('GCN_'):])
                if sampler and sampler[0] == SAMPLER_TABLE_PAIR and sampler[1] is not None and sampler[1] % 4 == 0:
                    samplers.setdefault(sampler[1] // 4, set()).add(name[len('GCN_'):])
        return dict(texture_apis={k: sorted(v) for k, v in sorted(textures.items())},
                    sampler_apis={k: sorted(v) for k, v in sorted(samplers.items())},
                    unresolved=unresolved, samples=samples)
    raise KeyError(shader_id)


def split_material_apis(usage: dict) -> tuple[list[int], list[int]]:
    """(material texture APIs, pass-owned texture APIs) of a ``forward_resource_usage`` result."""

    apis = sorted(usage['texture_apis'])
    # Pass textures: APIs 0-4 and the literal-offset block from API 32 up; material textures are 5..31.
    material = [a for a in apis if FIRST_MATERIAL_API <= a < 32]
    return material, [a for a in apis if a not in material]
