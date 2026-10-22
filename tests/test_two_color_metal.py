"""Source-backed corpus checks for the TwoColorGlossMetal viewer rule.

Reads saved source_assets through RC2016_RESEARCH; mocked texture loads avoid archive or GUI access.
"""
from __future__ import annotations
import hashlib, json, math, os, struct, unittest
from pathlib import Path
from unittest.mock import patch
ARCHIVE = Path(__file__).resolve().parents[1] / 'Research' / '2026-10-22_moved-from-PS4-models-Animation-pipeline-Research'
RESEARCH = Path(os.environ['RC2016_RESEARCH']) if os.environ.get('RC2016_RESEARCH') else None
HERE = ARCHIVE / 'decodes/Materials/TwoColorGlossMetal'
from rc2016.formats.materials import extract_material_parameters, viewer_graph_rule
from rc2016.shaders.families import graph_parameter_tables
from rc2016.viewer import textures as tp
from rc2016.viewer.viewport import ModelViewport

GRAPH_PATH="materialgraph/global/basic_normal_gloss_metal/basic_normal_two_color_gloss_metal.materialgraph"
GRAPH_FILE=HERE/"source_assets/basic_normal_two_color_gloss_metal.materialgraph" if HERE else None
EXPECTED_GRAPH_SHA="342877ebd945320f167e0d1eb269a8279a7edbba85f2867c7fd8fecfc2e2a93e"
SLOTS={"_c":0x49A7866BABDAD780,"_g":0x49A7866B1576D848,"_n":0x49A7866B7AE34E7A}
VECTORS={"diffuse_a":(0xD3E7B61B,(.1778884,1.,.8468732)),
         "diffuse_b":(0xA660CD56,(.3915725,.09530747,0.)),
         "spec_a":(0x72D80ADC,(0.,1.,.05780543)),
         "spec_b":(0xE00CD247,(1.,0.,1.))}
SCALARS={"power":(0x4A4437A7,1.),"gloss_power":(0x010CB8D3,4.),
         "texture_scale":(0x2F076BCD,6.),"normal_strength":(0xDCE99DE5,1.)}
EXPECTED_RUNTIME={"lighting_flags":96,"ssr_weight":0.,"fog_fade":1.,"debug":0,
                  "instance_opacity":1.,"alpha_cutoff":0.,"normal_fade":2.,"geometric_aa":1.}
class SourceAssetFakeArchive:
    """Minimal source-byte adapter matching the viewer Archive API."""
    def __init__(self, records):
        self.entries={}; self.by_path={}; self.blobs={}; self._graph_info_cache={}
        for i,(path,kind,data) in enumerate(records):
            key=f"source-{i}"
            self.entries[key]={"type":kind,"path":path,"built_path":path+".built"}
            self.by_path[path]=[key]; self.blobs[key]=data
    def resolve(self,path): return self.by_path.get(path,[])
    def bytes(self,key): return self.blobs[key],"source_assets_sha256"


def dat1(strings, section_builder):
    """Small real DAT1 fixture builder, adapted from test_texture_preview."""
    section_count=len(section_builder([0]*len(strings)))
    offsets=[]; cursor=16+12*section_count+1; text=b"\0"
    for value in strings:
        encoded=value.encode("latin-1"); offsets.append(cursor); text+=encoded+b"\0"; cursor+=len(encoded)+1
    rows=bytearray(); payload=bytearray(); offset=16+12*section_count+len(text)
    for tag,blob in section_builder(offsets):
        rows+=struct.pack("<III",tag,offset,len(blob)); payload+=blob; offset+=len(blob)
    return b"1TAD"+struct.pack("<IIHH",0,offset,section_count,0)+bytes(rows)+text+bytes(payload)

def slot_rows(rows):
    return b"".join(struct.pack("<IIQ",name_offset,1,slot) for name_offset,slot in rows)


def fake_parameter_rows(overrides):
    params=[]
    for key,value in overrides.items():
        vals=value if isinstance(value,(tuple,list)) else (value,)
        raw=struct.pack("<%df"%len(vals),*vals)
        params.append({"key":f"0x{key:08X}","key_hash":key,"size":len(raw),"raw_hex":raw.hex(),"type":"unsupported_untyped"})
    return {"parameters":[],"unsupported_parameters":params}


def source_value(data,key,fallback,vector=False,defaults=None):
    extracted=extract_material_parameters(data)
    rows={r.get("key_hash"):r for r in extracted.get("parameters",[])+extracted.get("unsupported_parameters",[])}
    row=rows.get(key)
    needed=12 if vector else 4
    if row and row.get("size",0)>=needed:
        count=3 if vector else 1
        vals=struct.unpack("<%df"%count,bytes.fromhex(row["raw_hex"])[:needed])
        value=list(vals) if vector else vals[0]
        return value,"material_override"
    if defaults and key in defaults:
        value=defaults[key]
        if vector and isinstance(value,(list,tuple)) and len(value)==3: return list(value),"graph_default"
        if not vector and isinstance(value,(int,float)): return float(value),"graph_default"
    return (list(fallback) if vector else float(fallback)),"viewer_fallback"


def close(a,b):
    if isinstance(a,(tuple,list)):
        return len(a)==len(b) and all(math.isclose(float(x),float(y),rel_tol=0,abs_tol=1e-7) for x,y in zip(a,b))
    return math.isclose(float(a),float(b),rel_tol=0,abs_tol=1e-7)


def make_source_archive():
    manifest=json.loads((HERE/"source_manifest.json").read_text(encoding="utf-8"))
    graph_record=next(r for r in manifest if r["filename"].endswith(".materialgraph"))
    graph_bytes=(HERE/"source_assets"/graph_record["filename"]).read_bytes()
    if hashlib.sha256(graph_bytes).hexdigest()!=EXPECTED_GRAPH_SHA: raise AssertionError("saved graph SHA mismatch")
    records=[(graph_record["path"],"materialgraph",graph_bytes)]; sources=[]
    for row in manifest:
        if not row["filename"].endswith(".material"): continue
        p=HERE/"source_assets"/row["filename"]; data=p.read_bytes()
        if hashlib.sha256(data).hexdigest()!=row["decoded_sha256"]: raise AssertionError(f"source SHA mismatch: {p.name}")
        records.append((row["path"],"material",data)); sources.append({**row,"data":data})
    if len(sources)!=8: raise AssertionError(f"expected 8 copied materials, got {len(sources)}")
    return SourceAssetFakeArchive(records),graph_bytes,sources


class ViewerCorpusChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if HERE is None or not (HERE/'source_manifest.json').is_file():
            raise unittest.SkipTest('Set RC2016_RESEARCH to the private workspace with the two-color source records')
        cls.archive,cls.graph_bytes,cls.sources=make_source_archive()
        cls.graph_tables,cls.graph_defaults=graph_parameter_tables(cls.graph_bytes)
        cls.materials=[{"index":i,"path":row["path"]} for i,row in enumerate(cls.sources)]

    def test_exact_dotted_rule_boundary(self):
        rule=tp._graph_rule([GRAPH_PATH])
        self.assertTrue(rule.get("two_color_metal"))
        self.assertIsNone(rule.get("self_lit_key"))
        sibling=GRAPH_PATH.replace("basic_normal_two_color_gloss_metal.materialgraph","basic_normal_two_color_gloss_metal_variant.materialgraph")
        self.assertEqual(tp._graph_rule([sibling]),{})

    def test_all_8_payload_values_and_native_slots(self):
        checked=[]; field_sources={k:{"material_override":0,"graph_default":0,"viewer_fallback":0} for k in (*VECTORS,*SCALARS)}
        for row in self.sources:
            data=row["data"]; paths=tp.material_texture_paths(self.archive,row["path"])
            serialized=tp.texture_slots(data,tp.MATERIAL_SLOT_SECTION)
            for role,slot in SLOTS.items():
                self.assertIn(slot,serialized,f"{row['filename']} missing serialized {role} slot")
                self.assertEqual(paths.get(role),serialized[slot],f"{row['filename']} {role} did not follow slot identity")
            metal=paths.get("two_color_metal"); self.assertIsNotNone(metal,row["filename"])
            self.assertEqual(metal["writer"],"1012E99D")
            self.assertEqual(metal["scope"],"source_material_terms_with_viewer_lighting")
            self.assertEqual(metal["runtime"],EXPECTED_RUNTIME)
            values={}; sources={}
            for name,(key,fallback) in VECTORS.items():
                expected,source=source_value(data,key,fallback,True,self.graph_defaults)
                self.assertTrue(close(metal[name],expected),f"{row['filename']} {name}: {metal[name]} != {expected}")
                values[name]=metal[name]; sources[name]=source; field_sources[name][source]+=1
            for name,(key,fallback) in SCALARS.items():
                expected,source=source_value(data,key,fallback,False,self.graph_defaults)
                self.assertTrue(close(metal[name],expected),f"{row['filename']} {name}: {metal[name]} != {expected}")
                values[name]=metal[name]; sources[name]=source; field_sources[name][source]+=1
            expected_spec,_=source_value(data,0x62835AF9,1.,False,self.graph_defaults)
            expected_gloss,_=source_value(data,0x24F4CA4C,1.,False,self.graph_defaults)
            self.assertTrue(close(paths["spec"],expected_spec)); self.assertTrue(close(paths["gloss"],expected_gloss))
            checked.append({"path":row["path"],"decoded_sha256":row["decoded_sha256"],
                "slots":{role:paths[role] for role in SLOTS},"parameters":values,"parameter_sources":sources,
                "generic_spec_scale":paths["spec"],"generic_gloss_scale":paths["gloss"]})
        self.assertEqual(len(checked),8)
        self.assertTrue(any(source=="graph_default" for counts in field_sources.values() for source,n in counts.items() if source=="graph_default" and n))
        self.assertTrue(any(0.0 in v["parameters"]["spec_a"] for v in checked),"must preserve native zero channels")

    def test_no_suffix_guess_and_serialized_slots_win(self):
        # Deliberately contradictory names: role assignment must follow slot hashes, not suffixes.
        graph=self.graph_bytes
        strings=[GRAPH_PATH,"textures/test/c_looks_like_n.texture","textures/test/g_looks_like_c.texture","textures/test/n_looks_like_g.texture"]
        slots=[("_c",strings[1],SLOTS["_c"]),("_g",strings[2],SLOTS["_g"]),("_n",strings[3],SLOTS["_n"])]
        def sections(at): return [(tp.MATERIAL_SLOT_SECTION,slot_rows([(at[i+1],slot) for i,(_,_,slot) in enumerate(slots)]))]
        material=dat1(strings,sections)
        archive=SourceAssetFakeArchive([(GRAPH_PATH,"materialgraph",graph),("material/test_conflicting_suffixes.material","material",material)])
        got=tp.material_texture_paths(archive,"material/test_conflicting_suffixes.material")
        self.assertEqual(got["_c"],strings[1]); self.assertEqual(got["_g"],strings[2]); self.assertEqual(got["_n"],strings[3])

    def test_graph_defaults_and_zero_instance_values(self):
        strings=[GRAPH_PATH]
        no_override=dat1(strings,lambda at:[])
        archive=SourceAssetFakeArchive([(GRAPH_PATH,"materialgraph",self.graph_bytes),("material/no_overrides.material","material",no_override)])
        with patch("rc2016.formats.materials.extract_material_parameters",return_value={"parameters":[],"unsupported_parameters":[]}):
            got=tp.material_texture_paths(archive,"material/no_overrides.material")["two_color_metal"]
        for name,(key,fallback) in VECTORS.items():
            expected,source=source_value(no_override,key,fallback,True,self.graph_defaults)
            self.assertEqual(source,"graph_default"); self.assertTrue(close(got[name],expected))
        for name,(key,fallback) in SCALARS.items():
            expected,source=source_value(no_override,key,fallback,False,self.graph_defaults)
            self.assertEqual(source,"graph_default"); self.assertTrue(close(got[name],expected))
        # A stored zero is a real override and must not be replaced with the nonzero graph default.
        zeros=fake_parameter_rows({0x4A4437A7:0.,0xD3E7B61B:(0.,0.,0.)})
        with patch("rc2016.formats.materials.extract_material_parameters",return_value=zeros):
            got_zero=tp.material_texture_paths(archive,"material/no_overrides.material")["two_color_metal"]
        self.assertEqual(got_zero["power"],0.); self.assertEqual(got_zero["diffuse_a"],[0.,0.,0.])

    def test_stale_self_lit_99_is_suppressed(self):
        actual=self.sources[0]; data=actual["data"]
        parsed=extract_material_parameters(data)
        parsed.setdefault("unsupported_parameters",[]).append({"key":"0x66EFA55B","key_hash":0x66EFA55B,
            "size":4,"raw_hex":struct.pack("<f",99.).hex(),"type":"unsupported_untyped"})
        with patch("rc2016.formats.materials.extract_material_parameters",return_value=parsed):
            got=tp.material_texture_paths(self.archive,actual["path"])
        self.assertEqual(got["k"],0.)

    def test_payload_transport_via_material_textures_and_viewport_entry(self):
        def fake_load(_archive,path):
            return {"path":path,"format":99,"width":1,"height":1,"srgb":path.endswith(("_c.texture","_g.texture")),"pixels":b"\0\0\0\0"}
        with patch.object(tp,"load_texture",side_effect=fake_load):
            entries=tp.material_textures(self.archive,self.materials,{})
        self.assertEqual(len(entries),8)
        viewport=object.__new__(ModelViewport); next_name=[40]
        def upload(texture):
            next_name[0]+=1; return next_name[0]
        viewport._upload_texture=upload
        transport=[]
        for index,source in enumerate(self.sources):
            original=tp.material_texture_paths(self.archive,source["path"])
            entry=entries[index]
            self.assertEqual(entry["two_color_metal"],original["two_color_metal"])
            gl=viewport._gl_entry(entry,{})
            self.assertEqual(gl["two_color_metal"],original["two_color_metal"])
            for role in SLOTS: self.assertIn(role,gl)
            transport.append({"path":source["path"],"payload_preserved":True,
                              "gl_roles":{role:gl[role] for role in SLOTS}})


    def test_blender_frontend_receives_the_same_source_terms(self):
        for source in self.sources:
            paths = tp.material_texture_paths(self.archive, source['path'])
            rule = viewer_graph_rule(self.archive, source['path'], bound_paths=[paths[r] for r in SLOTS])
            self.assertTrue(rule['applies'])
            self.assertIn('two_color_metal', rule['ported'])
            self.assertEqual(rule['terms']['two_color_metal'], paths['two_color_metal'])
            self.assertEqual((rule['g_mode'], rule['k'], rule['normal_z']), ('grey', 0., '1-x*x-y*y'))
            self.assertEqual((rule['spec'], rule['gloss']), (paths['spec'], paths['gloss']))


if __name__ == '__main__':
    unittest.main()
