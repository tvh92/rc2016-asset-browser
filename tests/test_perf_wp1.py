"""Bulk/cached pipeline paths agree with reference copies of the former per-element code."""
import gzip
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from rc2016.common import asset_path, sha
from rc2016.formats.archive import lz4_block
from rc2016.formats.texture import (BLOCK_BYTES, UNCOMPRESSED_BPP, decode_texture, parse_header,
    ps4_deswizzle_top, read, top_mip_size)
from rc2016.formats.texture_transport import (TextureDecodeCache, _limited, _occurrence_summary,
    image_interpretation_identity, validate_reopened_texture_bindings, validate_source_transport)
from rc2016.pipeline import bulk_checks as bc


# Reference copies of the former texture_transport functions (before the cached/indexed rewrite).

def old_validate_source_transport(binding, built_entry, stream_entry, stream_bytes, dds_bytes,
                              built_asset_id=None, stream_asset_id=None):
    """Compare indexed source identity, LZ4 output, deswizzled top mip and DDS payload."""
    built_asset_id=built_asset_id or binding.get('built_asset_id')
    stream_asset_id=stream_asset_id if stream_asset_id is not None else binding.get('stream_asset_id')
    result=dict(source_path=binding['path'],built_asset_id=built_asset_id,
        stream_asset_id=stream_asset_id,stream_occurrences=_occurrence_summary(stream_entry) if stream_entry else [])
    if built_entry is None or built_asset_id is None or built_entry.get('id') not in (None,built_asset_id):
        result.update(status='failed',source_exact_top_mip=False,reason='built archive identity is missing or inconsistent')
        return result
    if asset_path(built_entry.get('path','')) != asset_path(binding['path']):
        result.update(status='failed',source_exact_top_mip=False,reason='built canonical source path mismatch')
        return result
    if stream_entry is None:
        result.update(status='limited',source_exact_top_mip=None,reason='no HD stream indexed')
        return result
    if stream_asset_id is None or stream_entry.get('id') not in (None,stream_asset_id):
        result.update(status='failed',source_exact_top_mip=False,reason='stream entry is present but binding lacks its archive identity')
        return result
    if asset_path(stream_entry.get('path','')) != asset_path(binding['path']):
        result.update(status='failed',source_exact_top_mip=False,reason='stream archive identity or canonical path mismatch')
        return result
    if not stream_bytes:
        return _limited(binding,'HD stream has no available archive bytes')
    stream_hash=sha(stream_bytes)
    if binding.get('stream_sha256') not in (None,stream_hash):
        result.update(status='failed',source_exact_top_mip=False,reason='archive stream bytes differ from the hash recorded by the decode path')
        return result
    result['stream_sha256']=stream_hash
    occurrences=stream_entry.get('occurrences',[])
    if not occurrences or not any(o.get('available') for o in occurrences):
        return _limited(binding,'HD stream archive range is unavailable',stream_hash)
    if any(not o.get('available') for o in occurrences):
        result.update(status='failed',source_exact_top_mip=False,reason='one or more indexed stream occurrences are out of archive bounds')
        return result
    sizes={o.get('size') for o in occurrences}
    if len(sizes)!=1 or next(iter(sizes))!=len(stream_bytes):
        result.update(status='failed',source_exact_top_mip=False,reason='stream occurrence bounds disagree with extracted stream length')
        return result
    try:
        built_data=read(binding['built_path'])
        info=parse_header(built_data)
    except Exception as exc:
        result.update(status='failed',source_exact_top_mip=False,reason='built texture header could not be validated: '+str(exc))
        return result
    fmt=info['format'];width=info['hd_width'];height=info['hd_height']
    result.update(format=fmt,width=width,height=height,stream_size=info['stream_size'],
        sd_mip_count=info['sd_mip_count'],hd_mip_count=info['hd_mip_count'],
        sd_mip_count_offset=info['sd_mip_count_offset'],hd_mip_count_offset=info['hd_mip_count_offset'],
        built_sha256=sha(built_data),
        stream_occurrences=_occurrence_summary(stream_entry))
    if fmt not in BLOCK_BYTES and fmt not in UNCOMPRESSED_BPP:
        return _limited(binding,f'format {fmt} is outside top-mip byte comparison support',stream_hash)
    if not width or not height or not info['stream_size']:
        return _limited(binding,'built header has no supported HD stream dimensions/size',stream_hash)
    if len(stream_bytes)==info['stream_size']:
        expanded=stream_bytes
        stream_encoding='raw'
    else:
        try:
            expanded=lz4_block(stream_bytes,info['stream_size'])
            stream_encoding='lz4_block'
        except Exception as exc:
            result.update(status='failed',source_exact_top_mip=False,reason='HD stream LZ4 decode failed: '+str(exc))
            return result
    if len(expanded)!=info['stream_size']:
        result.update(status='failed',source_exact_top_mip=False,reason='HD stream LZ4 output size disagrees with built header')
        return result
    size=top_mip_size(width,height,fmt)
    if len(expanded)<size:
        result.update(status='failed',source_exact_top_mip=False,reason='expanded HD stream is smaller than its top mip')
        return result
    try:
        expected=ps4_deswizzle_top(expanded[:size],width,height,fmt)
    except Exception as exc:
        result.update(status='failed',source_exact_top_mip=False,reason='HD top mip deswizzle failed: '+str(exc))
        return result
    if len(dds_bytes)<148 or dds_bytes[:4]!=b'DDS ' or dds_bytes[84:88]!=b'DX10':
        result.update(status='failed',source_exact_top_mip=False,reason='pipeline DDS header is not a DX10 DDS')
        return result
    dds_height,dds_width=struct.unpack_from('<II',dds_bytes,12)
    dds_format=struct.unpack_from('<I',dds_bytes,128)[0]
    actual=dds_bytes[148:148+size]
    result.update(dds_width=dds_width,dds_height=dds_height,dds_format=dds_format,
        stream_encoding=stream_encoding,expanded_stream_size=len(expanded),top_mip_bytes=size,
        source_top_mip_sha256=sha(expected),pipeline_top_mip_sha256=sha(actual))
    if (dds_width,dds_height,dds_format)!=(width,height,fmt) or len(actual)!=size or actual!=expected:
        result.update(status='failed',source_exact_top_mip=False,reason='deswizzled HD top mip differs from pipeline DDS payload/header')
    else:
        result.update(status='passed',source_exact_top_mip=True,reason='supported compressed or uncompressed top mip is byte-identical to pipeline DDS')
    return result


def old_validate_reopened_texture_bindings(expected, images):
    """Check packed bytes and Blender image interpretation after a fresh reopen."""
    report=[]
    for binding in expected:
        row=dict(source_path=binding['source_path'],status=binding.get('status','limited'),
            source_exact_top_mip=binding.get('source_exact_top_mip'),reason=binding.get('reason',''),
            built_asset_id=binding.get('built_asset_id'),stream_asset_id=binding.get('stream_asset_id'),
            dds_sha256=binding.get('dds_sha256'),format=binding.get('format'),
            dimensions=binding.get('dimensions'),source_evidence=binding.get('source_evidence',{}))
        identity=image_interpretation_identity(binding)
        row.update(image_identity=identity)
        candidates=[]
        for candidate in images:
            try:
                candidate_identity=dict(source_path=asset_path(candidate.get('source_texture_path') or ''),
                    dds_sha256=str(candidate.get('source_texture_sha256') or '').lower(),
                    colorspace=candidate.colorspace_settings.name,
                    alpha_mode=candidate.alpha_mode,
                    conversion=candidate.get('blender_conversion') or 'DDS_NATIVE_V1')
            except (ValueError,AttributeError):
                continue
            if candidate_identity==identity:candidates.append(candidate)
        if len(candidates)!=1:
            row.update(status='failed',source_exact_top_mip=False,reopened=False,
                reason=f'expected one reopened source image, found {len(candidates)}')
            report.append(row);continue
        image=candidates[0]
        try:
            packed=bytes(image.packed_file.data)
            packed_sha=hashlib.sha256(packed).hexdigest()
            expected_sha=binding.get('dds_sha256')
            image_sha=expected_sha
            image_size=binding.get('packed_dimensions',binding.get('dimensions',()))
            conversion_ok=True
            if binding.get('format')==49:
                image_sha=image.get('blender_packed_sha256')
                expected_size=binding.get('packed_dimensions',binding.get('dimensions',()))
                image_size=expected_size
                conversion_ok=(image.get('blender_conversion')=='R8G8_UNORM_DDS_TO_RGBA8_PNG_V1'
                    and image.get('packed_container')=='PNG with exact R/G and shader-reconstructed normal Z from R8G8_UNORM DDS'
                    and image.get('source_texture_sha256')==expected_sha
                    and tuple(image.get('blender_dimensions',()))==tuple(expected_size)
                    and packed.startswith(b'\x89PNG\r\n\x1a\n')
                    and image_sha is not None)
            elif binding.get('format')==61:
                image_sha=image.get('blender_packed_sha256')
                expected_size=binding.get('packed_dimensions',binding.get('dimensions',()))
                image_size=expected_size
                conversion_ok=(image.get('blender_conversion')=='R8_UNORM_DDS_TO_RGBA8_PNG_V1'
                    and image.get('packed_container')=='PNG with exact R8_UNORM scalar replicated to RGB'
                    and image.get('source_texture_sha256')==expected_sha
                    and tuple(image.get('blender_dimensions',()))==tuple(expected_size)
                    and packed.startswith(b'\x89PNG\r\n\x1a\n') and image_sha is not None)
            settings=(tuple(image.size)==tuple(image_size) and
                image.alpha_mode==identity['alpha_mode'] and
                image.colorspace_settings.name==binding.get('colorspace') and packed_sha==image_sha
                and conversion_ok)
            row.update(reopened=settings,reopened_packed_sha256=packed_sha,
                reopened_dimensions=list(image.size),reopened_colorspace=image.colorspace_settings.name,
                reopened_alpha_mode=image.alpha_mode)
            if binding.get('format')==49:
                row.update(blender_conversion=image.get('blender_conversion'),
                    blender_packed_sha256=image_sha,conversion_marker_valid=conversion_ok)
            elif binding.get('format')==61:
                row.update(blender_conversion=image.get('blender_conversion'),
                    blender_packed_sha256=image_sha,conversion_marker_valid=conversion_ok)
            if not settings:
                row.update(status='failed',source_exact_top_mip=False,reason='reopened packed DDS bytes or image settings differ from binding')
            elif binding.get('status')=='failed':
                row.update(status='failed',source_exact_top_mip=False)
            elif binding.get('status')=='passed' and binding.get('source_exact_top_mip') is True:
                row.update(status='passed',source_exact_top_mip=True)
            else:
                row.update(status='limited',source_exact_top_mip=None)
            report.append(row)
        except Exception as exc:
            row.update(status='failed',source_exact_top_mip=False,reopened=False,reason='reopened image validation failed: '+str(exc))
            report.append(row)
    return report


def old_vertex_group_adds(weights, joint_names):
    """Reference loop: one add per vertex and nonzero influence, summing a joint listed twice."""
    groups={};errors=[]
    for vertex_index,rows in enumerate(weights):
        if rows is None:
            errors.append((vertex_index,'missing'));continue
        total=sum(value for _,value in rows)
        if abs(total-1)>.005:errors.append((vertex_index,total))
        for joint,value in rows:
            if joint>=len(joint_names) or not joint_names[joint]:raise ValueError('outside')
            if value:
                vertex_groups=groups.setdefault(joint_names[joint],{})
                vertex_groups[vertex_index]=vertex_groups.get(vertex_index,0.0)+value
    return groups,errors


def apply_batches(batches):
    groups={}
    for name,value,indices in batches:
        for index in indices:groups.setdefault(name,{})[index]=value
    return groups


def quaternion_wxyz(m):
    """Unit quaternion (w,x,y,z) of a rotation matrix (Shepperd's method)."""
    t=np.trace(m)
    if t>0:
        r=np.sqrt(1+t);s=.5/r
        return np.array([.5*r,(m[2,1]-m[1,2])*s,(m[0,2]-m[2,0])*s,(m[1,0]-m[0,1])*s])
    i=int(np.argmax(np.diag(m)));j=(i+1)%3;k=(i+2)%3
    r=np.sqrt(1+m[i,i]-m[j,j]-m[k,k]);s=.5/r
    q=np.empty(4);q[0]=(m[k,j]-m[j,k])*s;q[1+i]=.5*r;q[1+j]=(m[j,i]+m[i,j])*s;q[1+k]=(m[k,i]+m[i,k])*s
    return q


def random_rotation(rng):
    q=rng.normal(size=4);q/=np.linalg.norm(q)
    return bc.quaternion_matrices(q)


def bake_channels(rest_bone, target):
    """The baker's split of an affine target into helper (T,R(U),S) and bone (rest^-1 Vt) channels."""
    u,singular,vt=np.linalg.svd(target[:3,:3])
    if np.linalg.det(u)<0:u[:,-1]*=-1;singular[-1]*=-1
    if np.linalg.det(vt)<0:vt[-1,:]*=-1;singular[-1]*=-1
    helper=np.r_[target[:3,3],quaternion_wxyz(u),singular]
    vt4=np.eye(4);vt4[:3,:3]=vt
    basis=np.linalg.inv(rest_bone)@vt4
    bone=np.r_[basis[:3,3],quaternion_wxyz(basis[:3,:3]),np.ones(3)]
    return helper,bone


def built_texture(width, height, fmt, stream_size, sd_payload=b''):
    """A minimal 1TAD built texture holding the 36-byte PS4 texture header."""
    header=bytearray(36)
    struct.pack_into('<II',header,0,len(sd_payload),stream_size)
    struct.pack_into('<4H',header,8,width,height,width if sd_payload else 0,height if sd_payload else 0)
    struct.pack_into('<HBB',header,16,1,0,1);struct.pack_into('<H',header,20,fmt)
    struct.pack_into('<4B',header,30,1,0,1,0)
    table=struct.pack('<III',0x4EDE3593,28,36)
    body=b'1TAD'+struct.pack('<I',0)+struct.pack('<I',16+12+36)+struct.pack('<HH',1,0)+table+bytes(header)
    return body+sd_payload


class FakeSettings:
    def __init__(self,name):self.name=name


class FakePacked:
    def __init__(self,data):self.data=data


class FakeImage:
    def __init__(self,props,colorspace,alpha='CHANNEL_PACKED',data=b'DDS ',size=(8,8)):
        self.props=dict(props);self.colorspace_settings=FakeSettings(colorspace);self.alpha_mode=alpha
        self.packed_file=FakePacked(data);self.size=size
    def get(self,key,default=None):return self.props.get(key,default)


class WeightBatchTests(unittest.TestCase):
    def test_batches_equal_per_vertex_replace(self):
        names=['root','spine','',None,'arm']
        cases=[[],[None],[[(0,1.0)]],[[(0,.5),(1,.5)],[(1,.25),(4,.75)],None,[(0,0.0),(1,1.0)]],
               [[(1,.3),(1,.7)],[(4,.6),(4,0.0),(0,.4)]],[[(0,.2),(1,.2)]]]
        rng=np.random.default_rng(3)
        rows=[]
        for _ in range(200):
            k=int(rng.integers(1,5));joints=rng.choice([0,1,4],size=k);w=np.round(rng.random(k),2)
            rows.append([(int(j),float(x)) for j,x in zip(joints,w/(w.sum() or 1))])
        cases.append(rows)
        for weights in cases:
            want,want_errors=old_vertex_group_adds(weights,names)
            batches,errors=bc.weight_batches(weights,names)
            self.assertEqual(apply_batches(batches),want)
            self.assertEqual(errors,want_errors)
            self.assertEqual(len({(n,v) for n,v,_ in batches}),len(batches))

    def test_unknown_joint_raises(self):
        for joint in (2,3,9):
            with self.assertRaises(ValueError):bc.weight_batches([[(joint,1.0)]],['a','b','',None])

    def test_repeated_joint_sums_to_source_weight(self):
        # Source row from hero_clank subset 010: joint 38 listed twice, 0.75 + 0.25 = 1.0.
        batches,errors=bc.weight_batches([[(38,.75),(38,.25)]],['j%d'%i for i in range(40)])
        self.assertEqual(errors,[])
        self.assertEqual(apply_batches(batches),{'j38':{0:1.0}})


class BufferHelperTests(unittest.TestCase):
    def test_per_loop_matches_loop_assignment(self):
        uv=[(0.1,0.2),(0.3,0.4),(0.5,0.6)];loops=[2,0,1,1]
        want=np.array([c for i in loops for c in uv[i]],dtype=np.float32)
        np.testing.assert_array_equal(bc.per_loop(uv,loops),want)
        self.assertEqual(len(bc.per_loop(uv,[])),0)

    def test_signed_int32_matches_wrap(self):
        words=[0,1,(1<<31)-1,1<<31,0xffffffff,0x80000001]
        self.assertEqual(bc.signed_int32(words).tolist(),[w if w<(1<<31) else w-(1<<32) for w in words])
        self.assertEqual((bc.signed_int32(words).astype(np.int64)&0xffffffff).tolist(),words)
        self.assertEqual(len(bc.signed_int32([])),0)

    def test_split_polygons_matches_polygon_vertices(self):
        loops=[0,1,2,2,1,3,4,0,5,6];starts=[0,3,7];totals=[3,4,3]
        self.assertEqual(bc.split_polygons(loops,starts,totals),[[0,1,2],[2,1,3,4],[0,5,6]])
        self.assertEqual(bc.split_polygons([],[],[]),[])

    def test_error_and_mismatch_helpers(self):
        self.assertEqual(bc.max_abs_error([],np.zeros((0,2))),0.0)
        a=np.array([[0,0,0],[1,1,1]],dtype=np.float32)
        self.assertAlmostEqual(bc.max_abs_error(a,[[0,0,1e-7],[1,1,1.5]]),0.5)
        self.assertIsNone(bc.first_mismatch([1,2,3],[1,2,3]))
        self.assertEqual(bc.first_mismatch([1,2,3],[1,5,3]),1)
        self.assertEqual(bc.first_mismatch([1,2],[1,2,3]),2)
        self.assertIsNone(bc.first_mismatch([],[]))
        self.assertEqual(bc.first_mismatch([[1,2],[3,4]],[[1,2],[3,5]]),1)


class KeyframeCompositionTests(unittest.TestCase):
    def test_constant_curve_values_match_step_lookup(self):
        frames=np.arange(1,11)
        keys=[1,4,5,9];values=[.1,.2,-.3,.4]
        want=[]
        for f in frames:
            value=values[0]
            for k,v in zip(keys,values):
                if k<=f:value=v
            want.append(value)
        np.testing.assert_array_equal(bc.constant_curve_values(keys,values,frames),want)
        np.testing.assert_array_equal(bc.constant_curve_values([3],[7.],[1,2,3,4]),[7.,7.,7.,7.])
        with self.assertRaises(ValueError):bc.constant_curve_values([],[],[1])
        with self.assertRaises(ValueError):bc.constant_curve_values([2,2],[1,2],[1])

    def test_dedup_keys_reproduce_every_sample(self):
        # add_curve keeps a key only where the value changes; CONSTANT lookup restores every frame.
        values=np.array([1,1,2,2,2,3,1,1],dtype=np.float32)
        keep=np.r_[True,values[1:]!=values[:-1]]
        out=bc.constant_curve_values(np.flatnonzero(keep)+1,values[keep],np.arange(1,len(values)+1))
        np.testing.assert_array_equal(out,values)

    def test_quaternions_are_normalised(self):
        q=np.array([2.,0,0,0]);np.testing.assert_allclose(bc.quaternion_matrices(q),np.eye(3))
        q=np.array([np.cos(.3),0,0,np.sin(.3)])*3
        r=bc.quaternion_matrices(q)
        np.testing.assert_allclose(r,[[np.cos(.6),-np.sin(.6),0],[np.sin(.6),np.cos(.6),0],[0,0,1]],atol=1e-12)

    def test_pose_composition_reproduces_baked_targets(self):
        rng=np.random.default_rng(7)
        joints=3;frames=4
        rest=[]
        for _ in range(joints):
            m=np.eye(4);m[:3,:3]=random_rotation(rng);m[:3,3]=rng.normal(size=3);rest.append(m)
        rests=[];parents=[];channels=np.zeros((frames,2*joints,10));targets=np.zeros((frames,joints,4,4))
        for j in range(joints):
            rests+=[np.eye(4),rest[j]];parents+=[-1,2*j]
        for f in range(frames):
            for j in range(joints):
                t=np.eye(4);t[:3,:3]=random_rotation(rng)@np.diag(rng.uniform(.2,3,size=3))@random_rotation(rng)
                if j==1 and f==2:t[:3,:3]=np.diag([1.,1.,0.])   # collapsed scale
                t[:3,3]=rng.normal(size=3)*10;targets[f,j]=t
                helper,bone=bake_channels(rest[j],t)
                channels[f,2*j]=helper;channels[f,2*j+1]=bone
        posed=bc.pose_matrices(np.array(rests),parents,channels)[:,1::2]
        errors=bc.matrix_errors(posed,np.repeat(np.eye(4)[None],joints,0),targets)
        self.assertLess(errors.max(),1e-9)

    def test_pose_matrices_matches_recursive_reference(self):
        rng=np.random.default_rng(11)
        rest=np.stack([np.eye(4)]*3);rest[1,:3,3]=[0,1,0];rest[2,:3,:3]=random_rotation(rng);rest[2,:3,3]=[1,2,3]
        parents=[-1,0,1];channels=rng.normal(size=(2,3,10));channels[...,7:]=rng.uniform(.5,2,size=(2,3,3))
        posed=bc.pose_matrices(rest,parents,channels)
        for f in range(2):
            pose=[]
            for b,p in enumerate(parents):
                basis=bc.channel_matrices(channels[f,b])
                local=rest[b]@basis if p<0 else np.linalg.inv(rest[p])@rest[b]@basis
                pose.append(local if p<0 else pose[p]@local)
            np.testing.assert_allclose(posed[f],np.array(pose),atol=1e-12)
        with self.assertRaises(ValueError):bc.pose_matrices(rest,[1,-1,0],channels)

    def test_matrix_errors_matches_loop(self):
        rng=np.random.default_rng(5)
        actual=rng.normal(size=(3,2,4,4));right=rng.normal(size=(2,4,4));target=rng.normal(size=(3,2,4,4))
        want=[max(abs(a-b) for j in range(2) for ra,rb in zip(actual[f,j]@right[j],target[f,j]) for a,b in zip(ra,rb)) for f in range(3)]
        np.testing.assert_allclose(bc.matrix_errors(actual,right,target),want)
        self.assertEqual(len(bc.matrix_errors(np.zeros((0,2,4,4)),right,np.zeros((0,2,4,4)))),0)

    def test_sample_frames(self):
        self.assertEqual(bc.sample_frames(1),[1])
        self.assertEqual(bc.sample_frames(2),[1,2])
        self.assertEqual(bc.sample_frames(9),[1,5,9])
        motion=np.zeros((9,1,4,4));motion[6,0,0,3]=5;motion[8,0,0,3]=1
        self.assertEqual(bc.sample_frames(9,motion),[1,5,7,9])
        self.assertEqual(bc.sample_frames(9,extra=[3,0,12]),[1,3,5,9])
        with self.assertRaises(ValueError):bc.sample_frames(0)

    def test_cached_frames_match(self):
        frames=[[{'hash':'0x1','name':'root','translation':[0.1,-0.0,3],'rotation_xyzw':None}]]
        clip={'name':'Idle','frames':{'note':',"frames":[]}'},'metadata':{}}
        payload=json.dumps(dict(clip=clip,frames=frames),separators=(',',':'),allow_nan=False).encode()
        self.assertTrue(bc.cached_frames_match(gzip.decompress(gzip.compress(payload)),frames))
        self.assertEqual(json.loads(payload)['frames'],frames)
        changed=json.loads(json.dumps(frames));changed[0][0]['translation'][0]=0.2
        self.assertFalse(bc.cached_frames_match(payload,changed))
        self.assertFalse(bc.cached_frames_match(payload,frames[:0]))
        self.assertTrue(bc.cached_frames_match(json.dumps(dict(clip={},frames=[]),separators=(',',':')).encode(),[]))


class TextureTransportTests(unittest.TestCase):
    def fixture(self,root,fmt=28,width=8,height=8,corrupt_dds=False):
        size=top_mip_size(width,height,fmt)
        rng=np.random.default_rng(fmt)
        stream=rng.integers(0,256,size=size,dtype=np.uint8).tobytes()
        built=built_texture(width,height,fmt,len(stream))
        built_path=Path(root)/'t.built';built_path.write_bytes(built)
        dds_path=Path(root)/'t.dds'
        report=decode_texture(built_path,stream,dds_path)
        dds=dds_path.read_bytes()
        if corrupt_dds:dds=dds[:-1]+bytes([dds[-1]^1])
        binding=dict(path='textures/a_c.texture',built_asset_id='b1',stream_asset_id='s1',built_path=str(built_path),stream_sha256=sha(stream))
        built_entry=dict(id='b1',path='textures/a_c.texture')
        stream_entry=dict(id='s1',path='textures/a_c.texture',occurrences=[dict(archive='x',offset=0,size=len(stream),available=True)])
        return binding,built_entry,stream_entry,stream,dds,report

    def test_transport_matches_reference_with_and_without_cache(self):
        for kwargs in (dict(),dict(corrupt_dds=True),dict(fmt=61),dict(fmt=49,width=16,height=8)):
            with tempfile.TemporaryDirectory() as root:
                binding,built_entry,stream_entry,stream,dds,_=self.fixture(root,**kwargs)
                want=old_validate_source_transport(binding,built_entry,stream_entry,stream,dds)
                self.assertEqual(want['status'],'failed' if kwargs.get('corrupt_dds') else 'passed')
                self.assertEqual(json.dumps(validate_source_transport(binding,built_entry,stream_entry,stream,dds)),json.dumps(want))
                cache=TextureDecodeCache(Path(root)/'cache')
                for _ in range(2):
                    got=validate_source_transport(binding,built_entry,stream_entry,stream,dds,transport_cache=cache)
                    self.assertEqual(json.dumps(got),json.dumps(want))
                self.assertTrue(any((Path(root)/'cache').rglob('*.json')))

    def test_transport_failure_paths_match_reference(self):
        with tempfile.TemporaryDirectory() as root:
            binding,built_entry,stream_entry,stream,dds,_=self.fixture(root)
            cache=TextureDecodeCache(Path(root)/'cache')
            variants=[
                (binding,built_entry,stream_entry,stream,dds[:100]),
                (binding,built_entry,None,stream,dds),
                (dict(binding,stream_sha256='0'*64),built_entry,stream_entry,stream,dds),
                (dict(binding,stream_sha256=None),built_entry,stream_entry,stream+b'xx',dds),
                (dict(binding,built_path=str(Path(root)/'missing')),built_entry,stream_entry,stream,dds),
                (binding,dict(built_entry,path='other.texture'),stream_entry,stream,dds),
                (binding,built_entry,stream_entry,b'',dds),
            ]
            bad_header=Path(root)/'bad.built';bad_header.write_bytes(b'not a container')
            variants.append((dict(binding,built_path=str(bad_header)),built_entry,stream_entry,stream,dds))
            no_stream=Path(root)/'nostream.built';no_stream.write_bytes(built_texture(8,8,28,0))
            variants.append((dict(binding,built_path=str(no_stream)),built_entry,stream_entry,stream,dds))
            for args in variants:
                want=old_validate_source_transport(*args)
                for _ in range(2):
                    got=validate_source_transport(*args,transport_cache=cache)
                    self.assertEqual(json.dumps(got),json.dumps(want))

    def test_decode_cache_serves_identical_report_and_bytes(self):
        from rc2016.formats.materials import _decode_texture_cached
        with tempfile.TemporaryDirectory() as root:
            _binding,_b,_s,stream,_dds,_=self.fixture(root)
            built_path=Path(root)/'t.built';built_sha=sha(built_path.read_bytes())
            cache=TextureDecodeCache(Path(root)/'cache')
            want=decode_texture(built_path,stream,Path(root)/'want.dds')
            first=_decode_texture_cached(cache,built_path,built_sha,stream,sha(stream),Path(root)/'one'/'a.dds')
            second=_decode_texture_cached(cache,built_path,built_sha,stream,sha(stream),Path(root)/'two'/'b.dds')
            for got,name in ((first,'one/a.dds'),(second,'two/b.dds')):
                self.assertEqual(list(got),list(want))
                self.assertEqual({k:v for k,v in got.items() if k!='dds_path'},{k:v for k,v in want.items() if k!='dds_path'})
                self.assertEqual(got['dds_path'],str(Path(root)/name))
                self.assertEqual((Path(root)/name).read_bytes(),(Path(root)/'want.dds').read_bytes())
            # A tampered cache entry is ignored and recomputed.
            for path in (Path(root)/'cache').rglob('*.dds'):path.write_bytes(b'corrupt')
            third=_decode_texture_cached(cache,built_path,built_sha,stream,sha(stream),Path(root)/'three.dds')
            self.assertEqual((Path(root)/'three.dds').read_bytes(),(Path(root)/'want.dds').read_bytes())
            self.assertEqual(third['dds_sha256'],want['dds_sha256'])
            # A wrong built hash never serves or stores a cache entry under that hash.
            other=_decode_texture_cached(cache,built_path,'0'*64,stream,sha(stream),Path(root)/'four.dds')
            self.assertEqual(other['dds_sha256'],want['dds_sha256'])

    def test_cache_key_namespaces(self):
        cache=TextureDecodeCache('unused')
        self.assertNotEqual(cache.key('dds','a',None),cache.key('dds','a','b'))
        self.assertNotEqual(cache.key('dds','a','b'),cache.key('transport','a','b'))
        self.assertIsNone(cache.key('dds',b'not text'))

    def test_reopened_bindings_match_reference(self):
        data=b'DDS payload';digest=hashlib.sha256(data).hexdigest()
        images=[
            FakeImage(dict(source_texture_path='Textures/A_c.texture',source_texture_sha256=digest.upper()),'sRGB',data=data),
            FakeImage(dict(source_texture_path='textures/b_n.texture',source_texture_sha256=digest),'Non-Color',data=data),
            FakeImage(dict(source_texture_path='textures/b_n.texture',source_texture_sha256=digest),'Non-Color',data=data),
            FakeImage(dict(source_texture_path='textures/c_g.texture',source_texture_sha256=digest,blender_conversion='R8G8_UNORM_DDS_TO_RGBA8_PNG_V1'),'Non-Color',data=data),
            FakeImage(dict(source_texture_path='',source_texture_sha256=''),'sRGB'),
        ]
        expected=[
            dict(source_path='textures/a_c.texture',dds_sha256=digest,colorspace='sRGB',status='passed',source_exact_top_mip=True,dimensions=[8,8]),
            dict(source_path='textures/b_n.texture',dds_sha256=digest,colorspace='Non-Color'),
            dict(source_path='textures/missing.texture',dds_sha256=digest,colorspace='sRGB'),
            dict(source_path='textures/a_c.texture',dds_sha256=digest,colorspace='Non-Color'),
            dict(source_path='textures/c_g.texture',dds_sha256=digest,colorspace='Non-Color',format=49,dimensions=[8,8]),
            dict(source_path='textures/a_c.texture',dds_sha256=digest,colorspace='sRGB',status='failed'),
        ]
        new=validate_reopened_texture_bindings(expected,images)
        self.assertEqual(new,old_validate_reopened_texture_bindings(expected,images))
        self.assertEqual([row['status'] for row in new][:3],['passed','failed','failed'])
        self.assertEqual(validate_reopened_texture_bindings([],images),[])
        self.assertEqual(validate_reopened_texture_bindings(expected,[]),old_validate_reopened_texture_bindings(expected,[]))


class FakeArchive:
    def __init__(self,root,entries,mapping,data):
        self.root=Path(root);self.entries=entries;self.mapping=mapping;self.data=data;self.reads=[]
        self.layout_identity='layout';self.metadata={'layout.csv':'m'}
    def bytes(self,key):
        self.reads.append(key);value=self.data[key];return value,sha(value)
    def decode(self,key):
        self.bytes(key);return Path(self.root)/key


class FakeIndex:
    def __init__(self,archive):
        self.archive=archive;self.graph={};self.errors=[];self.scans=0
    def actor_index(self):
        self.scans+=1
        for key,entry in self.archive.entries.items():
            if entry['type']=='actor':self.graph[key]=[dict(path='a.model',byte_offset=4,resolved_ids=['m'])]
        self.errors.append(dict(asset_id='act2',path='b.actor',error='bad'))


class RunCacheTests(unittest.TestCase):
    def setUp(self):
        from rc2016.pipeline import run
        self.run=run

    def test_verified_cache_hit_without_previews(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);final=root/'m.blend';final.write_bytes(b'blend')
            validation=root/'validation.json';validation.write_text(json.dumps(dict(status='passed')))
            old=dict(fingerprint='f',final_sha256=sha(b'blend'),validation_sha256=sha(validation.read_bytes()))
            self.assertTrue(self.run.verified_cache_hit(old,'f',final,validation))
            png=root/'p.png';png.write_bytes(b'png')
            validation.write_text(json.dumps(dict(status='passed',previews=[dict(path=str(png),sha256=sha(b'png'))])))
            old['validation_sha256']=sha(validation.read_bytes())
            self.assertTrue(self.run.verified_cache_hit(old,'f',final,validation))
            png.write_bytes(b'changed')
            self.assertFalse(self.run.verified_cache_hit(old,'f',final,validation))

    def test_promotion_recheck_skips_only_unchanged_sources_hashed_this_run(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root)/'layout.csv').write_text('x');(Path(root)/'a0s0').write_bytes(b'seg')
            entries={'k1':dict(type='model'),'k2':dict(type='texture'),'loose':dict(type='fbx',loose_path='x.fbx')}
            data={'k1':b'one','k2':b'two','loose':b'fbx'}
            archive=FakeArchive(root,entries,{},data)
            stamps=self.run.archive_file_stamps(root)
            self.assertEqual(set(stamps),{'layout.csv','a0s0'})
            verified=self.run.record_verified_sources(archive)
            archive.mapping={'k1':dict(source_sha256=archive.bytes('k1')[1])}
            self.assertTrue(self.run.sources_unchanged_since_start(archive,['k1','k2'],stamps,verified))
            archive.mapping['k2']=dict(source_sha256=sha(b'two'))      # mapped, never read this run
            self.assertFalse(self.run.sources_unchanged_since_start(archive,['k1','k2'],stamps,verified))
            archive.bytes('k2')
            self.assertTrue(self.run.sources_unchanged_since_start(archive,['k1','k2'],stamps,verified))
            archive.mapping['loose']=dict(source_sha256=archive.bytes('loose')[1])
            self.assertFalse(self.run.sources_unchanged_since_start(archive,['loose'],stamps,verified))
            self.assertFalse(self.run.sources_unchanged_since_start(archive,['k1'],None,verified))
            (Path(root)/'a0s0').write_bytes(b'segment changed')
            self.assertFalse(self.run.sources_unchanged_since_start(archive,['k1'],stamps,verified))

    def test_actor_index_reused_while_stamp_matches(self):
        with tempfile.TemporaryDirectory() as root:
            entries={'act1':dict(type='actor'),'act2':dict(type='actor'),'m':dict(type='model')}
            archive=FakeArchive(root,entries,{},{})
            events=[];log=lambda event,**values:events.append(event)
            first=FakeIndex(archive)
            self.assertFalse(self.run.load_or_build_actor_index(first,archive,root,{'a0s0':[1,2]},log))
            second=FakeIndex(archive)
            self.assertTrue(self.run.load_or_build_actor_index(second,archive,root,{'a0s0':[1,2]},log))
            self.assertEqual((first.scans,second.scans),(1,0))
            self.assertEqual(second.graph,first.graph);self.assertEqual(second.errors,first.errors)
            self.assertIn('actor_reference_index_reused',events)
            third=FakeIndex(archive)
            self.assertFalse(self.run.load_or_build_actor_index(third,archive,root,{'a0s0':[1,3]},log))
            self.assertEqual(third.scans,1)
            self.assertEqual(len(list((Path(root)/'cache'/'actor_index').glob('*.json'))),1)

    def test_lz4_warning_names_the_interpreter(self):
        from rc2016.formats import archive as archive_module
        warning=self.run.lz4_warning()
        if archive_module._lz4_c is None:self.assertIn('pip install lz4',warning)
        else:self.assertIsNone(warning)

    def test_no_preview_plumbing_left(self):
        from rc2016.app.batch import PipelineBatchRunner
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            runner=PipelineBatchRunner(['hero'],root/'src',root/'out',root/'py.exe',root/'blender.exe')
            self.assertNotIn('--skip-previews',runner.command())
        source=(Path(self.run.__file__)).read_text(encoding='utf-8')
        self.assertNotIn('skip-previews',source);self.assertNotIn("validation['previews']",source)


if __name__=='__main__':
    unittest.main()
