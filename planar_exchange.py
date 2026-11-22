"""Exchange linear XY triangles and named exterior edge sets (FieldCraft/3, SI).

Place the external adapter mesh_exchange.py on PYTHONPATH for its verified archive reader.
"""
import io
import struct

import numpy as np


def _records(raw):
    stream = io.BytesIO(raw)
    def take(length):
        value = stream.read(length)
        if len(value) != length:
            raise ValueError('Truncated FieldCraft mesh')
        return value
    def read(fmt):
        layout = struct.Struct('<'+fmt)
        return layout.unpack(take(layout.size))
    def name():
        return take(read('H')[0]).decode('utf-8')
    if take(8) != b'MCMESH02':
        raise ValueError('Expected mesh encoding MCMESH02')
    nn, ne, ng, nt, nv = read('5Q')
    if not (3 <= nn and 1 <= ne and nt <= ne and nv <= nn and 28*nn+17*ne+4*ng+10*nt+8*nv <= len(raw)-48):
        raise ValueError('Invalid FieldCraft mesh counts')
    nodes = np.frombuffer(take(28*nn),np.dtype([('label','<u4'),('xyz','<f8',3)]))
    cells = np.frombuffer(take(17*ne),np.dtype([('label','<u4'),('kind','u1'),('nodes','<u4',3)]))
    if np.any(cells['kind'] != 3):
        raise ValueError('Planar exchange requires exclusively Tri3 elements')
    for ids in (nodes['label'],cells['label']):
        if not ids[0] or np.any(ids[1:]<=ids[:-1]):
            raise ValueError('Element and node labels must be positive and sorted')
    points = nodes['xyz']
    if not np.isfinite(points).all() or np.any(points[:,2] != 0):
        raise ValueError('FieldCraft planar exchange requires finite XY geometry at z=0')
    tri = np.searchsorted(nodes['label'],cells['nodes'])
    if np.any(tri>=nn) or not np.array_equal(nodes['label'][tri],cells['nodes']):
        raise ValueError('Triangle uses an unknown node')
    if len(np.unique(tri)) != nn:
        raise ValueError('Mesh includes nodes outside the planar elements')
    corners = points[tri, :2]
    a,b = corners[:,1]-corners[:,0], corners[:,2]-corners[:,0]
    twice_area = a[:,0]*b[:,1] - a[:,1]*b[:,0]
    if not np.all(np.isfinite(twice_area) & (twice_area>0)):
        raise ValueError('Expected counter-clockwise nondegenerate XY triangles')
    entities = []
    for _ in range(ng):
        kind, = read('B')
        if kind > 3:
            raise ValueError('Invalid geometry type in mesh')
        body = name()
        entities.append((body,kind,name() if kind else ''))
    cell_labels = set(cells['label'].tolist())
    node_labels = set(nodes['label'].tolist())
    regions, edges = {}, {}
    def entity(idx):
        if idx >= ng:
            raise ValueError('Invalid geometry tag index')
        return entities[idx]
    prior = 0
    for _ in range(nt):
        label, area = read('II')
        if label not in cell_labels or label <= prior:
            raise ValueError('Invalid tagged element label')
        prior = label
        if area != 0xffffffff:
            region=entity(area)
            if region[1] not in (0,1):
                raise ValueError('Planar element has an invalid region type')
            regions[label] = region
        for topology in ('face','edge'):
            tags, = read('B')
            for _ in range(tags):
                number, index = read('BI')
                tag = entity(index)
                if not 1 <= number <= (3 if topology=='edge' else 2):
                    raise ValueError('Invalid triangle edge or face number')
                if topology == 'edge':
                    if tag[1] != 2 or (label,number) in edges:
                        raise ValueError('Invalid or repeated planar edge tag')
                    edges[label,number] = tag
    prior = 0
    for _ in range(nv):
        label,index = read('II')
        if label not in node_labels or label <= prior:
            raise ValueError('Invalid tagged node label')
        prior = label
        entity(index)
    if stream.read(1):
        raise ValueError('Trailing bytes in mesh')
    return nodes,cells['label'],tri,regions,edges


def _groups(keys, tags, sets, names, boundary):
    if not names or len(names) != len(set(names)):
        raise ValueError('Select distinct nonempty group names')
    indices = {key:i for i,key in enumerate(keys)}
    group_ids = np.zeros(len(keys),dtype=np.int32)
    part = 'edge' if boundary else 'face'
    for group,name in enumerate(names,1):
        if name not in sets:
            raise ValueError(f'Model set not found: {name}')
        definition = sets[name]
        members = {(item['element'],item['edge']) for item in definition.get('edges',[])} if boundary else set(definition.get('elements',[]))
        for ref in definition.get('geometry',[]):
            if set(ref) not in ({'body'},{'body',part}):
                raise ValueError(f'Unsupported geometry reference for {name}: {ref}')
            for key,(body,_,tag_name) in tags.items():
                if ref['body'] != body:
                    continue
                if part not in ref or tag_name==ref[part] or (part=='face' and tag_name.startswith(ref[part]+'#')):
                    members.add(key)
        if not members or any(m not in indices for m in members):
            raise ValueError(f'Set {name} is empty or contains non-exterior edges / unknown elements')
        selected = [indices[m] for m in members]
        if np.any(group_ids[selected]):
            raise ValueError(f'Set {name} overlaps another selected set')
        group_ids[selected] = group
    if not np.all(group_ids):
        raise ValueError('Selected sets do not cover every material region or every exterior edge')
    return group_ids


def export_mesh(source, destination, boundaries, materials):
    from mesh_exchange import load_parts
    model,raw,digest = load_parts(source)
    nodes, ids, tri, regions, tagged_edges = _records(raw)
    oriented = tri[:,[[0,1],[1,2],[2,0]]].reshape(-1,2)
    _, first, incidence, counts = np.unique(np.sort(oriented,axis=1),axis=0,return_index=True,return_inverse=True,return_counts=True)
    orientation=np.where(oriented[:,0]<oriented[:,1],1,-1)
    if np.any(np.bincount(incidence,weights=orientation)[counts==2] != 0):
        raise ValueError('Incident triangles disagree on edge orientation')
    if np.any(counts>2):
        raise ValueError('Non-manifold planar mesh')
    ext = np.sort(first[counts==1])
    edge_keys = list(zip(ids[ext//3].tolist(),(1+ext%3).tolist()))
    sets = model.get('sets',{})
    boundary_ids = _groups(edge_keys,tagged_edges,sets,boundaries,True)
    material_ids = _groups(ids.tolist(),regions,sets,materials,False)
    np.savez_compressed(destination,mesh_sha256=np.array(digest),points_m=nodes['xyz'],node_ids=nodes['label'],
                        material_names=np.array(materials),material_ids=material_ids,triangles=tri,element_ids=ids,
                        boundary_names=np.array(boundaries),boundary_ids=boundary_ids,segments=oriented[ext])
    return len(nodes),len(tri),dict(zip(boundaries,np.bincount(boundary_ids)[1:].tolist()))


def ngsolve_mesh(data):
    import ngsolve as ng
    import netgen.meshing as nm
    for key,width in [('triangles',3),('segments',2)]:
        labels=np.asarray(data[key]);n=len(data['node_ids'])
        if (labels.ndim!=2 or labels.shape[1]!=width or not labels.size or labels.dtype.kind not in 'iu' or
                labels.min()<0 or labels.max()>=n or n>np.iinfo(np.int32).max):
            raise ValueError('Invalid planar connectivity: '+key)
    if data['points_m'].shape!=(n,3) or not np.isfinite(data['points_m']).all():
        raise ValueError('Invalid planar coordinates')
    mesh = nm.Mesh(dim=2)
    mesh.AddPoints(np.ascontiguousarray(data['points_m'],dtype=np.float64))
    for n, name in enumerate(data['material_names'],1):
        index = mesh.AddRegion(str(name),dim=2)
        triangles = np.ascontiguousarray(data['triangles'][data['material_ids']==n],dtype=np.int32)
        mesh.AddElements(2,index,triangles,base=0)
    for n,name in enumerate(data['boundary_names'],1):
        index = mesh.AddRegion(str(name),dim=1)
        segments = data['segments'][data['boundary_ids']==n]
        for a,b in segments:
            mesh.Add(nm.Element1D([nm.PointId(int(a)+1),nm.PointId(int(b)+1)],index=index))
    mesh.UpdateTopology()
    result = ng.Mesh(mesh)
    if result.ne != len(data['triangles']) or result.nv != len(data['node_ids']):
        raise ValueError('Unexpected change in mesh cardinality on import')
    if result.GetMaterials() != tuple(data['material_names']) or result.GetBoundaries() != tuple(data['boundary_names']):
        raise ValueError('Solver lost named regions on import')
    return result
