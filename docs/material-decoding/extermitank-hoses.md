# Extermitank hoses

The exact graph `materialgraph/character/enemy/enm_extermitank_hoses/enm_extermitank_hoses.materialgraph` (SHA-256 `ded9c6922595742a6dd88670284a3551ce03c78e4aad58da2445b2a565efa58c`) compiles to several pixel programs. Only the deferred writer `0x91799332` (code SHA-256 `f0caa0b6ebb3b94abb07c383065ea91b26edc30ff5e14c5454c052215c639ac4`, pass contract 1) is bound. Its only unbound engine input is `CB0[99]`.

The exact material `material/character/enemy/enm_extermitank_hoses/enm_extermitank_hoses.material` (SHA-256 `905d93721826c44646981e3ca991b96ede18022316023280348d7700ebc546df`) binds:

| API | Texture | Used by |
|---|---|---|
| 5 | `textures/character/enemy/enm_tank_extermitank/enm_tank_extermitank_hoses_g.texture` | F0, gloss, albedo, emission |
| 6 | `textures/character/enemy/enm_tank_extermitank/enm_tank_extermitank_hoses_c.texture` | albedo, emission (alpha channel only) |
| 7 | `textures/character/enemy/enm_tank_extermitank/enm_tank_extermitank_hoses_c.texture` | albedo |

The material's float parameters are read through the graph's CB2 pass table. Only `CB2[3]` (material value `0.775`) enters the writer's expressions that depend on `CB0[99]`.

`CB0[99]` is a time-like phase input. In the source DAG it is multiplied by `CB2[3]` and added to the V coordinate of the API 6 sample that reads the `_c` alpha channel. That sample is the self-lit mask, so the phase input shifts the self-lit term used by albedo and emission. Sampling for API 5 and API 7 uses the unshifted UV. F0 and gloss do not depend on `CB0[99]`.

The bounded preview freezes `CB0[99]` at 0, which removes the offset. The engine upload and runtime value are unverified; this value is a review choice, not a decoded game value.

No portable tangent-normal slice was recovered, so the geometry normal is used. The sibling writer `0x315a8578` in the same graph needs `CB0[99]`, `CB0[95]`, `POS_W` and `TBUFFER` inputs and stays unbound.

The source-preview policy applies only to the exact graph, material, writer shader ID and code hash above. Any other graph or material, including one that shares the writer code, does not receive it. The receipt is validated before Blender acceptance. Game equivalence is false. Shared review lighting and the deferred BRDF approximation apply as for other materials. Archive-backed checks are in `tests/test_extermitank_hoses_preview.py`.

The graph holds two deferred writers (`0x91799332` and `0x315a8578`). Which one the engine selects at draw time is not established; this preview follows `0x91799332` because it is the only one whose inputs reduce to a single named, frozen value.
